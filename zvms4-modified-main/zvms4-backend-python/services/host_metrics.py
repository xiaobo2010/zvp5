"""Host resource metrics read straight from ``/proc`` (design §2.3 / §4.1).

No third-party dependency (no ``psutil``): every value is parsed from the kernel
pseudo-files, plus ``shutil.disk_usage`` (stdlib ``statvfs``) for the root
filesystem.  The deployment is a single ``uvicorn`` worker on a Linux host, so
"read the process' own ``/proc``" *is* "read the host" — there is no container
boundary to aggregate across.

Rate semantics (frozen series names in ``docs/DB_PERF_MAINTENANCE_DESIGN.md``
§4.1):

* CPU percentages, network throughput and disk throughput are **derived from the
  previous reading** kept in this module.  The first reading in a process has no
  previous sample, so those series are *omitted* instead of being written with a
  fabricated zero.  The same rule applies after a restart.
* ``*_kbps`` means kibibytes per second (``bytes / 1024 / seconds``); the frontend
  renders the unit.
* ``diskio`` tracks the single device backing ``/`` (partition suffix stripped,
  e.g. ``/dev/vda1`` -> ``vda``); pseudo devices (``loop*``, ``ram*``, ``zram*``,
  ``fd*``, ``sr*``) are never selected.  ``net`` sums the host's **real egress**
  interfaces only (loopback and container/tunnel devices are excluded, see
  :func:`is_egress_interface`) — a veth pair would otherwise count the same
  bytes twice.

Gauge series (memory, load, disk usage, process RSS/uptime) need no previous
sample and are always emitted.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
from typing import Any

_logger = logging.getLogger(__name__)

# Tests point this at a fixture tree; production always reads the real /proc.
_PROC_ROOT = "/proc"

# Replaced in tests to make rate assertions deterministic.
_monotonic = time.monotonic

# Devices that are not the data disk.  ``dm-*`` and ``md*`` are kept: they can be
# the real block device behind ``/`` on a mapped/RAID setup.
_SKIPPED_DEVICE_PREFIXES = ("loop", "ram", "zram", "fd", "sr", "nbd", "mtdblock")

# Network interfaces that are *not* the host's own egress path. Summing them
# double counts traffic: a container's ``veth`` pair sees every byte twice, and
# a bridge/tunnel sees it once more on top of the physical NIC.
#
#   lo        loopback (uphill — never leaves the host)
#   docker*   docker bridge (docker0, docker_gwbridge)
#   veth*     container side of a veth pair (mirror of the real NIC's traffic)
#   br*       software bridges (br0, br-<id>; no NIC naming scheme uses br*)
#   virbr*    libvirt bridges
#   tun*/tap* tunnels and tap devices (VPN / Clash TUN)
#   zram*     compressed swap (not network, but same pseudo-device family)
#   wg*/tailscale*/utun*  VPN tunnels
#   dummy*/ifb*  synthetic qdisc devices
#
# Everything else (eth0, enp3s0, ens3, wlan0, bond0, ...) is kept.
_LOOPBACK_INTERFACE = "lo"
_SKIPPED_INTERFACE_PREFIXES = (
    "docker", "veth", "br", "virbr", "tun", "tap", "zram", "wg", "tailscale",
    "utun", "dummy", "ifb",
)


def is_egress_interface(name: str) -> bool:
    """True when ``name`` is a real (non-virtual) network interface."""
    interface = (name or "").strip()
    if not interface or interface == _LOOPBACK_INTERFACE:
        return False
    return not interface.startswith(_SKIPPED_INTERFACE_PREFIXES)

# sda1 -> sda, vda1 -> vda, nvme0n1p1 -> nvme0n1, mmcblk0p1 -> mmcblk0.
_PARTITION_SUFFIX = re.compile(r"^(?:(nvme\d+n\d+|mmcblk\d+|md\d+|dm-\d+)p\d+|([a-z]+)\d+)$")

_previous_lock = threading.Lock()
_previous: dict[str, Any] | None = None


def _read_lines(relative: str) -> list[str]:
    path = os.path.join(_PROC_ROOT, relative)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().splitlines()
    except OSError:
        _logger.debug("host metrics: cannot read %s", path)
        return []


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


# --------------------------------------------------------------------------- #
# raw readings
# --------------------------------------------------------------------------- #


def _cpu_counters() -> dict[str, int] | None:
    """Aggregate ``cpu`` line of ``/proc/stat``: user nice system idle iowait ..."""
    for line in _read_lines("stat"):
        if not line.startswith("cpu "):
            continue
        parts = line.split()[1:9]
        values = [_to_int(part) for part in parts]
        if len(values) < 8 or any(value is None for value in values):
            return None
        user, nice, system, idle, iowait, irq, softirq, steal = values
        return {
            "user": user + nice,
            "system": system + irq + softirq,
            "idle": idle,
            "iowait": iowait,
            "steal": steal,
            "total": user + nice + system + idle + iowait + irq + softirq + steal,
        }
    return None


def _memory() -> dict[str, float | None]:
    values: dict[str, int] = {}
    for line in _read_lines("meminfo"):
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        amount = _to_int(rest.strip().split()[0]) if rest.strip().split() else None
        if amount is not None:
            values[key.strip()] = amount
    total_kb = values.get("MemTotal")
    available_kb = values.get("MemAvailable")
    total_mb = None if total_kb is None else total_kb / 1024
    available_mb = None if available_kb is None else available_kb / 1024
    used_mb = (
        None if total_mb is None or available_mb is None else total_mb - available_mb
    )
    return {"total_mb": total_mb, "used_mb": used_mb, "available_mb": available_mb}


def _load_average() -> dict[str, float | None]:
    for line in _read_lines("loadavg"):
        parts = line.split()
        if len(parts) >= 3:
            return {
                "1m": _to_float(parts[0]),
                "5m": _to_float(parts[1]),
                "15m": _to_float(parts[2]),
            }
    return {"1m": None, "5m": None, "15m": None}


def _net_counters() -> tuple[int, int]:
    """``(rx_bytes, tx_bytes)`` summed over the host's real egress interfaces.

    Loopback and virtual/container/tunnel interfaces are excluded (see
    :data:`_SKIPPED_INTERFACE_PREFIXES`): counting them would multiply the same
    traffic through the veth/bridge stack and report Docker or Clash throughput
    as host throughput.
    """
    rx = tx = 0
    for line in _read_lines("net/dev"):
        if ":" not in line:
            continue
        interface, _, rest = line.partition(":")
        if not is_egress_interface(interface):
            continue
        fields = rest.split()
        if len(fields) < 9:
            continue
        received = _to_int(fields[0])
        transmitted = _to_int(fields[8])
        if received is not None:
            rx += received
        if transmitted is not None:
            tx += transmitted
    return rx, tx


def base_disk_name(device: str) -> str:
    """``/dev/vda1`` -> ``vda``; partitions collapse onto their parent device."""
    name = os.path.basename(device.strip())
    match = _PARTITION_SUFFIX.match(name)
    if match:
        return match.group(1) or match.group(2)
    return name


def _mount_device(mount_point: str = "/") -> str | None:
    for path in (os.path.join(_PROC_ROOT, "mounts"), "/etc/mtab"):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) >= 2 and fields[1] == mount_point and fields[0].startswith("/dev/"):
                return base_disk_name(fields[0])
    return None


def _disk_counters() -> tuple[str | None, int, int]:
    """``(device, sectors_read, sectors_written)`` for the main data disk."""
    wanted = _mount_device()
    records: list[tuple[str, int, int]] = []
    for line in _read_lines("diskstats"):
        fields = line.split()
        if len(fields) < 10:
            continue
        name = fields[2]
        if name.startswith(_SKIPPED_DEVICE_PREFIXES):
            continue
        sectors_read = _to_int(fields[5])
        sectors_written = _to_int(fields[9])
        if sectors_read is None or sectors_written is None:
            continue
        records.append((name, sectors_read, sectors_written))
    for name, read, written in records:
        if wanted is not None and name == wanted:
            return name, read, written
    # No /proc/mounts entry (containerised root, overlay fs, ...): fall back to the
    # first physical device in diskstats order rather than reporting nothing.
    if records:
        return records[0]
    return None, 0, 0


def _disk_usage(mount: str = "/") -> dict[str, Any]:
    """Root filesystem usage via ``statvfs`` (same numbers ``df /`` prints)."""
    try:
        usage = shutil.disk_usage(mount)
    except OSError:
        _logger.debug("host metrics: cannot stat filesystem %s", mount)
        return {"mount": mount, "used_pct": None, "free_gb": None}
    total = usage.total
    return {
        "mount": mount,
        "used_pct": _round(usage.used / total * 100) if total else None,
        "free_gb": _round(usage.free / 1024**3),
    }


def _proc_rss_mb() -> float | None:
    for line in _read_lines("self/status"):
        if line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) >= 2:
                kb = _to_float(parts[1])
                return None if kb is None else kb / 1024
    return None


def _proc_uptime_hours() -> float | None:
    """Process age: ``/proc/uptime`` minus this process' start time."""
    boot_seconds = None
    for line in _read_lines("uptime"):
        parts = line.split()
        if parts:
            boot_seconds = _to_float(parts[0])
        break
    if boot_seconds is None:
        return None

    stat = None
    for line in _read_lines("self/stat"):
        stat = line
        break
    if not stat:
        return None
    # ``comm`` may contain spaces and parentheses; everything after the last ')'
    # is the fixed-width remainder where field 22 (starttime) is index 19.
    _, _, remainder = stat.rpartition(")")
    fields = remainder.split()
    if len(fields) < 20:
        return None
    start_ticks = _to_int(fields[19])
    if start_ticks is None:
        return None
    try:
        ticks_per_second = os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError, AttributeError):  # pragma: no cover - non-POSIX
        return None
    started_at = start_ticks / ticks_per_second
    return max(0.0, (boot_seconds - started_at) / 3600)


# --------------------------------------------------------------------------- #
# derived values
# --------------------------------------------------------------------------- #


def _cpu_percentages(
    previous: dict[str, int] | None, current: dict[str, int]
) -> dict[str, float | None]:
    empty = {"user_pct": None, "system_pct": None, "idle_pct": None, "iowait_pct": None}
    if previous is None:
        return empty
    total = current["total"] - previous["total"]
    if total <= 0:
        return empty
    return {
        "user_pct": _round((current["user"] - previous["user"]) / total * 100),
        "system_pct": _round((current["system"] - previous["system"]) / total * 100),
        "idle_pct": _round((current["idle"] - previous["idle"]) / total * 100),
        "iowait_pct": _round((current["iowait"] - previous["iowait"]) / total * 100),
    }


def _rate_kbps(previous: int | None, current: int, elapsed: float | None) -> float | None:
    if previous is None or elapsed is None or elapsed <= 0:
        return None
    delta = current - previous
    if delta < 0:  # counter reset (interface/device recreated): no sample
        return None
    return _round(delta / 1024 / elapsed)


def collect_host_metrics() -> dict[str, Any]:
    """One reading of the host.

    Returns ``{"host": {...}, "series": {...}}`` — ``host`` is the nested shape the
    realtime endpoint returns (§5.1) and ``series`` is the flat, storable mapping
    of §4.1 (rate keys absent on the first reading after a restart).
    """
    global _previous

    cpu = _cpu_counters()
    memory = _memory()
    load = _load_average()
    rx, tx = _net_counters()
    device, sectors_read, sectors_written = _disk_counters()
    disk = _disk_usage()
    rss_mb = _proc_rss_mb()
    uptime_hours = _proc_uptime_hours()

    no_cpu = {"user_pct": None, "system_pct": None, "idle_pct": None, "iowait_pct": None}
    no_rates = {"read_kbps": None, "write_kbps": None}
    no_net = {"rx_kbps": None, "tx_kbps": None}

    with _previous_lock:
        now = _monotonic()
        previous = _previous
        elapsed = None if previous is None else now - previous["at"]
        previous_cpu = None if previous is None else previous["cpu"]
        previous_net = None if previous is None else previous["net"]
        previous_disk = None if previous is None else previous["disk"]

        cpu_percent = (
            _cpu_percentages(previous_cpu, cpu)
            if cpu is not None and previous_cpu is not None
            else no_cpu
        )
        net_rates = (
            no_net
            if previous_net is None
            else {
                "rx_kbps": _rate_kbps(previous_net[0], rx, elapsed),
                "tx_kbps": _rate_kbps(previous_net[1], tx, elapsed),
            }
        )
        disk_rates = (
            no_rates
            if previous_disk is None or device is None or previous_disk[0] != device
            else {
                "read_kbps": _rate_kbps(previous_disk[1], sectors_read * 512, elapsed),
                "write_kbps": _rate_kbps(previous_disk[2], sectors_written * 512, elapsed),
            }
        )
        _previous = {
            "at": now,
            "cpu": cpu,
            "net": (rx, tx),
            # Stored in bytes so ``sectors * 512`` is applied exactly once.
            "disk": (device, sectors_read * 512, sectors_written * 512),
        }

    host = {
        "cpu": cpu_percent,
        "mem": {
            "total_mb": _round(memory["total_mb"]),
            "used_mb": _round(memory["used_mb"]),
            "available_mb": _round(memory["available_mb"]),
        },
        "load": {
            "1m": _round(load["1m"], 3),
            "5m": _round(load["5m"], 3),
            "15m": _round(load["15m"], 3),
        },
        "disk": disk,
        "net": net_rates,
        "diskio": disk_rates,
        "proc": {"rss_mb": _round(rss_mb), "uptime_hours": _round(uptime_hours, 3)},
    }

    series: dict[str, float] = {}

    def emit(key: str, value: float | None) -> None:
        if value is not None:
            series[key] = value

    emit("host.cpu.user_pct", host["cpu"]["user_pct"])
    emit("host.cpu.system_pct", host["cpu"]["system_pct"])
    emit("host.cpu.idle_pct", host["cpu"]["idle_pct"])
    emit("host.cpu.iowait_pct", host["cpu"]["iowait_pct"])
    emit("host.mem.total_mb", host["mem"]["total_mb"])
    emit("host.mem.used_mb", host["mem"]["used_mb"])
    emit("host.mem.available_mb", host["mem"]["available_mb"])
    emit("host.load.1m", host["load"]["1m"])
    emit("host.load.5m", host["load"]["5m"])
    emit("host.load.15m", host["load"]["15m"])
    emit("host.disk.used_pct", host["disk"]["used_pct"])
    emit("host.disk.free_gb", host["disk"]["free_gb"])
    emit("host.net.rx_kbps", host["net"]["rx_kbps"])
    emit("host.net.tx_kbps", host["net"]["tx_kbps"])
    emit("host.diskio.read_kbps", host["diskio"]["read_kbps"])
    emit("host.diskio.write_kbps", host["diskio"]["write_kbps"])
    emit("host.proc.rss_mb", host["proc"]["rss_mb"])
    emit("host.proc.uptime_hours", host["proc"]["uptime_hours"])

    return {"host": host, "series": series, "disk_device": device}


def reset_host_metrics_state() -> None:
    """Drop the previous reading (tests; also makes a fresh process reproducible)."""
    global _previous
    with _previous_lock:
        _previous = None


__all__ = [
    "base_disk_name",
    "collect_host_metrics",
    "is_egress_interface",
    "reset_host_metrics_state",
]

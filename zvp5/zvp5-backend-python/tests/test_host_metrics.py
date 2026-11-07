"""Host metrics: /proc parsing, rate derivation and device selection."""

from __future__ import annotations

import os

import pytest

from services import host_metrics

STAT_FIRST = """cpu  100 0 50 850 0 0 0 0 0 0
cpu0 50 0 25 425 0 0 0 0 0 0
intr 12345 0 0 0
ctxt 999
"""

# +10 user, +10 system, +30 idle: 50 jiffies total, split 20/20/60/0.
STAT_SECOND = """cpu  110 0 60 880 0 0 0 0 0 0
cpu0 55 0 30 440 0 0 0 0 0 0
intr 23456 0 0 0
ctxt 1999
"""

MEMINFO = """MemTotal:        1965888 kB
MemFree:          200000 kB
MemAvailable:    1128363 kB
SwapTotal:             0 kB
"""

LOADAVG = "0.01 0.02 0.00 1/234 5678\n"

# Only eth0's 1000/200 bytes count: lo and the container/tunnel interfaces are
# excluded (a veth pair mirrors the physical NIC, a bridge/tunnel adds more).
NET_DEV_FIRST = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:    5000      50    0    0    0     0          0         0     5000      50    0    0    0     0       0          0
  eth0:    1000      10    0    0    0     0          0         0      200       2    0    0    0     0       0          0
docker0:  999999    9999    0    0    0     0          0         0   888888    9999    0    0    0     0       0          0
veth123:  777777    7777    0    0    0     0          0         0   666666    7777    0    0    0     0       0          0
 br-abc:  555555    5555    0    0    0     0          0         0   444444    5555    0    0    0     0       0          0
   tun0:  333333    3333    0    0    0     0          0         0   222222    3333    0    0    0     0       0          0
"""

NET_DEV_SECOND = NET_DEV_FIRST.replace("eth0:    1000", "eth0:    2024").replace(
    "      200       2", "      712       2"
)

# loop0 must never be picked; /dev/nvme0n1p1 on / resolves to nvme0n1.
DISKSTATS_FIRST = """   7       0 loop0 10 0 20 0 0 0 0 0 0 0 0
 259       0 nvme0n1 111 0 1000 50 222 0 3000 60 0 0 0
 259       1 nvme0n1p1 111 0 1000 50 222 0 3000 60 0 0 0
   8       0 sdb 1 0 5 0 1 0 5 0 0 0 0
"""

DISKSTATS_SECOND = """   7       0 loop0 20 0 40 0 0 0 0 0 0 0 0
 259       0 nvme0n1 111 0 3048 50 222 0 3000 60 0 0 0
 259       1 nvme0n1p1 111 0 3048 50 222 0 3000 60 0 0 0
   8       0 sdb 1 0 5 0 1 0 5 0 0 0 0
"""

MOUNTS = """/dev/nvme0n1p1 / ext4 rw,relatime 0 0
tmpfs /run tmpfs rw 0 0
"""

STATUS = "Name:\tpython3.12\nVmRSS:\t   184525 kB\nThreads:\t1\n"

UPTIME = "345678.90 12345.00\n"

# field 22 (starttime) lands at index 19 after the final ')'.
_SELF_STAT_TICKS = 34_000_000
SELF_STAT = (
    "1234 (python3.12) " + " ".join(["S"] + ["0"] * 18 + [str(_SELF_STAT_TICKS)] + ["0"] * 30)
)


class Clock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


@pytest.fixture
def proc(monkeypatch, tmp_path):
    root = tmp_path / "proc"
    (root / "self").mkdir(parents=True)
    (root / "stat").write_text(STAT_FIRST)
    (root / "meminfo").write_text(MEMINFO)
    (root / "loadavg").write_text(LOADAVG)
    (root / "net" / "dev").parent.mkdir(parents=True, exist_ok=True)
    (root / "net" / "dev").write_text(NET_DEV_FIRST)
    (root / "diskstats").write_text(DISKSTATS_FIRST)
    (root / "mounts").write_text(MOUNTS)
    (root / "uptime").write_text(UPTIME)
    (root / "self" / "status").write_text(STATUS)
    (root / "self" / "stat").write_text(SELF_STAT)

    monkeypatch.setattr(host_metrics, "_PROC_ROOT", str(root))
    monkeypatch.setattr(
        host_metrics,
        "_disk_usage",
        lambda mount="/": {"mount": mount, "used_pct": 61.0, "free_gb": 11.3},
    )
    monkeypatch.setattr(host_metrics, "_monotonic", Clock(1000.0, 1001.0))
    host_metrics.reset_host_metrics_state()
    return root


def test_first_reading_has_gauges_but_no_rates(proc):
    data = host_metrics.collect_host_metrics()
    host = data["host"]

    # No previous reading in this process yet => no fabricated rate values.
    assert host["cpu"] == {
        "user_pct": None,
        "system_pct": None,
        "idle_pct": None,
        "iowait_pct": None,
    }
    assert host["net"] == {"rx_kbps": None, "tx_kbps": None}
    assert host["diskio"] == {"read_kbps": None, "write_kbps": None}
    for key in (
        "host.cpu.user_pct",
        "host.net.rx_kbps",
        "host.diskio.read_kbps",
        "host.diskio.write_kbps",
    ):
        assert key not in data["series"]

    assert host["mem"]["total_mb"] == pytest.approx(1919.81, abs=0.01)
    assert host["mem"]["available_mb"] == pytest.approx(1101.92, abs=0.01)
    assert host["mem"]["used_mb"] == pytest.approx(817.9, abs=0.02)
    assert host["load"] == {"1m": 0.01, "5m": 0.02, "15m": 0.0}
    assert host["disk"] == {"mount": "/", "used_pct": 61.0, "free_gb": 11.3}
    assert host["proc"]["rss_mb"] == pytest.approx(180.2, abs=0.01)

    expected_uptime = max(0.0, (345678.90 - _SELF_STAT_TICKS / os.sysconf("SC_CLK_TCK")) / 3600)
    assert host["proc"]["uptime_hours"] == pytest.approx(round(expected_uptime, 3), abs=0.01)

    assert data["series"]["host.mem.total_mb"] == pytest.approx(1919.81, abs=0.01)
    assert data["series"]["host.disk.used_pct"] == 61.0
    assert data["series"]["host.load.1m"] == 0.01


def test_second_reading_derives_rates_from_the_previous_counters(proc):
    host_metrics.collect_host_metrics()

    (proc / "stat").write_text(STAT_SECOND)
    (proc / "net" / "dev").write_text(NET_DEV_SECOND)
    (proc / "diskstats").write_text(DISKSTATS_SECOND)

    data = host_metrics.collect_host_metrics()
    host = data["host"]

    assert host["cpu"]["user_pct"] == 20.0
    assert host["cpu"]["system_pct"] == 20.0
    assert host["cpu"]["idle_pct"] == 60.0
    assert host["cpu"]["iowait_pct"] == 0.0

    # +1024 bytes rx and +512 bytes tx over one second.
    assert host["net"]["rx_kbps"] == 1.0
    assert host["net"]["tx_kbps"] == 0.5

    # +2048 sectors = +1 MiB read; no write progress.
    assert host["diskio"]["read_kbps"] == 1024.0
    assert host["diskio"]["write_kbps"] == 0.0

    assert data["series"]["host.cpu.user_pct"] == 20.0
    assert data["series"]["host.net.rx_kbps"] == 1.0
    assert data["series"]["host.diskio.read_kbps"] == 1024.0


def test_main_disk_device_excludes_loop_and_partitions(proc):
    data = host_metrics.collect_host_metrics()
    assert data["disk_device"] == "nvme0n1"


def test_device_falls_back_to_first_physical_device_without_mounts(proc, monkeypatch):
    (proc / "mounts").unlink()
    monkeypatch.setattr(host_metrics, "_mount_device", lambda mount="/": None)
    data = host_metrics.collect_host_metrics()
    assert data["disk_device"] == "nvme0n1"

    assert host_metrics.base_disk_name("/dev/vda1") == "vda"
    assert host_metrics.base_disk_name("/dev/nvme0n1p3") == "nvme0n1"
    assert host_metrics.base_disk_name("/dev/mmcblk0p2") == "mmcblk0"
    assert host_metrics.base_disk_name("/dev/sda") == "sda"
    assert host_metrics.base_disk_name("/dev/dm-0") == "dm-0"


def test_counter_reset_does_not_emit_a_negative_rate(proc):
    host_metrics.collect_host_metrics()
    (proc / "net" / "dev").write_text(NET_DEV_FIRST.replace("eth0:    1000", "eth0:    50"))
    data = host_metrics.collect_host_metrics()
    assert data["host"]["net"]["rx_kbps"] is None
    assert "host.net.rx_kbps" not in data["series"]


def test_only_real_egress_interfaces_are_counted():
    for name in ("lo", "docker0", "docker_gwbridge", "veth123", "br-abc", "br0",
                 "virbr0", "tun0", "tap0", "zram0", "wg0", "tailscale0", "utun3",
                 "dummy0", "ifb0", ""):
        assert host_metrics.is_egress_interface(name) is False, name
    for name in ("eth0", "enp3s0", "ens3", "eno1", "wlan0", "bond0", "ppp0"):
        assert host_metrics.is_egress_interface(name) is True, name


def test_net_counters_ignore_virtual_interfaces(proc):
    # The fixture carries lo/docker0/veth123/br-abc/tun0 with huge counters; only
    # eth0 (1000 rx / 200 tx) may be summed.
    assert host_metrics._net_counters() == (1000, 200)
    data = host_metrics.collect_host_metrics()
    assert "host.net.rx_kbps" not in data["series"]  # first reading: no rate yet

    (proc / "net" / "dev").write_text(NET_DEV_SECOND)
    second = host_metrics.collect_host_metrics()
    assert second["host"]["net"]["rx_kbps"] == 1.0
    assert second["host"]["net"]["tx_kbps"] == 0.5


def test_real_filesystem_usage_is_sane():
    usage = host_metrics._disk_usage("/")
    assert usage["mount"] == "/"
    assert 0.0 <= usage["used_pct"] <= 100.0
    assert usage["free_gb"] >= 0.0

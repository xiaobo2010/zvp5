"""``util/statement.py`` 与 reportlab 解耦的防回归测试。

每日导出早已从 PDF 改成 JSON，PDF 渲染整条链路（reportlab import、
``VolunteerStatementGenerator``、``create_statement_from_kernel_data``）已
删除；这个用例把「模块级不再依赖 reportlab」钉死。

手法：在**子进程**里先往 ``sys.meta_path`` 插一个拒绝 reportlab 的 finder，
再 import ``util.statement`` 并跑一遍 ``statement_data_to_json``。走子进程是
为了避免本进程已导入的模块被 reload —— reload 会让 pydantic 类身份漂移，
波及同会话其它用例。子进程里先自证封锁生效（``import reportlab`` 会炸），
所以「能 import 成功」不是因为 reportlab 恰好没装。

Run from the repository root::

    ./.venv/bin/python -m pytest tests/test_statement_reportlab_free.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_PROBE = r"""
import importlib.util
import json
import sys

installed = importlib.util.find_spec("reportlab") is not None


class _BlockReportlab:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "reportlab" or fullname.startswith("reportlab."):
            raise ImportError("reportlab is blocked by test_statement_reportlab_free")
        return None


sys.meta_path.insert(0, _BlockReportlab())
for name in [n for n in sys.modules if n.startswith("reportlab")]:
    del sys.modules[name]

try:
    import reportlab  # noqa: F401
except ImportError:
    pass
else:
    raise SystemExit("blocker ineffective: reportlab is still importable")

from util.statement import (
    StatsData,
    UserData,
    VolunteerStatementData,
    statement_data_to_json,
)

data = VolunteerStatementData(
    user_data=UserData(
        name="张三",
        student_id="20240001",
        class_name="高一(1)班",
        export_time="2026-10-19 12:00:00",
    ),
    stats_data=StatsData(
        on_campus_hours=1.0, off_campus_hours=2.0, social_practice_hours=3.0
    ),
    stats_data_raw=StatsData(
        on_campus_hours=1.0, off_campus_hours=2.0, social_practice_hours=3.0
    ),
    stats_data_this_year=StatsData(
        on_campus_hours=1.0, off_campus_hours=2.0, social_practice_hours=3.0
    ),
)

payload = json.loads(statement_data_to_json(data, {"_id": "x"}, "x.json"))
print(json.dumps({"installed": installed, "payload": payload}, ensure_ascii=False))
""".strip()


def test_module_imports_and_serialises_without_reportlab() -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"

    stdout = proc.stdout.strip().splitlines()
    result = json.loads(stdout[-1])
    payload = result["payload"]

    assert set(payload) == {
        "user_data",
        "stats_data",
        "stats_data_raw",
        "stats_data_this_year",
        "statement_data",
        "spreadsheet",
        "filename",
    }, f"reportlab installed in this env = {result['installed']}"
    assert payload["filename"] == "x.json"
    assert payload["spreadsheet"] == {"_id": "x"}
    assert payload["stats_data"]["total_hours"] == 6.0

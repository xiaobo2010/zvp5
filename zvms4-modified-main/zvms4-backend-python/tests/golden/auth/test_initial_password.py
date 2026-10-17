"""``util.cert.generate_initial_password`` 的离线单测。

只验证一次性初始密码的四条不变量：长度 / 字符集 / 刻意不达强度、以及随机性
（连续 50 次不重复）。不连数据库、不发 HTTP。

导入 ``util.cert`` 会在 import 时读取仓库根的 ``aes_key.txt`` / ``rsa_*.pem``，
所以 pytest 必须从仓库根运行（验收命令即如此），与 ``tests/golden/auth`` 既有
引导风格一致。
"""

from __future__ import annotations

import string

from util.cert import generate_initial_password, pwd_strength

ALPHABET = set(string.ascii_lowercase + string.digits)


def test_length_is_12() -> None:
    assert len(generate_initial_password()) == 12


def test_charset_is_lowercase_letters_and_digits_only() -> None:
    for _ in range(20):
        assert set(generate_initial_password()) <= ALPHABET


def test_strength_stays_below_the_enforcement_threshold() -> None:
    # pwd_strength < 3 正是后端「弱密码 → 只发 5 分钟临时 token → 前端强制改密」
    # 闭环的触发条件；若这里 >= 3，新用户/重置后的账号会绕过强制改密。
    for _ in range(20):
        assert pwd_strength(generate_initial_password()) < 3


def test_fifty_consecutive_draws_are_unique() -> None:
    samples = {generate_initial_password() for _ in range(50)}
    assert len(samples) == 50

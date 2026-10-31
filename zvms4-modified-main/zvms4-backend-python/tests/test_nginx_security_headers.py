from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "config" / "render_nginx_security_headers.py"
SPEC = importlib.util.spec_from_file_location("render_nginx_security_headers", SCRIPT)
assert SPEC and SPEC.loader
RENDERER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RENDERER)


class RenderNginxSecurityHeadersTest(unittest.TestCase):
    def test_replaces_all_origin_placeholders(self) -> None:
        rendered = RENDERER.render(
            "connect-src __ZVMS_API_ORIGIN__ __ZVMS_API_WS_ORIGIN__",
            "https://api.example.test",
            "wss://api.example.test",
        )

        self.assertEqual(
            rendered,
            "connect-src https://api.example.test wss://api.example.test",
        )

    def test_rejects_wrong_scheme(self) -> None:
        with self.assertRaisesRegex(ValueError, "ZVMS_API_ORIGIN"):
            RENDERER.render(
                "connect-src __ZVMS_API_ORIGIN__ __ZVMS_API_WS_ORIGIN__",
                "http://api.example.test",
                "wss://api.example.test",
            )

    def test_rejects_non_origin_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "exact"):
            RENDERER.render(
                "connect-src __ZVMS_API_ORIGIN__ __ZVMS_API_WS_ORIGIN__",
                "https://api.example.test/path",
                "wss://api.example.test",
            )


if __name__ == "__main__":
    unittest.main()

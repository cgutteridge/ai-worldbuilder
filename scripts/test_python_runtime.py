"""Unit tests for the Python interpreter / numpy fail-fast guard (R-013)."""
from __future__ import annotations

import builtins
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

TOOL_ROOT = Path(__file__).resolve().parent.parent
if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

import python_runtime  # noqa: E402


class PythonRuntimeGuardTests(unittest.TestCase):
    """``ensure_python_runtime`` refuses old interpreters and missing numpy."""

    def test_rejects_python_below_3_10(self) -> None:
        """3.9 must exit with the one-line Homebrew message."""
        with mock.patch.object(python_runtime.sys, "version_info", (3, 9, 6)):
            with mock.patch.object(
                python_runtime.sys,
                "executable",
                "/usr/bin/python3",
            ):
                with mock.patch.object(
                    python_runtime.sys,
                    "version",
                    "3.9.6 (default, Nov 11 2024)",
                ):
                    with self.assertRaises(SystemExit) as ctx:
                        python_runtime.ensure_python_runtime()
        msg = str(ctx.exception)
        self.assertIn("needs python3 >= 3.10 with numpy (Homebrew)", msg)
        self.assertIn("/usr/bin/python3", msg)
        self.assertIn("3.9.6", msg)

    def test_rejects_missing_numpy(self) -> None:
        """Interpreter new enough but without numpy still exits the same way."""
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "numpy" or name.startswith("numpy."):
                raise ModuleNotFoundError("No module named 'numpy'")
            return real_import(name, *args, **kwargs)

        with mock.patch.object(python_runtime.sys, "version_info", (3, 10, 0)):
            with mock.patch.object(
                python_runtime.sys,
                "executable",
                "/usr/bin/python3",
            ):
                with mock.patch.object(
                    python_runtime.sys,
                    "version",
                    "3.10.0 (default)",
                ):
                    with mock.patch("builtins.__import__", side_effect=fake_import):
                        with self.assertRaises(SystemExit) as ctx:
                            python_runtime.ensure_python_runtime()
        msg = str(ctx.exception)
        self.assertIn("needs python3 >= 3.10 with numpy (Homebrew)", msg)
        self.assertIn("/usr/bin/python3", msg)

    def test_accepts_current_runtime(self) -> None:
        """Homebrew 3.10+ with numpy installed must return without exiting."""
        self.assertGreaterEqual(sys.version_info[:2], (3, 10))
        python_runtime.ensure_python_runtime()

    def test_message_helper_shape(self) -> None:
        """Failure text always names executable and short version."""
        fake = SimpleNamespace(
            executable="/opt/homebrew/bin/python3",
            version="3.9.0 (main)",
        )
        with mock.patch.object(python_runtime, "sys", fake):
            msg = python_runtime.runtime_requirement_message()
        self.assertEqual(
            msg,
            "needs python3 >= 3.10 with numpy (Homebrew); "
            "got /opt/homebrew/bin/python3 3.9.0",
        )


if __name__ == "__main__":
    unittest.main()

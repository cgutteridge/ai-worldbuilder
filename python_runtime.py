"""Fail fast when the process is not a usable worldbuilder Python.

Bare ``python3`` after a macOS reboot can resolve to Apple ``/usr/bin/python3``
(3.9, no numpy). That yields misleading errors (PEP 604 ``|`` annotations,
``No module named numpy``, red pre-commit). Import this module early from
entry points and raise ``SystemExit`` with one clear line instead.
"""
from __future__ import annotations

import sys

MIN_VERSION = (3, 10)


def runtime_requirement_message() -> str:
    """One-line refusal naming the executable and short version string.

    @returns: message suitable for ``SystemExit`` / stderr
    """
    short = sys.version.split()[0]
    return (
        f"needs python3 >= 3.10 with numpy (Homebrew); "
        f"got {sys.executable} {short}"
    )


def ensure_python_runtime() -> None:
    """Require Python >= 3.10 with numpy importable; else exit non-zero.

    @raises SystemExit: when the interpreter or numpy is unsuitable
    """
    if sys.version_info < MIN_VERSION:
        raise SystemExit(runtime_requirement_message())
    try:
        import numpy  # noqa: F401
    except ImportError:
        raise SystemExit(runtime_requirement_message()) from None

"""Shared script preamble for the worldbuilder tool.

``ROOT`` is the **active world** checkout (place memory, world frame, local
providers). ``TOOL_ROOT`` is this worldbuilder repo (scripts, skills, system
providers). Provider packages live under two roots:

- ``SYSTEM_PROVIDERS`` — ``TOOL_ROOT / "providers"`` (nationwide+ catalogue)
- ``WORLD_PROVIDERS`` — ``ROOT / "providers"`` (active-world catalogue)

Shared libraries live under ``TOOL_ROOT / "lib" / <category> /`` (bare imports).
CLI entry points live under ``scripts/``. Catalogue ``package`` fields are
relative paths (e.g. ``providers/<id>``); resolve them with
:func:`resolve_provider_package`. Prefer ``from script_env import ROOT, TOOL_ROOT``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent.parent
if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

import python_runtime  # noqa: E402

python_runtime.ensure_python_runtime()

from active_world import WORLD_ROOT  # noqa: E402

ROOT = WORLD_ROOT
SCRIPTS = TOOL_ROOT / "scripts"
SHIMS = TOOL_ROOT / "shims"
LIB = TOOL_ROOT / "lib"
SYSTEM_PROVIDERS = TOOL_ROOT / "providers"
WORLD_PROVIDERS = ROOT / "providers"


def lib_category_dirs(lib_root: Path | None = None) -> list[Path]:
    """Return ``lib/<category>/`` directories that hold library modules.

    @param lib_root: library root (default: ``TOOL_ROOT / "lib"``)
    @returns: sorted category directories (only existing dirs, not files)
    """
    root = LIB if lib_root is None else Path(lib_root)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def ensure_import_paths() -> None:
    """Put ``lib/*``, ``shims/``, and ``scripts/`` on ``sys.path``.

    Library category dirs are forced to the front every call so bare imports
    hit ``lib/`` modules even when a caller previously prepended ``scripts/``
    (where same-named CLI wrappers live).
    """
    for _path in (SCRIPTS, SHIMS):
        _text = str(_path)
        if _text not in sys.path:
            sys.path.insert(0, _text)
    for _path in reversed(lib_category_dirs()):
        _text = str(_path)
        if _text in sys.path:
            sys.path.remove(_text)
        sys.path.insert(0, _text)


ensure_import_paths()


def resolve_provider_package(package: str, home: str | None = None) -> Path:
    """Resolve a catalogue ``package`` path against system and/or world roots.

    ``package`` is relative to the chosen repo root (e.g. ``providers/uos-portals``).
    ``home`` selects which root to use:

    - ``"system"`` — under ``TOOL_ROOT``
    - ``"world"`` — under ``ROOT`` (active world)
    - ``None`` — first existing directory, system then world

    @param package: relative package path from the provider catalogue
    @param home: ``system``, ``world``, or ``None`` to search both
    @returns: absolute path to the package directory
    @raises ValueError: if ``package`` or ``home`` is invalid
    @raises FileNotFoundError: if no matching directory exists
    """
    if not package or not isinstance(package, str):
        raise ValueError(f"invalid package path: {package!r}")
    pkg = Path(package)
    if pkg.is_absolute() or ".." in pkg.parts:
        raise ValueError(f"package must be a relative path without ..: {package!r}")
    if home is not None and home not in ("system", "world"):
        raise ValueError(f"home must be 'system', 'world', or None: {home!r}")

    candidates: list[Path] = []
    if home in (None, "system"):
        candidates.append(TOOL_ROOT / package)
    if home in (None, "world"):
        candidates.append(ROOT / package)

    for path in candidates:
        if path.is_dir():
            return path.resolve()
    tried = ", ".join(str(p) for p in candidates)
    raise FileNotFoundError(
        f"provider package not found for {package!r} (home={home!r}); tried: {tried}"
    )


def make_parser(doc: str | None, **kw) -> argparse.ArgumentParser:
    """ArgumentParser that shows the module docstring verbatim as its description.

    @param doc: description text (usually the module docstring)
    @param kw: forwarded to ArgumentParser
    @returns: configured parser
    """
    return argparse.ArgumentParser(
        description=doc,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        **kw,
    )

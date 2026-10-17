"""Shared script preamble for the worldbuilder tool.

``ROOT`` is the **active world** checkout (place memory, world frame, local
providers). ``TOOL_ROOT`` is this worldbuilder repo (scripts, skills, system
providers). Provider packages live under two roots:

- ``SYSTEM_PROVIDERS`` — ``TOOL_ROOT / "providers"`` (nationwide+ catalogue)
- ``WORLD_PROVIDERS`` — ``ROOT / "providers"`` (active-world catalogue)

Catalogue ``package`` fields are relative paths (e.g. ``providers/ukdefra-lidar``);
resolve them with :func:`resolve_provider_package`. Prefer
``from script_env import ROOT, TOOL_ROOT``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent.parent
if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

from active_world import WORLD_ROOT  # noqa: E402

ROOT = WORLD_ROOT
SCRIPTS = TOOL_ROOT / "scripts"
SHIMS = TOOL_ROOT / "shims"
SYSTEM_PROVIDERS = TOOL_ROOT / "providers"
WORLD_PROVIDERS = ROOT / "providers"

# Provider CLIs: shims/ (system) then scripts/ (glue + world-local shims).
for _path in (SHIMS, SCRIPTS):
    _text = str(_path)
    if _text not in sys.path:
        sys.path.insert(0, _text)


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

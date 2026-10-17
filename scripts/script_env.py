"""Shared script preamble for the worldbuilder tool.

``ROOT`` is the **active world** checkout (place memory, world frame, local
providers). ``TOOL_ROOT`` is this worldbuilder repo (scripts, skills, system
providers). Prefer ``from script_env import ROOT, TOOL_ROOT``.
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
PROVIDERS = TOOL_ROOT / "providers"

# Provider CLIs live under shims/; glue stays in scripts/. Prefer shims for
# historical module names (defra_lidar, google_aerial, …).
for _path in (SHIMS, SCRIPTS):
    _text = str(_path)
    if _text not in sys.path:
        sys.path.insert(0, _text)


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

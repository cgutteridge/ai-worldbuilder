"""System-level active-world pointer (ADR 002).

Import this module from worldbuilder scripts and skills::

    from active_world import WORLD_ROOT, load_world_config, load_world_frame

Set env ``AI_WORLDBUILDER_WORLD_ROOT`` to the active world checkout. Exactly one
world is active at a time. There is no silent default: unset refuses.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import python_runtime

python_runtime.ensure_python_runtime()

WORLD_ENV = "AI_WORLDBUILDER_WORLD_ROOT"


def _resolve_world_root() -> Path:
    """Resolve the active world checkout from ``AI_WORLDBUILDER_WORLD_ROOT``.

    @returns: absolute path to the world checkout
    @raises RuntimeError: when the env var is unset or empty
    """
    raw = os.environ.get(WORLD_ENV)
    if not raw or not str(raw).strip():
        raise RuntimeError(
            f"{WORLD_ENV} is unset. Set it to the active world checkout "
            f"(e.g. export {WORLD_ENV}=$PWD/worlds/southampton-build)."
        )
    return Path(raw).expanduser().resolve()


WORLD_ROOT: Path = _resolve_world_root()

TOOL_ROOT: Path = Path(__file__).resolve().parent


def world_config_path() -> Path:
    """Path to the active world's identity config (gameworld id + runtime paths)."""
    return WORLD_ROOT / "config" / "world.json"


def world_frame_path() -> Path:
    """Path to the active world's frame (offsets, CRS, Y offset, bounds)."""
    return WORLD_ROOT / "config" / "world-frame.json"


def load_world_config(*, reload: bool = False) -> dict[str, Any]:
    """Load ``config/world.json`` from the active world.

    @param reload: unused; reserved for cache invalidation callers
    @returns: parsed JSON object
    @raises FileNotFoundError: if the world config is missing
    @raises ValueError: if required identity fields are absent
    """
    del reload
    path = world_config_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"Active world config missing: {path} (WORLD_ROOT={WORLD_ROOT})"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    gameworld_id = data.get("gameworld_id") or data.get("world_name")
    if not gameworld_id:
        raise ValueError(
            f"{path} must set gameworld_id (or legacy world_name)"
        )
    data["gameworld_id"] = str(gameworld_id)
    return data


def load_world_frame(*, reload: bool = False) -> dict[str, Any]:
    """Load ``config/world-frame.json`` from the active world.

    @param reload: unused; reserved for cache invalidation callers
    @returns: parsed JSON object
    @raises FileNotFoundError: if the frame file is missing
    @raises ValueError: if offsets are absent
    """
    del reload
    path = world_frame_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"Active world frame missing: {path} (WORLD_ROOT={WORLD_ROOT})"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if "offsets" not in data:
        raise ValueError(f"{path} missing offsets")
    return data


def gameworld_id() -> str:
    """Return the active world's gameworld id (Minecraft world identity)."""
    return str(load_world_config()["gameworld_id"])

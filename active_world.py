"""System-level active-world pointer (ADR 002).

Import this module from worldbuilder scripts and skills::

    from active_world import set_world, world_root, load_world_config, load_world_frame

Select a world with ``set_world(name)`` (CLI ``--world``) or ``set_world_root(path)``
(tests / disposable checkouts). Exactly one world is active at a time. There is
no silent default and no environment-variable fallback: call ``world_root()``
before place memory is read, or it raises.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import python_runtime

python_runtime.ensure_python_runtime()

TOOL_ROOT: Path = Path(__file__).resolve().parent

WORLD_ROOT: Path | None = None


def list_world_names() -> list[str]:
    """Return directory names under ``worlds/`` (sorted).

    @returns: world directory basenames; empty when ``worlds/`` is missing
    """
    worlds = TOOL_ROOT / "worlds"
    if not worlds.is_dir():
        return []
    return sorted(
        p.name
        for p in worlds.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )


def set_world(name: str) -> Path:
    """Select the active world by directory name under ``worlds/``.

    @param name: world dirname (e.g. ``southampton-build``, ``bakeoff``); no ``..``
    @returns: absolute path to the world checkout
    @raises RuntimeError: when the name is invalid or the directory is missing
    """
    if not name or not str(name).strip():
        raise RuntimeError(
            "no world selected; pass --world <name> (a directory under worlds/)"
        )
    raw = str(name).strip()
    if raw in (".", "..") or "/" in raw or "\\" in raw or ".." in Path(raw).parts:
        raise RuntimeError(
            f"invalid --world {name!r}; pass a directory name under worlds/"
        )
    path = (TOOL_ROOT / "worlds" / raw).resolve()
    worlds_root = (TOOL_ROOT / "worlds").resolve()
    if path.parent != worlds_root or not path.is_dir():
        available = list_world_names()
        hint = ", ".join(available) if available else "(none found)"
        raise RuntimeError(
            f"unknown --world {raw!r}; expected a directory under worlds/ "
            f"(available: {hint})"
        )
    return set_world_root(path)


def set_world_root(path: Path) -> Path:
    """Select the active world by absolute checkout path (tests / disposable).

    Not a CLI flag — use :func:`set_world` for ``--world <name>``.

    @param path: existing world checkout directory
    @returns: absolute resolved path
    @raises RuntimeError: when the path is missing or not a directory
    """
    global WORLD_ROOT
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"world root is not a directory: {root}")
    WORLD_ROOT = root
    return root


def world_root() -> Path:
    """Return the active world checkout.

    @returns: absolute path to the selected world
    @raises RuntimeError: when no world has been selected yet
    """
    if WORLD_ROOT is None:
        raise RuntimeError(
            "no world selected; pass --world <name> (a directory under worlds/)"
        )
    return WORLD_ROOT


def world_config_path() -> Path:
    """Path to the active world's identity config (gameworld id + runtime paths)."""
    return world_root() / "config" / "world.json"


def world_frame_path() -> Path:
    """Path to the active world's frame (offsets, CRS, Y offset, bounds)."""
    return world_root() / "config" / "world-frame.json"


def load_world_config(*, reload: bool = False) -> dict[str, Any]:
    """Load ``config/world.json`` from the active world.

    @param reload: unused; reserved for cache invalidation callers
    @returns: parsed JSON object
    @raises FileNotFoundError: if the world config is missing
    @raises ValueError: if required identity fields are absent
    """
    del reload
    path = world_config_path()
    root = world_root()
    if not path.is_file():
        raise FileNotFoundError(
            f"Active world config missing: {path} (WORLD_ROOT={root})"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    gameworld_id = data.get("gameworld_id")
    if not gameworld_id:
        raise ValueError(f"{path} must set gameworld_id")
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
    root = world_root()
    if not path.is_file():
        raise FileNotFoundError(
            f"Active world frame missing: {path} (WORLD_ROOT={root})"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if "offsets" not in data:
        raise ValueError(f"{path} missing offsets")
    return data


def save_path(*, required: bool = True) -> Path | None:
    """Minecraft save folder for the active world (read-only to this tool).

    The save lives in a standalone Minecraft instance outside this project;
    ``config/world.json`` ``save_path`` references it. A relative value is taken
    against the world root.

    @param required: raise when ``save_path`` is unset (else return None)
    @returns: absolute save path, or None when unset and not required
    @raises RuntimeError: when required and ``save_path`` is unset
    """
    raw = load_world_config().get("save_path")
    return resolve_save(raw, required)


def resolve_save(raw: Any, required: bool) -> Path | None:
    """Resolve a ``save_path`` value against the world root.

    @param raw: the config value (may be empty)
    @param required: raise when empty
    @returns: absolute path or None
    """
    if not raw:
        if required:
            raise RuntimeError(
                f"{world_config_path()} missing save_path "
                "(point it at the save inside your Minecraft instance; "
                "see docs/minecraft-instance-setup.md)"
            )
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else world_root() / path


def region_path(*, required: bool = True) -> Path | None:
    """Overworld region folder, derived from :func:`save_path`.

    @param required: raise when ``save_path`` is unset (else return None)
    @returns: ``<save>/dimensions/minecraft/overworld/region`` or None
    """
    save = save_path(required=required)
    if save is None:
        return None
    return save / "dimensions" / "minecraft" / "overworld" / "region"


def gameworld_id() -> str:
    """Return the active world's gameworld id (Minecraft world identity)."""
    return str(load_world_config()["gameworld_id"])

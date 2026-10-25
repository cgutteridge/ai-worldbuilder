"""Contract tests for active_world pointer and per-world config shape."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

TOOL_ROOT = Path(__file__).resolve().parent.parent
if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

WORLD_ENV = "AI_WORLDBUILDER_WORLD_ROOT"


def _purge_active_world() -> None:
    """Drop cached ``active_world`` so the next import re-resolves WORLD_ROOT."""
    for mod in list(sys.modules):
        if mod == "active_world" or mod.startswith("active_world."):
            del sys.modules[mod]


def _snapshot_active_world_modules() -> dict[str, ModuleType]:
    """Capture ``active_world`` entries so the suite can restore the same objects."""
    return {
        name: mod
        for name, mod in sys.modules.items()
        if name == "active_world" or name.startswith("active_world.")
    }


def _restore_active_world_modules(saved: dict[str, ModuleType]) -> None:
    """Put back the exact module objects other tests already imported."""
    _purge_active_world()
    sys.modules.update(saved)


def _clear_frame_cache() -> None:
    """Reset ``world_frame`` module cache when that module is already loaded."""
    wf = sys.modules.get("world_frame")
    if wf is not None and hasattr(wf, "_FRAME"):
        wf._FRAME = None


@contextmanager
def _isolated_world_root(world_root: str | None) -> Iterator[None]:
    """Set or clear WORLD_ENV, then restore env, modules, and frame cache.

    Restores the pre-test ``active_world`` module objects (not a fresh import) so
    other suite modules that already hold ``import active_world`` keep working
    and ``mock.patch('active_world.…')`` still targets the same object.

    @param world_root: absolute path to a temporary world, or None to unset
    """
    saved_env = os.environ.get(WORLD_ENV)
    saved_modules = _snapshot_active_world_modules()
    wf = sys.modules.get("world_frame")
    saved_frame = getattr(wf, "_FRAME", None) if wf is not None else None
    _purge_active_world()
    _clear_frame_cache()
    try:
        if world_root is None:
            os.environ.pop(WORLD_ENV, None)
        else:
            os.environ[WORLD_ENV] = world_root
        yield
    finally:
        if saved_env is None:
            os.environ.pop(WORLD_ENV, None)
        else:
            os.environ[WORLD_ENV] = saved_env
        _restore_active_world_modules(saved_modules)
        if wf is not None and hasattr(wf, "_FRAME"):
            wf._FRAME = saved_frame


def _write_fictional_world(
    root: Path,
    *,
    world: dict,
    frame: dict,
) -> None:
    """Write temporary world.json + world-frame.json under ``root``."""
    config = root / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "world.json").write_text(json.dumps(world), encoding="utf-8")
    (config / "world-frame.json").write_text(json.dumps(frame), encoding="utf-8")


class ActiveWorldContractTests(unittest.TestCase):
    """Active world loads identity + frame from WORLD_ROOT."""

    def test_unset_world_root_refuses(self) -> None:
        """Import without AI_WORLDBUILDER_WORLD_ROOT refuses; no silent default."""
        with _isolated_world_root(None):
            with self.assertRaises(RuntimeError) as ctx:
                import active_world  # noqa: F401
            self.assertIn(WORLD_ENV, str(ctx.exception))

    def test_loads_gameworld_id_and_offsets(self) -> None:
        """WORLD_ROOT with world.json + world-frame.json yields id and offsets."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fictional_world(
                root,
                world={
                    "gameworld_id": "TestWorld",
                    "save_path": str(root / "instance" / "saves" / "TestWorld"),
                },
                frame={
                    "place": "Test",
                    "crs": "EPSG:27700",
                    "offsets": {"easting": 1.0, "northing": 2.0},
                    "bounds_mc": {
                        "west": 0,
                        "east": 10,
                        "north": 0,
                        "south": -10,
                    },
                    "y_offset": 11,
                },
            )
            with _isolated_world_root(str(root)):
                import active_world as aw

                self.assertEqual(aw.WORLD_ROOT, root.resolve())
                self.assertEqual(aw.gameworld_id(), "TestWorld")
                frame = aw.load_world_frame()
                self.assertEqual(frame["offsets"]["easting"], 1.0)
                self.assertEqual(frame["offsets"]["northing"], 2.0)

    def test_legacy_world_name_accepted(self) -> None:
        """Legacy world_name fills gameworld_id when the new key is absent."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fictional_world(
                root,
                world={"world_name": "LegacyName"},
                frame={"offsets": {"easting": 0, "northing": 0}},
            )
            with _isolated_world_root(str(root)):
                import active_world as aw

                self.assertEqual(aw.gameworld_id(), "LegacyName")


if __name__ == "__main__":
    unittest.main()

"""Contract tests for active_world pointer and per-world config shape."""
from __future__ import annotations

import json
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
SCRIPTS = TOOL_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


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
    if wf is not None and hasattr(wf, "clear_frame_cache"):
        wf.clear_frame_cache()
    elif wf is not None and hasattr(wf, "_FRAME"):
        wf._FRAME = None


@contextmanager
def _isolated_world(selected: Path | None) -> Iterator[None]:
    """Select or clear the active world, then restore modules and frame cache.

    @param selected: absolute path to a temporary world, or None to leave unset
    """
    import active_world
    import script_env

    saved_modules = _snapshot_active_world_modules()
    saved_root = active_world.WORLD_ROOT
    wf = sys.modules.get("world_frame")
    saved_frame = getattr(wf, "_FRAME", None) if wf is not None else None
    try:
        if selected is None:
            active_world.WORLD_ROOT = None
        else:
            active_world.set_world_root(selected)
            script_env.bind_world()
            try:
                script_env.clear_world_caches()
            except (KeyError, FileNotFoundError, RuntimeError):
                # Minimal fixture worlds may omit region_path; skip site_kit reload.
                pass
        _clear_frame_cache()
        yield
    finally:
        current = sys.modules.get("active_world")
        if current is not None:
            current.WORLD_ROOT = saved_root
        if saved_root is not None:
            try:
                script_env.clear_world_caches()
            except (KeyError, FileNotFoundError, RuntimeError):
                pass
        else:
            script_env.clear_world_caches()
        _restore_active_world_modules(saved_modules)
        restored = sys.modules.get("active_world")
        if restored is not None:
            restored.WORLD_ROOT = saved_root
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

    def test_world_root_before_select_raises(self) -> None:
        """``world_root()`` before select refuses; no silent default."""
        with _isolated_world(None):
            import active_world as aw

            with self.assertRaises(RuntimeError) as ctx:
                aw.world_root()
            self.assertIn("--world", str(ctx.exception))

    def test_import_without_world_ok(self) -> None:
        """Importing ``active_world`` does not require a selected world."""
        with _isolated_world(None):
            _purge_active_world()
            import active_world as aw

            self.assertIsNone(aw.WORLD_ROOT)

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
            with _isolated_world(root):
                import active_world as aw

                self.assertEqual(aw.WORLD_ROOT, root.resolve())
                self.assertEqual(aw.gameworld_id(), "TestWorld")
                frame = aw.load_world_frame()
                self.assertEqual(frame["offsets"]["easting"], 1.0)
                self.assertEqual(frame["offsets"]["northing"], 2.0)

    def test_save_and_region_paths_derived(self) -> None:
        """region_path is derived from save_path; a relative save_path is taken from the world root."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fictional_world(
                root,
                world={"gameworld_id": "W", "save_path": "../inst/saves/W"},
                frame={"offsets": {"easting": 0, "northing": 0}},
            )
            with _isolated_world(root):
                import active_world as aw

                save = aw.save_path()
                self.assertEqual(save, root.resolve() / ".." / "inst" / "saves" / "W")
                self.assertEqual(
                    aw.region_path(), save / "dimensions" / "minecraft" / "overworld" / "region"
                )

    def test_missing_save_path(self) -> None:
        """Without save_path: required raises with the setup hint, optional gives None."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fictional_world(
                root,
                world={"gameworld_id": "W"},
                frame={"offsets": {"easting": 0, "northing": 0}},
            )
            with _isolated_world(root):
                import active_world as aw

                self.assertIsNone(aw.save_path(required=False))
                with self.assertRaises(RuntimeError) as ctx:
                    aw.save_path()
                self.assertIn("minecraft-instance-setup", str(ctx.exception))

    def test_world_name_alone_rejected(self) -> None:
        """A world.json with only the retired world_name key has no gameworld_id."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_fictional_world(
                root,
                world={"world_name": "LegacyName"},
                frame={"offsets": {"easting": 0, "northing": 0}},
            )
            with _isolated_world(root):
                import active_world as aw

                with self.assertRaises(ValueError):
                    aw.gameworld_id()

    def test_set_world_resolves_under_worlds(self) -> None:
        """``set_world(name)`` resolves ``worlds/<name>`` when the directory exists."""
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp)
            worlds = tool / "worlds"
            named = worlds / "demo-place"
            named.mkdir(parents=True)
            _write_fictional_world(
                named,
                world={"gameworld_id": "Demo"},
                frame={"offsets": {"easting": 0, "northing": 0}},
            )
            import active_world as aw

            saved_tool = aw.TOOL_ROOT
            saved_root = aw.WORLD_ROOT
            try:
                aw.TOOL_ROOT = tool
                path = aw.set_world("demo-place")
                self.assertEqual(path, named.resolve())
                self.assertEqual(aw.world_root(), named.resolve())
            finally:
                aw.TOOL_ROOT = saved_tool
                aw.WORLD_ROOT = saved_root

    def test_set_world_unknown_name_lists_available(self) -> None:
        """Unknown ``--world`` name fails with available directory names."""
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp)
            (tool / "worlds" / "alpha").mkdir(parents=True)
            (tool / "worlds" / "beta").mkdir(parents=True)
            import active_world as aw

            saved_tool = aw.TOOL_ROOT
            saved_root = aw.WORLD_ROOT
            try:
                aw.TOOL_ROOT = tool
                aw.WORLD_ROOT = None
                with self.assertRaises(RuntimeError) as ctx:
                    aw.set_world("missing")
                msg = str(ctx.exception)
                self.assertIn("missing", msg)
                self.assertIn("alpha", msg)
                self.assertIn("beta", msg)
            finally:
                aw.TOOL_ROOT = saved_tool
                aw.WORLD_ROOT = saved_root

    def test_set_world_rejects_path_segments(self) -> None:
        """``set_world`` refuses names with ``..`` or directory separators."""
        import active_world as aw

        with self.assertRaises(RuntimeError):
            aw.set_world("../etc")
        with self.assertRaises(RuntimeError):
            aw.set_world("foo/bar")


if __name__ == "__main__":
    unittest.main()

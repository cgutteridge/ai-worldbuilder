"""Contract tests for active_world pointer and per-world config shape."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent.parent
if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

WORLD_ENV = "AI_WORLDBUILDER_WORLD_ROOT"


def _purge_active_world() -> None:
    for mod in list(sys.modules):
        if mod == "active_world" or mod.startswith("active_world."):
            del sys.modules[mod]


class ActiveWorldContractTests(unittest.TestCase):
    """Active world loads identity + frame from WORLD_ROOT."""

    def test_unset_world_root_refuses(self) -> None:
        """Import without AI_WORLDBUILDER_WORLD_ROOT refuses; no silent default."""
        saved = os.environ.pop(WORLD_ENV, None)
        _purge_active_world()
        try:
            with self.assertRaises(RuntimeError) as ctx:
                import active_world  # noqa: F401
            self.assertIn(WORLD_ENV, str(ctx.exception))
        finally:
            if saved is not None:
                os.environ[WORLD_ENV] = saved
            _purge_active_world()

    def test_loads_gameworld_id_and_offsets(self) -> None:
        """WORLD_ROOT with world.json + world-frame.json yields id and offsets."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "world.json").write_text(
                json.dumps(
                    {
                        "gameworld_id": "TestWorld",
                        "save_path": str(root / "instance" / "saves" / "TestWorld"),
                    }
                ),
                encoding="utf-8",
            )
            (root / "config" / "world-frame.json").write_text(
                json.dumps(
                    {
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
                    }
                ),
                encoding="utf-8",
            )
            os.environ[WORLD_ENV] = str(root)
            _purge_active_world()
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
            (root / "config").mkdir()
            (root / "config" / "world.json").write_text(
                json.dumps({"world_name": "LegacyName"}),
                encoding="utf-8",
            )
            (root / "config" / "world-frame.json").write_text(
                json.dumps({"offsets": {"easting": 0, "northing": 0}}),
                encoding="utf-8",
            )
            os.environ[WORLD_ENV] = str(root)
            _purge_active_world()
            import active_world as aw

            self.assertEqual(aw.gameworld_id(), "LegacyName")


if __name__ == "__main__":
    unittest.main()

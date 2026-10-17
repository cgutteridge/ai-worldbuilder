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


class ActiveWorldContractTests(unittest.TestCase):
    """Active world loads identity + frame from WORLD_ROOT."""

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
            os.environ["AI_WORLDBUILDER_WORLD_ROOT"] = str(root)
            for mod in list(sys.modules):
                if mod == "active_world" or mod.startswith("active_world."):
                    del sys.modules[mod]
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
            os.environ["AI_WORLDBUILDER_WORLD_ROOT"] = str(root)
            for mod in list(sys.modules):
                if mod == "active_world" or mod.startswith("active_world."):
                    del sys.modules[mod]
            import active_world as aw

            self.assertEqual(aw.gameworld_id(), "LegacyName")


if __name__ == "__main__":
    unittest.main()

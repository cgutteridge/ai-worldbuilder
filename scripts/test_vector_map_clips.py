#!/usr/bin/env python3
"""Tests for vector-map clip-tree resolution."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / 'shims', _ROOT / 'scripts'):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

import vector_map_clips as vmc  # noqa: E402


class VectorMapClipRoot(unittest.TestCase):
    """Clip tree prefers the type-named folder, with a legacy fallback."""

    def test_prefers_vector_map_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preferred = root / 'reference' / 'vector_map'
            preferred.mkdir(parents=True)
            (root / 'reference' / 'osm').mkdir()
            self.assertEqual(vmc.vector_map_clip_root(root), preferred)

    def test_falls_back_to_legacy_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / 'reference' / 'osm'
            legacy.mkdir(parents=True)
            self.assertEqual(vmc.vector_map_clip_root(root), legacy)

    def test_empty_vector_map_does_not_hide_legacy_clips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preferred = root / 'reference' / 'vector_map'
            preferred.mkdir(parents=True)
            (preferred / 'survey').mkdir()
            legacy = root / 'reference' / 'osm'
            place = legacy / 'highfield'
            place.mkdir(parents=True)
            (place / 'buildings.geojson').write_text('{"type":"FeatureCollection","features":[]}\n')
            self.assertEqual(vmc.vector_map_clip_root(root), legacy)

    def test_missing_tree_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                vmc.vector_map_clip_root(Path(tmp))


if __name__ == '__main__':
    unittest.main()

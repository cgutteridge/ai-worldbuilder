#!/usr/bin/env python3
"""Tests for vector-map download/cache/reference contract (no network)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / 'shims', _ROOT / 'scripts'):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

import vector_map_clips as vmc  # noqa: E402


def _write_clip(tree: Path, place: str = 'survey', name: str = 'buildings.geojson') -> Path:
    """Create a minimal ``place/*.geojson`` under a clip tree."""
    place_dir = tree / place
    place_dir.mkdir(parents=True, exist_ok=True)
    path = place_dir / name
    path.write_text('{"type":"FeatureCollection","features":[]}\n')
    return path


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
            _write_clip(legacy, 'highfield')
            self.assertEqual(vmc.vector_map_clip_root(root), legacy)

    def test_missing_tree_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError) as ctx:
                vmc.vector_map_clip_root(Path(tmp))
            msg = str(ctx.exception)
            self.assertIn('vector_map', msg)
            self.assertIn('downloads', msg)
            self.assertIn('osm_root', msg)


class VectorMapReferenceContract(unittest.TestCase):
    """Download → cache → project reference helpers (temp dirs; no network)."""

    def test_layout_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(vmc.downloads_dir(root), root / 'downloads')
            self.assertEqual(vmc.reference_dir(root), root / 'reference' / 'vector_map')
            self.assertEqual(
                vmc.default_pbf_path('hampshire-latest.pbf', root),
                root / 'downloads' / 'hampshire-latest.pbf',
            )
            self.assertEqual(
                vmc.place_clip_dir('portswood', root),
                root / 'reference' / 'vector_map' / 'portswood',
            )

    def test_pbf_and_place_reject_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                vmc.default_pbf_path('../other.pbf', root)
            with self.assertRaises(ValueError):
                vmc.place_clip_dir('a/b', root)
            with self.assertRaises(ValueError):
                vmc.place_clip_dir('', root)

    def test_has_clips_and_project_status_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(vmc.has_clips(root))
            status = vmc.project_status(root)
            self.assertFalse(status['ready'])
            self.assertEqual(status['clip_count'], 0)
            self.assertEqual(status['reference'], root / 'reference' / 'vector_map')
            self.assertEqual(status['downloads'], root / 'downloads')

            _write_clip(vmc.reference_dir(root), 'campus')
            self.assertTrue(vmc.has_clips(root))
            status = vmc.project_status(root)
            self.assertTrue(status['ready'])
            self.assertEqual(status['clip_count'], 1)
            self.assertFalse(status['legacy_fallback'])
            self.assertEqual(status['path'], vmc.reference_dir(root))

    def test_project_status_legacy_fallback_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vmc.reference_dir(root).mkdir(parents=True)
            _write_clip(vmc.legacy_reference_dir(root), 'old')
            status = vmc.project_status(root)
            self.assertTrue(status['ready'])
            self.assertTrue(status['legacy_fallback'])

    def test_is_under_world(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inside = root / 'reference' / 'vector_map'
            inside.mkdir(parents=True)
            other = Path(tmp).parent / 'other-world' / 'reference' / 'vector_map'
            # sibling under the same parent temp — use a second temp for outside
            with tempfile.TemporaryDirectory() as other_tmp:
                outside = Path(other_tmp) / 'reference' / 'vector_map'
                outside.mkdir(parents=True)
                self.assertTrue(vmc.is_under_world(inside, root))
                self.assertFalse(vmc.is_under_world(outside, root))


class VectorMapGuideLines(unittest.TestCase):
    """Cell guide treats vector_map as the project reference (not a parallel loader)."""

    def test_guide_reports_missing_and_ready(self):
        import cell_sources as cs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_idx = type('Idx', (), {
                'streets': [],
                'buildings': [],
                'areas': [],
                'covered': lambda self, x, z: False,
            })()
            with mock.patch.object(cs, 'ROOT', root), \
                    mock.patch.object(vmc, 'ROOT', root), \
                    mock.patch.object(cs, 'osm_summary', return_value={
                        'bbox_mc': [0, 0, 127, 127],
                        'osm_covered_centre': False,
                        'streets': [],
                        'buildings': 0,
                        'areas': [],
                        'osm_tree_or_forest_polygons': 0,
                    }):
                missing = cs.vector_map_guide_lines(0, 0, idx=fake_idx)
                self.assertTrue(any('MISSING' in line for line in missing))
                self.assertTrue(any('osm_root=' in line for line in missing))

                _write_clip(vmc.reference_dir(root), 'place')
                ready = cs.vector_map_guide_lines(0, 0, idx=fake_idx)
                self.assertTrue(any('HAVE' in line and 'geojson' in line for line in ready))


if __name__ == '__main__':
    unittest.main()

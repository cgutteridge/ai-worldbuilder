#!/usr/bin/env python3
"""Tests for evidence contact sheet production and result recording (task #17 / Q-202)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import evidence_sheet as E


class CameraTest(unittest.TestCase):
    def test_portal_facing_stands_outside_looking_in(self):
        eye, look = E.portal_facing_camera(10, 0, 0, 0, eye_y=30, stand_back=10)
        self.assertGreater(eye[0], 10)
        self.assertEqual(look[0], 10)
        self.assertEqual(eye[1], 30)

    def test_topdown_centres_above_bbox(self):
        eye, look = E.topdown_camera((0, 0, 20, 40))
        self.assertAlmostEqual(eye[0], 10)
        self.assertAlmostEqual(eye[2], 20)
        self.assertGreater(eye[1], look[1])


class SheetProductionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.out = self.root / 'verification' / 'site'
        self.out.mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)
        patches = [
            mock.patch.object(E, 'ROOT', self.root),
            mock.patch.object(E, 'OUT', self.out),
            mock.patch.object(E, 'BUILDS', self.root / 'site' / 'builds'),
            mock.patch.object(E, 'PORTAL_PHOTOS', self.root / 'reference' / 'photos' / 'portals'),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _png(self, path: Path, colour=(200, 100, 50)) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (80, 60), colour).save(path)
        return path

    def test_from_dir_writes_sheet_and_records_path(self):
        shots = self.out / 'demo-shots'
        self._png(shots / 'portal-A.png', (255, 0, 0))
        self._png(shots / 'top-down.png', (0, 255, 0))
        self._png(shots / 'aerial-crop.png', (0, 0, 255))
        (self.out / 'demo-result.json').write_text(json.dumps({'planned': 1}))

        sheet = E.build_evidence_sheet('demo', from_dir=shots)
        self.assertTrue(sheet.is_file())
        self.assertEqual(sheet, self.out / 'demo-contact.png')
        data = json.loads((self.out / 'demo-result.json').read_text())
        self.assertEqual(data['contact_sheet'], 'verification/site/demo-contact.png')
        self.assertEqual(E.find_contact_sheet('demo'), sheet)

    def test_capture_pipeline_tiles_portal_topdown_aerial(self):
        captured = []

        def capture(eye, look, dest):
            captured.append((tuple(eye), tuple(look), dest.name))
            return self._png(Path(dest), (120, 120, 120))

        def aerial(bbox, dest):
            self.assertEqual(bbox, (0, 0, 10, 10))
            return self._png(Path(dest), (30, 90, 30))

        portals = [{'id': '9-A', 'x': 10, 'z': 5}]
        sheet = E.build_evidence_sheet(
            'feat',
            portals=portals,
            bbox=(0, 0, 10, 10),
            capture=capture,
            aerial_fn=aerial,
            use_minecraft=True,
        )
        self.assertTrue(sheet.is_file())
        names = [n for _e, _l, n in captured]
        self.assertEqual(names, ['portal-9-A.png', 'top-down.png'])
        data = json.loads((self.out / 'feat-result.json').read_text())
        self.assertIn('contact_sheet', data)
        # Sheet should include portal view, top-down, aerial (3 tiles).
        with Image.open(sheet) as im:
            self.assertGreater(im.width, 80)

    def test_find_contact_sheet_missing_is_none(self):
        self.assertIsNone(E.find_contact_sheet('nope'))

    def test_record_sheet_creates_result_when_absent(self):
        sheet = self._png(self.out / 'x-contact.png')
        rel = E.record_sheet('x', sheet)
        self.assertEqual(rel, 'verification/site/x-contact.png')
        self.assertTrue((self.out / 'x-result.json').is_file())


if __name__ == '__main__':
    unittest.main()

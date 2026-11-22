"""Unit tests for streetview_sample contact-sheet maths (no network)."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import streetview_sample as sv


class TestFrameUrl(unittest.TestCase):
    def test_url_embeds_ll_and_yaw(self):
        u = sv.frame_url(50.93, -1.39, 90, w=900, h=600)
        self.assertIn('yaw=90', u)
        self.assertIn('ll=50.930000,-1.390000', u)
        self.assertIn('w=900&h=600', u)

    def test_thumb_url_shrinks(self):
        u = sv.frame_url(50.93, -1.39, 0)
        t = sv.thumb_url(u)
        self.assertIn(f'w={sv.THUMB_W}&h={sv.THUMB_H}', t)
        self.assertNotIn('w=900&h=600', t)


class TestContactSheet(unittest.TestCase):
    def test_tiles_and_labels(self):
        frames = [
            {'id': 'g00-y0', 'x': 1, 'z': 2, 'yaw': 0},
            {'id': 'g00-y90', 'x': 1, 'z': 2, 'yaw': 90},
            {'id': 'g01-y0', 'x': 3, 'z': 4, 'yaw': 0},
        ]
        imgs = [Image.new('RGB', (sv.THUMB_W, sv.THUMB_H), (i * 40, 80, 120)) for i in range(3)]
        sheet = sv.contact_sheet(frames, imgs, cols=2)
        self.assertEqual(sheet.size, (2 * sv.THUMB_W, 2 * (sv.THUMB_H + 28)))
        self.assertEqual(sheet.getpixel((10, 10)), (0, 80, 120))

    def test_write_sheet_uses_cache_dir(self):
        import io
        import shutil
        import tempfile

        frames = [{'id': 'g00-y0', 'x': 1, 'z': 2, 'yaw': 0, 'url': 'http://example/w=900&h=600'}]
        solid = Image.new('RGB', (sv.THUMB_W, sv.THUMB_H), (10, 20, 30))
        buf = io.BytesIO()
        solid.save(buf, format='JPEG')
        raw = buf.getvalue()
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))

        with mock.patch.object(sv, 'fetch_bytes', return_value=raw), \
             mock.patch.object(sv, 'SHEETS', tmp):
            path = sv.write_sheet(9, 9, frames, '9_9-sheet', cache=True)
            self.assertEqual(path.suffix, '.png')
            self.assertTrue(path.exists())
            self.assertTrue((tmp / '9_9' / 'g00-y0.jpg').exists())


if __name__ == '__main__':
    unittest.main()

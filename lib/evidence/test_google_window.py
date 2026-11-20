"""Offline authorization checks for both Google aerial window modes."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import google_aerial as ga
import google_window as gw


def _projection(points, zoom):
    """Return fictional tile fractions for synthetic MC points.

    @param points: MC points
    @param zoom: unused zoom level
    @returns: one fractional tile pair per point
    """
    del zoom
    return [(x / 40, z / 40) for x, z in points]


class WindowAuthorizationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.sheets = Path(temp.name) / 'sheets'
        for patch in (
            mock.patch.object(ga, 'SHEETS', self.sheets),
            mock.patch.object(ga, 'mc_to_tile', side_effect=_projection),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_window_api_refuses_without_authorization(self):
        with mock.patch.object(ga.urllib.request, 'urlopen') as network:
            with self.assertRaisesRegex(PermissionError, '--download-authorized'):
                gw.window_image(40, 40, 10, 10, 1, lambda *_: None)
        network.assert_not_called()

    def test_both_modes_refuse_before_reading_plan_or_creating_output(self):
        for mode in ('crop', 'plan'):
            with self.subTest(mode=mode), mock.patch.object(ga.urllib.request, 'urlopen') as network, mock.patch(
                'sys.stdout', new_callable=io.StringIO,
            ), mock.patch('sys.stderr', new_callable=io.StringIO) as err:
                with self.assertRaises(SystemExit) as exc:
                    gw.main([mode, '--at', '40,40', '--plan', 'missing.json'])
            self.assertEqual(exc.exception.code, 2)
            self.assertIn('--download-authorized', err.getvalue())
            network.assert_not_called()
            self.assertFalse(self.sheets.exists())

    def test_dry_runs_describe_both_modes_without_fetching(self):
        for mode, prefix in (('crop', 'w'), ('plan', 'p')):
            with self.subTest(mode=mode), mock.patch.object(ga.urllib.request, 'urlopen') as network, mock.patch(
                'sys.stdout', new_callable=io.StringIO,
            ) as out:
                code = gw.main([mode, '--at', '40,40', '--half', '10x10', '--dry-run', '--download-authorized'])
            self.assertEqual(code, 0)
            plan = json.loads(out.getvalue())
            self.assertEqual(plan['output'], str(self.sheets / f'{prefix}_40_40.png'))
            self.assertEqual(plan['source'], ga.BASE)
            self.assertEqual(plan['tile_count'], 4)
            self.assertGreater(plan['estimated_bytes'], 0)
            network.assert_not_called()
            self.assertFalse(self.sheets.exists())

    def test_authorized_window_passes_authorization_to_every_tile(self):
        for mode, prefix in (('crop', 'w'), ('plan', 'p')):
            plan_file = self.sheets.parent / 'plan.json'
            plan_file.write_text(json.dumps({'trees': [], 'skipped': []}))
            with self.subTest(mode=mode), mock.patch.object(
                ga, 'fetch_tile', return_value=Image.new('RGB', (256, 256), 'blue'),
            ) as fetch, mock.patch('sys.stdout', new_callable=io.StringIO):
                code = gw.main([mode, '--at', '40,40', '--half', '10x10', '--ppb', '1',
                                '--plan', str(plan_file), '--download-authorized'])
            self.assertEqual(code, 0)
            self.assertEqual(fetch.call_count, 4)
            self.assertTrue(all(call.kwargs['download_authorized'] for call in fetch.call_args_list))
            self.assertTrue((self.sheets / f'{prefix}_40_40.png').is_file())


if __name__ == '__main__':
    unittest.main()

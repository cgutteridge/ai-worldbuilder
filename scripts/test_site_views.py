"""Named views come from world config and unknown flags are refused."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import site_views


class SiteViewsConfigTest(unittest.TestCase):
    def test_help_prints_and_unknown_flags_are_refused(self):
        script = Path(site_views.__file__)
        help_run = subprocess.run(
            [sys.executable, str(script), '--help'],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(help_run.returncode, 0, help_run.stderr)
        self.assertIn('usage:', help_run.stdout.lower())
        self.assertIn('--world', help_run.stdout)
        self.assertIn('config/views.json', help_run.stdout)
        self.assertNotIn('6396', help_run.stdout)

        bad = subprocess.run(
            [sys.executable, str(script), '--helpb35'],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(bad.returncode, 2)
        self.assertIn('unrecognized arguments', bad.stderr)

    def test_known_flags_match_the_parser(self):
        strings = {
            opt for action in site_views.build_parser()._actions for opt in action.option_strings
        }
        self.assertEqual(strings, site_views._OPTIONS)

    def test_hires_and_midres_together_are_refused(self):
        with self.assertRaises(SystemExit) as caught:
            site_views.build_parser().parse_args(['--world', 'example', '--hires', '--midres'])
        self.assertEqual(caught.exception.code, 2)

    def test_resolution_flags(self):
        self.assertEqual(site_views.downscale_for(), 4)
        self.assertEqual(site_views.downscale_for(hires=True), 1)
        self.assertEqual(site_views.downscale_for(midres=True), 2)
        args = ['--midres', '3', '--hires']
        self.assertEqual(site_views.pop_downscale(args), 1)
        self.assertEqual(args, ['3'])

    def test_load_views_from_a_world_catalogue(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'views.json'
            path.write_text(json.dumps({
                'square': {'eye': [1, 80, 0], 'look': [1, 64, 0]},
                'lane': {'eye': [2, 70, 3], 'look': [4.5, 64, 5]},
            }), encoding='utf-8')
            views = site_views.load_views(path)
        self.assertEqual(list(views), ['square', 'lane'])
        self.assertEqual(views['square'], ([1, 80, 0], [1, 64, 0]))
        self.assertEqual(views['lane'][1], [4.5, 64, 5])

    def test_missing_catalogue_has_no_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(site_views.load_views(Path(tmp) / 'views.json'), {})

    def test_bad_catalogue_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'views.json'
            path.write_text('[1]\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                site_views.load_views(path)
            path.write_text('{"../out": {"eye": [0, 0, 0], "look": [1, 1, 1]}}\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                site_views.load_views(path)
            path.write_text('{"ok": {"eye": [true, 0, 0], "look": [1, 1, 1]}}\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                site_views.load_views(path)

    def test_pictures_stay_under_the_world_views_directory(self):
        world = Path('/worlds/example')
        out = site_views.views_output_dir(world)
        self.assertEqual(out, world / 'verification' / 'site' / 'views')
        views = {'north': ([0, 80, 0], [0, 64, 0])}
        planned = site_views.plan_shots(views, [], 'cam-', out)
        self.assertEqual(planned[0][3], out / 'cam-north.png')
        self.assertEqual(site_views.views_config_path(world), world / 'config' / 'views.json')

    def test_prefix_cannot_be_a_flag_or_a_path(self):
        for prefix in ('--help', '../out', 'a/b'):
            with self.subTest(prefix=prefix):
                with self.assertRaises(SystemExit):
                    site_views.check_prefix(prefix)
        self.assertEqual(site_views.check_prefix(''), '')

    def test_unknown_and_empty_selections_are_refused(self):
        views = {'north': ([0, 0, 0], [1, 1, 1])}
        with self.assertRaises(SystemExit) as unknown:
            site_views.selected_names(views, ['south'])
        self.assertIn('south', str(unknown.exception))
        with self.assertRaises(SystemExit) as empty:
            site_views.selected_names({}, [])
        self.assertIn('config/views.json', str(empty.exception))

    def test_no_hard_coded_camera_table(self):
        text = Path(site_views.__file__).read_text(encoding='utf-8')
        self.assertNotIn('VIEWS =', text)
        for name in ('broadlands', 'portswood', 'highfield', 'susu'):
            self.assertNotIn(name, text.lower())

    def test_removed_clis_are_gone_and_mask_undo_stays_a_library(self):
        scripts = Path(site_views.__file__).resolve().parent
        for name in ('site_audit.py', 'site_index.py', 'mask_undo.py'):
            self.assertFalse((scripts / name).is_file(), name)
        import mask_undo
        self.assertTrue(callable(mask_undo.run))
        self.assertFalse(hasattr(mask_undo, 'main'))


if __name__ == '__main__':
    unittest.main()

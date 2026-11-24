"""Task #475: shop fascia colour from street-level frames (source, runner and loader doubles; no network)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import fascia_read as FR
import row_read as RW
import typology_spec as T
from test_row_read import RETAIL, ROLE, Runner, loaded, pitched_text
from test_typology_spec import STREET, building, rect
import test_roof_read as TR


def setUpModule():
    # no test may reach a street-level provider: the default source list is empty unless a test passes doubles
    global _sources
    _sources = FR.default_sources
    FR.default_sources = lambda: []


def tearDownModule():
    FR.default_sources = _sources


def answer(colour='black', **extra):
    return json.dumps({'visible': True, 'fascia_colour': colour, 'rgb': [20, 20, 20], 'confident': True,
                       'frames': ['kartaview-1'], 'why': 'black board', **extra})


def frame_source(tmp, ids=('kartaview-1',)):
    def source(_tgt, out_dir):
        out = []
        for fid in ids:
            path = Path(tmp) / f'{fid}.jpg'
            Image.new('RGB', (400, 300), (30, 30, 30)).save(path)
            out.append({'id': fid, 'source': 'kartaview', 'path': path, 'crop': (0.25, 0.75)})
        return out
    return source


def read(step, text, tmp, cache=None):
    runner = Runner(text)
    record = RW.read_building('1', 'bricks', loader=lambda _w: loaded(TR.shop), role_id=ROLE, runner=runner,
                              cache_dir=cache, steps=[step])
    return record, runner


class Step(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def step(self, *sources):
        return FR.make_step(list(sources), Path(self.tmp.name) / 'frames')

    def test_the_answer_becomes_a_signs_patch_with_the_frame_ids_as_evidence(self):
        record, runner = read(self.step(frame_source(self.tmp.name)), answer(), self.tmp.name)
        self.assertEqual(record['patch'], {'signs': {'fascia_colour': 'black'}})
        field = record['fields']['fascia_colour']
        self.assertEqual((field['value'], field['rests_on']), ('black', 'street-level imagery'))
        self.assertIn('kartaview-1', field['evidence'])
        self.assertEqual(runner.calls, 1)
        self.assertEqual(record['asks'][0]['key'], 'fascia')

    def test_a_second_run_costs_nothing(self):
        cache = Path(self.tmp.name) / 'cache'
        step = self.step(frame_source(self.tmp.name))
        read(step, answer(), self.tmp.name, cache)
        record, runner = read(step, answer(), self.tmp.name, cache)
        self.assertEqual(runner.calls, 0)
        self.assertEqual(RW.cost(record)['tokens'], 0)

    def test_no_frame_means_no_ask_and_the_class_default_is_said(self):
        record, runner = read(self.step(lambda *_a: []), answer(), self.tmp.name)
        self.assertEqual((runner.calls, record['patch']), (0, {}))
        self.assertIn('class default fascia', ' '.join(record['unsure']))

    def test_a_failing_source_falls_to_the_next(self):
        def broken(*_a):
            raise OSError('offline')
        record, _runner = read(self.step(broken, frame_source(self.tmp.name)), answer(), self.tmp.name)
        self.assertEqual(record['patch']['signs']['fascia_colour'], 'black')
        self.assertIn('failed', ' '.join(record['unsure']))

    def test_an_unsure_or_unseen_answer_keeps_the_class_default(self):
        for text in (answer(confident=False), answer(visible=False), 'no idea'):
            record, _runner = read(self.step(frame_source(self.tmp.name)), text, self.tmp.name)
            self.assertEqual(record['patch'], {}, text)
            self.assertIn('class default fascia', ' '.join(record['unsure']), text)

    def test_a_name_outside_the_catalogue_maps_to_the_nearest_colour_and_says_so(self):
        text = json.dumps({'visible': True, 'fascia_colour': 'charcoal', 'rgb': [28, 24, 26], 'confident': True,
                           'frames': ['kartaview-1']})
        record, _runner = read(self.step(frame_source(self.tmp.name)), text, self.tmp.name)
        self.assertEqual(record['patch']['signs']['fascia_colour'], 'black')
        self.assertTrue(record['fields']['fascia_colour']['flags'])

    def test_the_shop_name_tag_is_the_only_hint_and_no_shop_is_named_in_code(self):
        text = FR.prompt({'name': 'Corner Cafe'}, [{'id': 'a'}], ['black'])
        self.assertIn('Corner Cafe', text)
        self.assertNotIn('the shop named', FR.prompt({}, [{'id': 'a'}], ['black']))


class Frames(unittest.TestCase):
    TGT = {'x': 100.0, 'z': 100.0, 'fx': -1.0, 'fz': 0.0, 'half_width': 4.0, 'depth': 5.0}

    def row(self, rid, x, z, heading):
        return {'id': rid, 'mc': (x, z), 'heading': heading, 'd': 0.0}

    def test_kartaview_takes_street_side_frames_that_face_the_shop_and_crops_to_it(self):
        rows = [self.row('1', 80, 100, 90),     # street side (west), looking east at the shop: kept
                self.row('2', 120, 100, 90),    # behind the shop: dropped
                self.row('3', 80, 100, 270),    # looking away: dropped
                self.row('4', 102, 100, 90)]    # too close: dropped
        saved = []
        with tempfile.TemporaryDirectory() as tmp:
            frames = FR.kartaview_frames(self.TGT, Path(tmp), query=lambda *_a: rows,
                                         fetch=lambda row, path: saved.append(row['id']))
        self.assertEqual([f['id'] for f in frames], ['kartaview-1'])
        self.assertEqual(saved, ['1'])
        left, right = frames[0]['crop']
        self.assertAlmostEqual((left + right) / 2, 0.5, places=2)
        self.assertLess(right - left, 1.0)

    def test_streetview_aims_at_the_shop_from_its_street_side(self):
        urls = []
        with tempfile.TemporaryDirectory() as tmp:
            import io
            buf = io.BytesIO()
            Image.new('RGB', (30, 20)).save(buf, 'JPEG')
            frames = FR.streetview_frames(self.TGT, Path(tmp), fetch=lambda url: urls.append(url) or buf.getvalue(),
                                          to_latlon=lambda x, z: (50.9, -1.3))
        self.assertEqual(len(frames), 2)
        self.assertIn('yaw=90', urls[0])         # west of the shop, looking east
        self.assertTrue(all(f['source'] == 'google-streetview' for f in frames))

    def test_target_uses_the_street_point_when_known(self):
        class Ctx:
            cells = {(x, z) for x in range(10, 18) for z in range(10, 17)}
            front, loaded = 'W', {'street': (5.0, 13.0)}
        tgt = FR.target(Ctx())
        self.assertLess(tgt['fx'], -0.9)
        self.assertGreater(tgt['half_width'], 3.0)


class OptIn(unittest.TestCase):
    def test_the_default_steps_have_no_fascia_step_and_no_source_is_called(self):
        from unittest import mock
        self.assertNotIn(FR.fascia_step, RW.STEPS)
        calls = []
        with mock.patch.object(FR, 'default_sources', lambda: [lambda *a: calls.append(a) or []]):
            record = RW.read_building('1', 'bricks', loader=lambda _w: loaded(TR.shop), role_id=ROLE,
                                      runner=Runner(pitched_text()))
        self.assertEqual(calls, [])
        self.assertNotIn('fascia_colour', record['fields'])

    def test_default_reader_adds_the_fascia_step_only_when_asked(self):
        from unittest import mock
        for flag, expected in ((False, None), (True, [RW.roof_step, RW.roof_colour_step, FR.fascia_step])):
            with mock.patch.object(RW, 'make_reader') as made:
                RW.default_reader(([], []), role_id=ROLE, cache_dir='x', fascia=flag)
            self.assertEqual(made.call_args.kwargs['steps'], expected)

    def test_with_the_fascia_step_the_source_double_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            record, runner = read(FR.make_step([frame_source(tmp)], Path(tmp) / 'f'), answer(), tmp)
        self.assertEqual(record['patch'], {'signs': {'fascia_colour': 'black'}})
        self.assertEqual(runner.calls, 1)


class Generator(unittest.TestCase):
    def test_the_patch_wins_over_the_class_palette_for_that_shop_only(self):
        rows = [building(1, rect(0, 0, 8, 7), dict(RETAIL), lidar=4.0),
                building(2, rect(8, 0, 8, 7), dict(RETAIL), lidar=4.0)]
        evidence = {'1': loaded(TR.shop), '2': loaded(TR.shop)}
        colours = iter([answer(), answer('red', rgb=[150, 50, 50])])

        def runner(cmd, stdin):
            text = next(colours) if 'fascia' in stdin.lower() else pitched_text()
            return Runner(text)(cmd, stdin)
        with tempfile.TemporaryDirectory() as tmp:
            steps = [RW.roof_step, FR.make_step([frame_source(tmp)], Path(tmp) / 'f')]
            reader = RW.make_reader(loader=lambda way: evidence[way], role_id=ROLE, runner=runner, steps=steps)
            report = T.generate(rows, STREET, reader)
        spec = report['specs'][0]['spec']
        self.assertEqual(spec['signs']['a']['fascia_colour'], 'black')
        self.assertEqual(spec['signs']['b']['fascia_colour'], 'red')
        import feature_spec as FS
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'row.json'
            path.write_text(json.dumps(spec))
            FS.load_spec(path)


if __name__ == '__main__':
    unittest.main()

"""Task #474: a shop row is read building by building (loader and runner doubles, no world data)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import fascia_read as FR
import row_read as RW
import test_roof_read as TR
import typology_spec as T
from test_typology_spec import STREET, building, rect

ROLE = 'claude-opus-5-5-low'
RETAIL = {'building': 'retail', 'building:levels': '1'}


def flat_shop(s, t):
    """A flat one-storey roof over the plot, nothing beyond."""
    s, t = np.asarray(s, dtype=float), np.asarray(t, dtype=float)
    return np.where((np.abs(t) <= 2.5) & (s >= -9.5) & (s < 9.5), 3.3, 0.0)


def setUpModule():
    # the fascia step is in STEPS: these tests never reach a street-level provider (test_fascia_read covers it)
    global _sources
    _sources = FR.default_sources
    FR.default_sources = lambda: []


def tearDownModule():
    FR.default_sources = _sources


class Runner:
    """Designer double: answers every ask with a fixed model text and counts the calls."""

    def __init__(self, text=None, fail=False):
        self.text, self.fail, self.calls = text, fail, 0

    def __call__(self, cmd, stdin):
        self.calls += 1
        if self.fail:
            return ''
        return json.dumps({'type': 'result', 'result': self.text, 'total_cost_usd': 0.02,
                           'usage': {'input_tokens': 5, 'cache_creation_input_tokens': 1000,
                                     'cache_read_input_tokens': 0, 'output_tokens': 50}}) + '\n'


def loaded(height, tags=None, no_image=False):
    ev = TR.evidence(height)
    if no_image:
        ev.img = np.full(ev.img.shape, np.nan)
    return {'evidence': ev, 'tags': tags or RETAIL, 'front': 'W', 'cells': TR.CELLS}


def pitched_text():
    return json.dumps(TR.answer())


def flat_text():
    rows = {1: 'flat', 2: 'flat', 3: 'flat', 4: 'flat'}
    return json.dumps({'volumes': [{'id': k, 'form': v, 'roof': 'flat_deck', 'hidden': False, 'storeys': None}
                                   for k, v in rows.items()], 'front': 'none', 'note': ''})


class ReadBuilding(unittest.TestCase):
    def read(self, load, runner, cache=None):
        return RW.read_building('1', 'bricks', loader=lambda _w: load, role_id=ROLE, cache_dir=cache, runner=runner)

    def test_the_read_gives_storeys_and_a_pitched_roof_with_evidence_and_cost(self):
        runner = Runner(pitched_text())
        record = self.read(loaded(TR.shop), runner)
        self.assertEqual(record['status'], 'read')
        self.assertEqual(record['patch']['roofs']['kind'], 'pitched')
        self.assertGreaterEqual(record['patch']['storeys'], 2)
        self.assertIn('rests on', record['fields']['storeys']['evidence'])
        cost = RW.cost(record)
        self.assertEqual((cost['asks'], cost['tokens'], cost['cost_usd']), (1, 1055, 0.02))
        self.assertEqual(record['asks'][0]['model'], 'claude-opus-5-5')
        self.assertIn('way 1: read', RW.summary_line(record))

    def test_a_rerun_with_the_same_plan_and_image_is_cached_and_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = Runner(pitched_text())
            self.read(loaded(TR.shop), runner, Path(tmp))
            again = self.read(loaded(TR.shop), runner, Path(tmp))
        self.assertEqual(runner.calls, 1)
        cost = RW.cost(again)
        self.assertEqual((cost['cached'], cost['tokens'], cost['tokens_when_asked']), (1, 0, 1055))
        self.assertIn('cached', RW.summary_line(again))
        self.assertEqual(again['patch']['roofs']['kind'], 'pitched')

    def test_a_different_plan_is_not_served_from_the_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = Runner(pitched_text())
            self.read(loaded(TR.shop), runner, Path(tmp))
            self.read(loaded(flat_shop), runner, Path(tmp))
        self.assertEqual(runner.calls, 2)

    def test_a_missing_image_makes_no_ask_and_says_so(self):
        runner = Runner(pitched_text())
        record = self.read(loaded(TR.shop, no_image=True), runner)
        self.assertEqual(runner.calls, 0)
        self.assertEqual(record['status'], 'lidar only')
        self.assertTrue(any('no aerial image' in u for u in record['unsure']))
        self.assertIn('lidar only', RW.summary_line(record))

    def test_a_failed_ask_falls_back_to_lidar_and_says_so(self):
        record = self.read(loaded(TR.shop), Runner(fail=True))
        self.assertEqual(record['status'], 'lidar only')
        self.assertTrue(any('designer ask failed' in u for u in record['unsure']))
        self.assertIn('storeys', record['patch'])

    def test_an_unreadable_building_keeps_the_class_default(self):
        def boom(_way):
            raise SystemExit('not in the vector map')
        record = RW.read_building('1', 'bricks', loader=boom, role_id=ROLE)
        self.assertEqual((record['status'], record['patch']), ('unread', {}))
        self.assertIn('class default', record['unsure'][0])

    def test_roof_zones_need_lidar_mass_over_the_builder_threshold(self):
        low = lambda s_, t_: np.where(np.abs(t_) <= 2.5, 2.0, 0.0)          # noqa: E731
        high = loaded(TR.shop)
        ctx = lambda load: RW.Ctx('1', load, 'bricks', ROLE, None, None, Path('.'))  # noqa: E731
        self.assertTrue(RW._zone_mass(ctx(high)))
        self.assertFalse(RW._zone_mass(ctx(loaded(low))))

    def test_a_read_colour_with_no_pitched_triad_falls_back_to_the_class_roof(self):
        from unittest import mock
        real = RW.RR._family
        with mock.patch.object(RW.RR, '_family', side_effect=lambda t: 'smooth_stone' if t != 'grey_slate' else real(t)):
            record = self.read(loaded(TR.shop), Runner(pitched_text()))
        self.assertNotEqual(record['patch']['roofs']['material'], 'smooth_stone')
        self.assertTrue(any('not a pitched roof triad' in u for u in record['unsure']))

    def _coloured(self, rgb255):
        rgb = np.array(rgb255) / 255.0
        ev = loaded(TR.shop)
        ev['evidence'] = TR.evidence(TR.shop, colour=lambda _s, _t, img: img * 0 + rgb)
        return ev

    def test_measured_blue_grey_roof_picks_deepslate_and_writes_evidence(self):
        record = self.read(self._coloured((98, 96, 130)), Runner(pitched_text()))
        field = record['fields']['roof_material']
        self.assertEqual(field['value'], 'deepslate_tile')
        self.assertEqual(record['patch']['roofs']['material'], 'deepslate_tile')
        self.assertEqual(record['patch']['roofs']['kind'], 'pitched')
        self.assertIn('rgb=(98, 96, 130)', field['evidence'])
        self.assertTrue(any('one aerial source' in f for f in field['flags']))
        self.assertEqual(record['patch']['roofs']['stairs'], 'slope')     # the rest of the roof is kept
        self.assertEqual(len(record['asks']), 1)                          # no extra ask

    def test_a_roof_in_leaf_cover_keeps_the_type_material_and_says_so(self):
        from test_roof_read import LEAF
        record = self.read(self._coloured([c * 255 for c in LEAF]), Runner(pitched_text()))
        self.assertNotEqual(record['fields']['roof_material']['value'], 'deepslate_tile')
        self.assertTrue(any('leaf cover' in u for u in record['unsure']))

    def test_a_rejected_answer_is_not_cached_but_a_good_one_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = Runner('not json at all')
            record = self.read(loaded(TR.shop), runner, Path(tmp))
            self.assertTrue(any('designer ask failed' in u for u in record['unsure']))
            self.assertEqual(list(Path(tmp).glob('*.json')), [])
            self.assertEqual(record['asks'][0]['cached'], False)       # the spend is still recorded
            runner.text = pitched_text()
            again = self.read(loaded(TR.shop), runner, Path(tmp))
            self.assertEqual(runner.calls, 2)                          # asked again, not served from cache
            self.assertEqual(again['status'], 'read')
            self.read(loaded(TR.shop), runner, Path(tmp))
            self.assertEqual(runner.calls, 2)                          # the good answer is cached

    def test_a_later_step_plugs_in_with_its_own_cached_ask_and_patch(self):
        def fascia(ctx, record):
            call = ctx.ask('fascia', ctx.work_dir / '1-roof.png', 'colour?')
            record['fields']['fascia'] = {'value': call['text'], 'rests_on': 'image', 'flags': [], 'evidence': 'x'}
            record['patch']['signs'] = {'text_colour': 'white'}
        runner = Runner('lime')
        record = RW.read_building('1', 'bricks', loader=lambda _w: loaded(TR.shop), role_id=ROLE, runner=runner,
                                  steps=[RW.roof_step, fascia])
        runner.text = 'lime'
        self.assertEqual(len(record['asks']), 2)
        self.assertEqual(record['patch']['signs'], {'text_colour': 'white'})


class Generator(unittest.TestCase):
    """Through ``typology_spec.generate``: each shop of a row keeps its own read."""

    def row(self):
        return [building(1, rect(0, 0, 8, 7), dict(RETAIL), lidar=4.0),
                building(2, rect(8, 0, 8, 7), dict(RETAIL), lidar=4.0)]

    def test_each_shop_takes_its_own_storeys_and_roof_never_a_neighbours(self):
        evidence = {'1': loaded(TR.shop), '2': loaded(flat_shop)}
        texts = {'1': pitched_text(), '2': flat_text()}
        asked = []

        def runner(cmd, stdin):
            way = '1' if len(asked) == 0 else '2'
            asked.append(way)
            return Runner(texts[way])(cmd, stdin)
        reader = RW.make_reader(loader=lambda way: evidence[way], role_id=ROLE, runner=runner)
        report = T.generate(self.row(), STREET, reader)
        spec = report['specs'][0]['spec']
        self.assertEqual(spec['roofs']['a']['kind'], 'pitched')
        self.assertEqual(spec['roofs']['b']['kind'], 'flat')
        self.assertGreater(spec['storeys']['a'], spec['storeys']['b'])
        self.assertIn('a', spec['zones'])
        import feature_spec as FS
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'row.json'
            path.write_text(json.dumps(spec))
            FS.load_spec(path)              # the read spec validates as a feature spec
        self.assertNotIn('b', spec['zones'])
        self.assertEqual(len(report['reads']), 2)
        self.assertEqual(len(report['read_summary']), 3)
        self.assertIn('read total: 2 building(s), 2 ask(s) (0 cached), 2110 tokens', report['read_summary'][-1])
        self.assertIn('own read', spec['notes'])

    def test_the_measured_colour_reaches_each_shops_own_roof_material_in_the_spec(self):
        def coloured(rgb255):
            ev = loaded(TR.shop)
            ev['evidence'] = TR.evidence(TR.shop, colour=lambda _s, _t, img: img * 0 + np.array(rgb255) / 255.0)
            return ev
        evidence = {'1': coloured((98, 96, 130)), '2': coloured((165, 100, 85))}
        reader = RW.make_reader(loader=lambda way: evidence[way], role_id=ROLE, runner=Runner(pitched_text()))
        report = T.generate(self.row(), STREET, reader)
        spec = report['specs'][0]['spec']
        self.assertEqual((spec['roofs']['a']['material'], spec['roofs']['b']['material']), ('deepslate_tile', 'brick'))
        self.assertIn('rgb=(98, 96, 130)', report['reads'][0]['fields']['roof_material']['evidence'])
        self.assertIn('roof_material deepslate_tile (image (measured colour))', report['read_summary'][0])

    def test_the_roof_note_names_ways_that_were_not_read(self):
        def loader(way):
            if way == '2':
                raise SystemExit('not in the vector map')
            return loaded(TR.shop)
        reader = RW.make_reader(loader=loader, role_id=ROLE, runner=Runner(pitched_text()))
        notes = T.generate(self.row(), STREET, reader)['specs'][0]['spec']['notes']
        self.assertIn('where read', notes)
        self.assertIn('Not read, class or district default: ways 2.', notes)

    def test_no_reader_leaves_the_row_as_before(self):
        spec = T.generate(self.row(), STREET)['specs'][0]['spec']
        self.assertNotIn('zones', spec)
        self.assertNotIn('reads', T.generate(self.row(), STREET))

    def test_a_shop_with_no_image_is_lidar_only_and_the_summary_says_so(self):
        evidence = {'1': loaded(TR.shop, no_image=True), '2': loaded(TR.shop)}
        reader = RW.make_reader(loader=lambda way: evidence[way], role_id=ROLE, runner=Runner(pitched_text()))
        report = T.generate(self.row(), STREET, reader)
        self.assertIn('lidar only', report['read_summary'][0])
        self.assertIn('no aerial image', report['read_summary'][0])
        self.assertIn('way 2: read', report['read_summary'][1])

    def test_the_cli_reads_by_default_and_no_read_skips_it(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        evidence = {'1': loaded(TR.shop), '2': loaded(TR.shop)}
        reader = RW.make_reader(loader=lambda way: evidence[way], role_id=ROLE, runner=Runner(pitched_text()))
        index = mock.Mock()
        with mock.patch('osm_footprints.OSMIndex', return_value=index), \
                mock.patch.object(T, 'load_buildings', return_value=(self.row(), STREET)), \
                mock.patch.object(RW, 'default_reader', return_value=reader) as default:
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(T.main(['--ways', '1', '2']), 0)
            self.assertEqual(len(json.loads(out.getvalue())['read_summary']), 3)
            with redirect_stdout(io.StringIO()) as skipped:
                T.main(['--ways', '1', '2', '--no-read'])
            self.assertEqual(default.call_count, 1)
            self.assertNotIn('read_summary', skipped.getvalue())

    def test_a_shop_row_on_the_terrace_body_path_is_read_per_building_instead(self):
        import test_terrace_body as TB
        cells = TB.FOOTPRINT
        shop = building(1, cells, {'building': 'retail', 'building:levels': '3'}, lidar=8.0)
        shop['rises'] = TB.height_field(cells, 'S', 6.5, 10.0, 3.5)
        street = [[(0.0, 36.0), (40.0, 36.0)]]
        self.assertEqual(T.generate([shop], street)['specs'][0]['builder'], 'house_build')
        reader = RW.make_reader(loader=lambda _w: loaded(TR.shop), role_id=ROLE, runner=Runner(pitched_text()))
        item = T.generate([shop], street, reader)['specs'][0]
        self.assertEqual(item['builder'], 'feature_build')
        self.assertEqual(item['spec']['roofs']['a']['kind'], 'pitched')
        self.assertIn('zones', item['spec'])

    def test_houses_are_not_read(self):
        houses = [building(i + 1, rect(i * 5, 0, 5, 8), {'building': 'house', 'building:levels': '2'}, profile=None)
                  for i in range(2)]
        called = []
        report = T.generate(houses, STREET, lambda row, streets: called.append(row) or {})
        self.assertEqual((called, 'reads' in report), ([], False))


if __name__ == '__main__':
    unittest.main()

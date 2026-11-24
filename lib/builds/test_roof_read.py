"""Task #459: read step for roof volumes. Synthetic plots and staged damage, no world data."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import numpy as np

import designer_ask as A
import roof_read as R

# A 20 x 5 plot along x, street to the west: s = x + 0.5 - 10 (street end negative), t = z + 0.5 - 2.5.
CELLS = {(x, z) for x in range(20) for z in range(5)}
STREET = (-10.0, 2.5)
ROOF = (0.45, 0.45, 0.5)
LEAF = (0.25, 0.42, 0.18)
DEFAULTS = {'form': 'flat', 'roof': {'flat': 'flat_deck', 'pitched': 'grey_slate'}, 'storeys': 2}


def shop(s, t):
    """Flat front, a ridge across the plot, a rear wing falling across it, and a bin store beyond the way.

    The neighbours either side (|t| > 2.5) share the main range only.
    """
    s, t = np.asarray(s, dtype=float), np.asarray(t, dtype=float)
    h = np.zeros(np.broadcast(s, t).shape)
    own = np.abs(t) <= 2.5
    h = np.where(own & (s >= -9.5) & (s < -7.5), 3.3, h)
    main = (s >= -7.5) & (s < 2.0) & (np.abs(t) <= 9)
    h = np.where(main, 9.0 - 0.6 * np.abs(s + 2.75), h)
    h = np.where(own & (s >= 2.0) & (s < 9.75), 7.0 - 0.6 * (t + 2.0), h)
    h = np.where((np.abs(t) <= 1.0) & (s >= 9.75) & (s < 16.0), 2.6, h)
    return h


def evidence(height=shop, colour=None, cells=CELLS, street=STREET):
    """Evidence in the plot frame from closed-form height and colour functions of (s, t)."""
    frame = R.plot_frame(cells, street)
    s, t, band = R.profile_axes(frame)
    ss, tt = np.meshgrid(s, t, indexing='ij')
    h = height(ss, tt)
    columns = {}
    for x in range(-12, 40):
        for z in range(-12, 18):
            cs, ct = frame.plot(x + 0.5, z + 0.5)
            columns[(x, z)] = (float(cs), float(ct), float(height(cs, ct)))
    img_s = np.arange(s[0], s[-1] + 1e-9, R.CROP_M_PER_PX)[::-1]
    img_t = np.arange(t[0] - 0.5, t[-1] + 0.5 + 1e-9, R.CROP_M_PER_PX)
    gs, gt = np.meshgrid(img_s, img_t, indexing='ij')
    img = np.zeros(gs.shape + (3,)) + np.array(ROOF)
    if colour is not None:
        img = colour(gs, gt, img)
    return R.Evidence(frame, s, t, h, band, columns, img, img_s, img_t)


def canopy(base, s0, s1, rise=5.0, noisy=True, t_reach=3.0):
    """Staged tree crown over ``s0..s1``: a dome, with texture and gaps when ``noisy``."""
    rng = np.random.default_rng(7)

    def height(s, t):
        h = np.asarray(base(s, t), dtype=float)
        s_, t_ = np.broadcast_arrays(np.asarray(s, dtype=float), np.asarray(t, dtype=float))
        inside = (s_ >= s0) & (s_ < s1) & (np.abs(t_) <= t_reach)
        bump = rise * np.ones(h.shape)
        if noisy:
            bump = bump + rng.normal(0, 0.9, h.shape)
            bump = np.where(rng.random(h.shape) < 0.25, 0.1 * bump, bump)
        return np.where(inside, h + bump, h)
    return height


def leaves(s0, s1, t_reach=3.0):
    def colour(gs, gt, img):
        inside = (gs >= s0) & (gs < s1) & (np.abs(gt) <= t_reach)
        img[inside] = LEAF
        return img
    return colour


def shadow(s0, s1):
    def colour(gs, gt, img):
        img[(gs >= s0) & (gs < s1)] *= 0.35
        return img
    return colour


def answer(**over):
    rows = {1: 'flat', 2: 'gable_across', 3: 'mono_across', 4: 'flat'}
    data = {'volumes': [{'id': k, 'form': v, 'roof': 'flat_deck' if v == 'flat' else 'grey_tile',
                         'hidden': False, 'storeys': None} for k, v in rows.items()],
            'front': 'bay', 'note': ''}
    data.update(over)
    return data


def run(ev, ans=None, **kw):
    vols = R.measure(ev, R.segment(ev))
    parsed = R.parse_answer(json.dumps(ans), vols, R.roof_types()) if ans else None
    return R.read(ev, parsed, defaults=DEFAULTS, **kw)


class FrameTest(unittest.TestCase):
    def test_axis_points_away_from_the_street(self):
        frame = R.plot_frame(CELLS, STREET)
        self.assertAlmostEqual(abs(frame.ux), 1.0)
        self.assertGreater(frame.ux, 0)          # street west, s grows to the east
        frame = R.plot_frame(CELLS, (40.0, 2.5))
        self.assertLess(frame.ux, 0)
        self.assertAlmostEqual(frame.s_min, -9.5)

    def test_rotated_plot_round_trips(self):
        cells = {(round(10 + k * 0.83 + j * -0.56), round(10 + k * 0.56 + j * 0.83))
                 for k in range(20) for j in range(5)}
        frame = R.plot_frame(cells, (0.0, 0.0))
        s, t = frame.plot(*frame.world(3.0, -1.5))
        self.assertAlmostEqual(float(s), 3.0)
        self.assertAlmostEqual(float(t), -1.5)
        self.assertEqual(len(frame.polygon(0, 1, 0, 1)), 4)


class SegmentTest(unittest.TestCase):
    def test_clean_shop_reads_four_volumes(self):
        vols = R.measure(evidence(), R.segment(evidence()))
        self.assertEqual([v.kind for v in vols], ['flat', 'ridge_across', 'cross', 'flat'])
        self.assertEqual([v.beyond for v in vols], [False, False, False, True])
        self.assertEqual(vols[1].form, 'gable_across')
        self.assertEqual(vols[2].form, 'mono_across')
        self.assertAlmostEqual(vols[1].top_m, 9.0, delta=0.3)
        self.assertAlmostEqual(vols[3].top_m, 2.6, delta=0.1)

    def test_a_building_across_an_empty_yard_is_not_a_volume(self):
        def yard(s, t):
            h = shop(s, t)
            h = np.where((np.asarray(s) >= 9.75) & (np.asarray(s) < 12.0), 0.0, h)
            return h
        vols = R.segment(evidence(yard))
        self.assertEqual(len(vols), 3)
        self.assertFalse(any(v.beyond for v in vols))

    def test_hip_is_one_volume(self):
        def hip(s, t):
            s, t = np.asarray(s, dtype=float), np.asarray(t, dtype=float)
            top = 8.0 - 0.6 * np.maximum(np.abs(s) - 3.0, 0) - 0.6 * np.abs(t)
            return np.where((np.abs(s) <= 8.0) & (np.abs(t) <= 2.5), top, 0.0)
        cells = {(x, z) for x in range(2, 18) for z in range(5)}
        vols = R.measure(evidence(hip, cells=cells), R.segment(evidence(hip, cells=cells)))
        self.assertEqual([v.kind for v in vols], ['hip'])

    def test_clean_roofs_are_smooth_and_a_textured_crown_is_rough(self):
        clean = R.measure(evidence(), R.segment(evidence()))
        self.assertLess(clean[1].rough_m, R.ROUGH_MAX_M)
        crowned = evidence(canopy(shop, -6, 1))
        vols = R.measure(crowned, R.segment(crowned))
        main = max((v for v in vols if not v.beyond), key=lambda v: v.s1 - v.s0)
        self.assertGreater(main.rough_m, R.ROUGH_MAX_M)


class TrustTest(unittest.TestCase):
    def _vol(self, result, n):
        return next(v for v in result['volumes'] if v['id'] == n)

    def test_clean_evidence_raises_no_degraded_flag(self):
        result = run(evidence(), answer())
        main = self._vol(result, 2)['trust']
        self.assertEqual(main['height']['rests_on'], 'lidar')
        self.assertEqual((main['height']['lidar'], main['height']['row']), ('agree', 'agree'))
        self.assertEqual((main['form']['lidar'], main['form']['image'], main['form']['row']),
                         ('agree', 'agree', 'agree'))
        self.assertEqual(main['storeys']['value'], 2)
        self.assertEqual(main['colour']['value'], 'grey_tile')
        self.assertEqual(main['colour']['measured'], 'agree')
        self.assertFalse([f for f in main['flags'] if 'rough' in f or 'green' in f or 'shadow' in f])
        self.assertEqual(result['front_volume'], 2)
        self.assertEqual(main['front']['value'], 'bay')

    def test_rough_canopy_on_both_sources_falls_back_to_the_row(self):
        ev = evidence(canopy(shop, -6, 1), leaves(-6, 1))
        result = run(ev, answer())
        main = self._vol(result, result['main'])['trust']
        self.assertEqual(main['height']['rests_on'], 'row')
        self.assertEqual(main['height']['lidar'], 'silent')
        self.assertEqual(main['form']['image'], 'silent')
        self.assertAlmostEqual(main['height']['value']['top_m'], 9.0, delta=0.5)
        self.assertTrue(any('lidar rough' in f for f in main['flags']))
        self.assertTrue(any('image green' in f for f in main['flags']))

    def test_smooth_crown_passes_roughness_but_green_and_the_row_make_lidar_suspect(self):
        ev = evidence(canopy(shop, -6, 1, rise=4.0, noisy=False), leaves(-6, 1))
        result = run(ev, answer())
        main = self._vol(result, result['main'])['trust']
        self.assertTrue(any('lidar suspect' in f for f in main['flags']))
        self.assertEqual(main['height']['rests_on'], 'row')

    def test_smooth_crown_without_green_keeps_lidar_and_records_the_row_disagreeing(self):
        ev = evidence(canopy(shop, -6, 1, rise=4.0, noisy=False))
        main = self._vol(run(ev, answer()), 2)['trust']
        self.assertEqual(main['height']['rests_on'], 'lidar')
        self.assertEqual(main['height']['row'], 'disagree')

    def test_both_degraded_and_no_row_is_the_class_default(self):
        ev = evidence(canopy(shop, 3, 9), leaves(3, 9))
        rear = self._vol(run(ev, answer()), 3)['trust']
        self.assertEqual(rear['height']['rests_on'], 'class default')
        self.assertEqual(rear['form']['rests_on'], 'class default')
        self.assertEqual(rear['form']['value'], 'flat')
        self.assertTrue(any('unread' in f for f in rear['flags']))

    def test_shadow_silences_the_image_only(self):
        ev = evidence(colour=shadow(-11, -7))
        front = self._vol(run(ev, answer()), 1)['trust']
        self.assertEqual(front['colour']['rests_on'], 'class default')
        self.assertEqual(front['colour']['image'], 'silent')
        self.assertEqual(front['height']['rests_on'], 'lidar')
        self.assertTrue(any('shadow' in f for f in front['flags']))

    def test_model_disagreeing_with_clean_lidar_is_recorded_not_followed(self):
        result = run(evidence(), answer(volumes=[{'id': 2, 'form': 'gable_along', 'roof': 'grey_tile'}]))
        main = self._vol(result, 2)['trust']
        self.assertEqual(main['form']['value'], 'gable_across')
        self.assertEqual(main['form']['image'], 'disagree')

    def test_a_tag_checks_the_main_volume_storeys(self):
        result = run(evidence(), answer(), tags={'building:levels': '3'})
        self.assertEqual(self._vol(result, 2)['trust']['storeys']['tag'], 'disagree')
        self.assertEqual(self._vol(result, 1)['trust']['storeys']['tag'], 'silent')

    def test_single_source_is_named(self):
        rear = self._vol(run(evidence(), None), 3)['trust']
        self.assertIn('height rests on lidar alone', rear['flags'])


class AnswerTest(unittest.TestCase):
    def test_unknown_values_become_unsure_and_unknown_ids_drop(self):
        vols = R.segment(evidence())
        text = 'Sure: {"volumes": [{"id": 2, "form": "dome", "roof": "thatch", "storeys": true},' \
               ' {"id": 9, "form": "flat"}], "front": "porch"}'
        parsed = R.parse_answer(text, vols, R.roof_types())
        self.assertEqual(parsed['volumes'], {2: {'form': 'unsure', 'roof': 'unsure', 'hidden': False,
                                                 'storeys': None}})
        self.assertEqual(parsed['front'], 'unsure')
        with self.assertRaises(ValueError):
            R.parse_answer('no idea', vols, R.roof_types())

    def test_prompt_carries_the_lidar_table_and_the_types(self):
        vols = R.measure(evidence(), R.segment(evidence()))
        text = R.prompt(vols, R.roof_types())
        self.assertIn('2 | ', text)
        self.assertIn('beyond the mapped outline', text)
        self.assertIn('grey_slate', text)


class ProposalTest(unittest.TestCase):
    def test_proposal_keys_follow_the_volumes(self):
        result = run(evidence(), answer())
        spec = R.proposal(result, name='unit', way_id='99', front='W', wall='bricks')
        self.assertEqual(len(spec['zones']['a']['regions']), 3)
        self.assertEqual(spec['zones']['a']['surface'], 'fit')
        self.assertEqual(spec['roofs']['a'], {'kind': 'pitched', 'material': 'stone_brick',
                                              'stairs': 'slope', 'finish': 'carpet'})
        self.assertEqual(spec['storeys'], {'a': 2})
        store = spec['volumes']['a'][0]
        self.assertEqual((store['kind'], store['storeys']), ('flat', 1))
        self.assertIn('Read step proposal', spec['notes'])

    def test_an_unread_volume_beyond_the_way_is_not_proposed(self):
        ev = evidence(canopy(shop, 9.75, 16, t_reach=1.0), leaves(9.75, 16, t_reach=1.0))
        result = run(ev, answer())
        beyond = [v for v in result['volumes'] if v['beyond']]
        self.assertTrue(beyond)
        self.assertNotEqual(beyond[0]['trust']['height']['rests_on'], 'lidar')
        spec = R.proposal(result, name='unit', way_id='99', front='W', wall='bricks')
        self.assertNotIn('volumes', spec)

    def test_proposal_plans_through_feature_build(self):
        import feature_build as F
        from feature_build_fixtures import _StoneWorld
        import feature_volumes as FV
        result = run(evidence(), answer())
        import feature_spec as FS
        spec = R.proposal(result, name='unit', way_id='99', front='W', wall='bricks')
        spec['floor_min'] = 50
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'unit.json'
            path.write_text(json.dumps(spec))
            spec = FS.load_spec(path)           # the proposal validates as a feature spec
        x0, z0, width, depth = -4, -6, 30, 17
        frame = R.PlotFrame(**result['frame'])
        dsm = np.full((depth, width), 50.0)
        for j in range(depth):
            for i in range(width):
                s, t = frame.plot(x0 + i + 0.5, z0 + j + 0.5)
                dsm[j, i] = 50.0 + float(shop(s, t))
        grids = {'dsm': dsm, 'ground_y': np.full((depth, width), 50), 'x0': x0, 'z0': z0}
        builder = F.Build(spec, footprints={'a': CELLS}, world=_StoneWorld(),
                          zone_data={'a': grids, 'a.v1': grids})
        buf = io.StringIO()
        with mock.patch.object(FV, 'y_to_metres_aod', side_effect=float), redirect_stdout(buf):
            plan = builder.build()
        out = buf.getvalue()
        self.assertIn('a.v1 volume of a:', out)
        self.assertIn('a region 1: surface gable', out)
        self.assertTrue(plan.cells)


class DesignerAskTest(unittest.TestCase):
    def test_role_id_splits_into_model_and_effort(self):
        self.assertEqual(A.role_model('claude-opus-5-5-low'), ('claude-opus-5-5', 'low'))
        self.assertEqual(A.role_model('composer'), ('composer', None))

    def test_ask_returns_the_text_and_the_measured_usage(self):
        seen = {}

        def runner(cmd, stdin):
            seen['cmd'], seen['msg'] = cmd, json.loads(stdin)
            return '\n'.join([
                json.dumps({'type': 'system'}),
                json.dumps({'type': 'result', 'result': '{"volumes": []}', 'total_cost_usd': 0.01,
                            'usage': {'input_tokens': 2, 'cache_creation_input_tokens': 900,
                                      'cache_read_input_tokens': 0, 'output_tokens': 40}}),
            ])
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / 'a.png'
            png.write_bytes(b'\x89PNG')
            got = A.ask(png, 'q', role_id='claude-opus-5-5-low', runner=runner)
        self.assertEqual(got['total_tokens'], 942)
        self.assertEqual((got['model'], got['effort']), ('claude-opus-5-5', 'low'))
        self.assertIn('--tools', seen['cmd'])
        self.assertEqual(seen['msg']['message']['content'][0]['type'], 'image')

    def test_no_result_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / 'a.png'
            png.write_bytes(b'x')
            with self.assertRaises(RuntimeError):
                A.ask(png, 'q', role_id='m', runner=lambda cmd, stdin: '')


if __name__ == '__main__':
    unittest.main()

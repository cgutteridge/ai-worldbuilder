"""Tests for roof_aerial: flat-roof colour and PV arrays from a synthetic registered aerial."""
from __future__ import annotations

import json
import io
from contextlib import redirect_stdout, redirect_stderr
from types import SimpleNamespace
from unittest import mock
import tempfile
import unittest
from pathlib import Path

import numpy as np

import roof_aerial as R

PPB = 5            # aerial pixels per block in the fixture (about z19)
X0, Z0 = 100, 200  # MC min corner of the fixture image
ROOF = (200, 200, 205)
PANEL = (70, 90, 150)
GAP = (215, 215, 220)


def _affine(ppb: float = PPB, x0: int = X0, z0: int = Z0) -> np.ndarray:
    """MC (x, z) -> image (u, v) with no rotation."""
    return np.array([[ppb, 0.0, -ppb * x0], [0.0, ppb, -ppb * z0]])


def _paint(img, box, colour, ppb=PPB):
    """Fill an inclusive MC block box with one colour."""
    x0, z0, x1, z1 = box
    img[(z0 - Z0) * ppb:(z1 - Z0 + 1) * ppb, (x0 - X0) * ppb:(x1 - X0 + 1) * ppb] = colour


def _array(img, box, rows, period_px=7, joint_px=9, ppb=PPB):
    """Dark panel grid: a 2 px gap every ``period_px`` between rows, a 1 px joint every ``joint_px`` in a row."""
    x0, z0, x1, z1 = box
    v0, v1 = (z0 - Z0) * ppb, (z1 - Z0 + 1) * ppb
    u0, u1 = (x0 - X0) * ppb, (x1 - X0 + 1) * ppb
    img[v0:v1, u0:u1] = PANEL
    if rows == 'x':      # rows run along x: row gaps are horizontal in the image
        for v in range(v0, v1, period_px):
            img[v:v + 2, u0:u1] = GAP
        for u in range(u0, u1, joint_px):
            img[v0:v1, u] = GAP
    else:
        for u in range(u0, u1, period_px):
            img[v0:v1, u:u + 2] = GAP
        for v in range(v0, v1, joint_px):
            img[v, u0:u1] = GAP


def _fixture(shift=(0, 0), ppb=PPB):
    """60x50-block aerial: light roof on a 50x40 footprint, two PV arrays, a blue tarp, plant.

    @param shift: blocks the roof appears displaced in the image (aerial lean)
    @returns: (rgb uint8 image, footprint set, truth dict)
    """
    rng = np.random.default_rng(7)
    img = np.zeros((50 * ppb, 60 * ppb, 3), np.uint8)
    img[:] = (90, 110, 80)                               # grass round the building
    foot = {(x, z) for x in range(105, 155) for z in range(205, 245)}
    sx, sz = shift
    _paint(img, (105 + sx, 205 + sz, 154 + sx, 244 + sz), ROOF, ppb)
    noise = rng.integers(-6, 7, img.shape)
    img = np.clip(img.astype(int) + noise, 0, 255).astype(np.uint8)
    truth = {
        'a': (110, 210, 129, 221),   # rows along x (long axis x)
        'b': (135, 210, 146, 235),   # rows along z (long axis z)
    }
    _array(img, (110 + sx, 210 + sz, 129 + sx, 221 + sz), 'x', ppb=ppb)
    _array(img, (135 + sx, 210 + sz, 146 + sx, 235 + sz), 'z', ppb=ppb)
    _paint(img, (110 + sx, 228 + sz, 121 + sx, 238 + sz), PANEL, ppb)    # uniform blue tarp: no rows
    for v in range((241 + sz - Z0) * ppb, (244 + sz - Z0) * ppb, 7):      # one-way blue stripes: not a grid
        img[v:v + 2, (110 + sx - X0) * ppb:(125 + sx - X0) * ppb] = (60, 80, 150)
    plant = rng.integers(120, 255, (8 * ppb, 6 * ppb, 1)).repeat(3, axis=2)
    pv0, pu0 = (230 + sz - Z0) * ppb, (147 + sx - X0) * ppb
    img[pv0:pv0 + 8 * ppb, pu0:pu0 + 6 * ppb] = plant                  # irregular plant: no rows
    return img, foot, truth


def _near(box, truth, tol=1):
    return all(abs(a - b) <= tol for a, b in zip(box, truth))


class DeriveTest(unittest.TestCase):
    """Seam: derive(rgb, affine, footprint) -> roof colour + PV arrays."""

    def test_finds_each_array_with_its_row_axis(self):
        img, foot, truth = _fixture()
        out = R.derive(img, _affine(), foot)
        boxes = {a['rows']: a for a in out['pv_arrays']}
        self.assertEqual(len(out['pv_arrays']), 2, out['pv_arrays'])
        self.assertTrue(_near(boxes['x']['box'], truth['a']), boxes['x'])
        self.assertTrue(_near(boxes['z']['box'], truth['b']), boxes['z'])
        for a in out['pv_arrays']:
            self.assertAlmostEqual(a['row_pitch_m'], 7 / PPB, delta=0.25)
            self.assertEqual(a['pitch'], 2)
            self.assertTrue(a['source'])

    def test_uniform_blue_and_irregular_plant_are_not_pv(self):
        img, foot, _ = _fixture()
        out = R.derive(img, _affine(), foot)
        for a in out['pv_arrays']:
            x0, z0, x1, z1 = a['box']
            self.assertFalse(x0 <= 115 <= x1 and z0 <= 233 <= z1, 'tarp read as PV')
            self.assertFalse(x0 <= 150 <= x1 and z0 <= 234 <= z1, 'plant read as PV')
            self.assertFalse(z1 >= 241, 'one-way stripes read as PV')

    def test_roof_colour_ignores_pv_and_suggests_a_light_flat_roof_material(self):
        img, foot, _ = _fixture()
        out = R.derive(img, _affine(), foot)
        for got, want in zip(out['roof_rgb'], ROOF):
            self.assertAlmostEqual(got, want, delta=8)
        self.assertEqual(out['material'], 'polished_diorite')

    def test_lean_shift_puts_arrays_back_on_the_footprint(self):
        img, foot, truth = _fixture(shift=(3, -2))
        out = R.derive(img, _affine(), foot, shift=(3, -2))
        boxes = sorted(a['box'] for a in out['pv_arrays'])
        self.assertEqual(len(boxes), 2)
        self.assertTrue(_near(boxes[0], truth['a']), boxes)
        self.assertTrue(_near(boxes[1], truth['b']), boxes)

    def test_arrays_are_clipped_to_the_footprint(self):
        img, _, _ = _fixture()
        foot = {(x, z) for x in range(105, 140) for z in range(205, 245)}   # cuts array b at x=139
        out = R.derive(img, _affine(), foot)
        for a in out['pv_arrays']:
            x0, z0, x1, z1 = a['box']
            self.assertTrue(105 <= x0 <= x1 <= 139 and 205 <= z0 <= z1 <= 244, a)
        cut = [a for a in out['pv_arrays'] if a['box'][0] >= 133]
        self.assertEqual(len(cut), 1)
        self.assertEqual(cut[0]['box'][2], 139)

    def test_reports_full_coverage_of_the_footprint(self):
        img, foot, _ = _fixture()
        self.assertEqual(R.derive(img, _affine(), foot)['coverage'], 1.0)

    def test_footprint_mostly_off_the_aerial_is_refused(self):
        img, foot, _ = _fixture()
        # a wrong registration: the roof lands half off the image
        with self.assertRaisesRegex(ValueError, 'covers only'):
            R.derive(img, _affine(x0=X0 + 30), foot)

    def test_footprint_entirely_off_the_aerial_is_refused(self):
        img, foot, _ = _fixture()
        with self.assertRaisesRegex(ValueError, 'covers only 0%'):
            R.derive(img, _affine(x0=X0 + 500, z0=Z0 + 500), foot)

    def test_too_coarse_aerial_is_refused(self):
        img, foot, _ = _fixture()
        with self.assertRaisesRegex(ValueError, 'too coarse'):
            R.derive(img, _affine(ppb=2.0), foot)

    def test_rotated_registration_reports_rotation(self):
        img, foot, _ = _fixture()
        out = R.derive(img, _affine(), foot)
        self.assertAlmostEqual(out['rotation_deg'], 0.0, places=6)
        self.assertAlmostEqual(out['px_per_block'], PPB)


class HelpersTest(unittest.TestCase):
    """Seam: pure helpers used by derive."""

    def test_period_score_finds_the_lag(self):
        p = np.tile([0, 0, 0, 0, 0, 1, 1], 6).astype(float)
        score, lag = R.period_score(p, 4, 10)
        self.assertEqual(lag, 7)
        self.assertGreater(score, 0.8)

    def test_period_score_is_low_for_flat_or_noisy_profiles(self):
        self.assertEqual(R.period_score(np.full(30, 5.0), 4, 10)[0], 0.0)
        noise = np.random.default_rng(1).normal(size=40)
        self.assertLess(R.period_score(noise, 4, 10)[0], 0.5)

    def test_membrane_colour_is_the_dominant_surface_not_the_median(self):
        light = [(200, 202, 210)] * 50
        mixed = [(60, 60, 70)] * 30 + [(120, 125, 140)] * 15 + light
        got = R.membrane_colour(mixed)
        for a, b in zip(got, (200, 202, 210)):
            self.assertAlmostEqual(a, b, delta=2)

    def test_grow_box_recovers_window_eroded_edges_and_stops_at_roof(self):
        panel, roof = (90, 105, 160), (200, 200, 205)
        colour = {(x, z): roof for x in range(0, 30) for z in range(0, 20)}
        for x in range(5, 20):
            for z in range(4, 14):
                colour[(x, z)] = panel
        got = R.grow_box((7, 6, 17, 11), colour, panel, roof, set(colour))
        self.assertEqual(got, (5, 4, 19, 13))
        # never more than max_grow lines a side
        self.assertEqual(R.grow_box((9, 8, 15, 10), colour, panel, roof, set(colour), max_grow=1), (8, 7, 16, 11))

    def test_grow_box_does_not_cross_into_another_box(self):
        panel, roof = (90, 105, 160), (200, 200, 205)
        colour = {(x, z): panel for x in range(0, 20) for z in range(0, 5)}
        got = R.grow_box((2, 1, 6, 3), colour, panel, roof, set(colour), taken={(x, z) for x in range(9, 20) for z in range(0, 5)})
        self.assertEqual(got, (0, 0, 8, 4))

    def test_nearest_flat_roof_material(self):
        self.assertEqual(R.nearest_roof_material((160, 160, 160)), 'smooth_stone')
        self.assertEqual(R.nearest_roof_material((44, 39, 47)), 'polished_blackstone')


class SpecPatchTest(unittest.TestCase):
    """Seam: write the derived roof into a feature spec's roofs[label]."""

    def test_patch_sets_material_and_arrays_keeping_other_roof_keys(self):
        spec = {'roofs': {'WS': {'kind': 'flat', 'material': 'smooth_stone',
                                 'edge': {'block': 'gray_concrete', 'where': 'outer'}}}}
        derived = {'material': 'polished_diorite', 'pv_arrays': [
            {'box': [1, 2, 3, 4], 'rows': 'x', 'pitch': 2, 'source': 's',
             'row_pitch_m': 1.4, 'score': 0.7, 'cells': 9}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'w1.json'
            path.write_text(json.dumps(spec))
            R.patch_spec(path, 'WS', derived)
            roof = json.loads(path.read_text())['roofs']['WS']
        self.assertEqual(roof['material'], 'polished_diorite')
        self.assertEqual(roof['edge'], {'block': 'gray_concrete', 'where': 'outer'})
        self.assertEqual(roof['pv_arrays'], [{'box': [1, 2, 3, 4], 'rows': 'x', 'pitch': 2, 'source': 's'}])

    def test_patch_refuses_unknown_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'w1.json'
            path.write_text(json.dumps({'roofs': {'A': {'kind': 'flat', 'material': 'stone'}}}))
            with self.assertRaisesRegex(SystemExit, 'no roofs'):
                R.patch_spec(path, 'B', {'material': 'stone', 'pv_arrays': []})


class SpecPatchGuardTest(unittest.TestCase):
    """Seam: patch_spec never wipes or corrupts a spec."""

    ARRAY = {'box': [1, 2, 3, 4], 'rows': 'x', 'pitch': 2, 'source': 'new', 'row_pitch_m': 1.4, 'score': 0.7, 'cells': 9}
    HAND = {'box': [9, 9, 12, 12], 'rows': 'z', 'pitch': 2, 'source': 'hand-added'}

    def run_patch(self, roof, derived, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'w1.json'
            text = json.dumps({'roofs': {'WS': roof}})
            path.write_text(text)
            try:
                R.patch_spec(path, 'WS', derived, **kw)
            finally:
                after = path.read_text()
        return text, after

    def test_refuses_a_pitched_roof_and_leaves_the_file(self):
        roof = {'kind': 'pitched', 'material': 'brick'}
        with self.assertRaisesRegex(SystemExit, 'flat'):
            self.run_patch(roof, {'material': 'smooth_stone', 'pv_arrays': [self.ARRAY]})

    def test_no_arrays_found_writes_nothing(self):
        roof = {'kind': 'flat', 'material': 'smooth_stone', 'pv_arrays': [self.HAND]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'w1.json'
            path.write_text(json.dumps({'roofs': {'WS': roof}}))
            before = path.read_text()
            with self.assertRaisesRegex(SystemExit, 'no PV arrays'):
                R.patch_spec(path, 'WS', {'material': 'polished_diorite', 'pv_arrays': []}, replace=True)
            self.assertEqual(path.read_text(), before)

    def test_existing_arrays_need_replace(self):
        roof = {'kind': 'flat', 'material': 'smooth_stone', 'pv_arrays': [self.HAND]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'w1.json'
            path.write_text(json.dumps({'roofs': {'WS': roof}}))
            before = path.read_text()
            with self.assertRaisesRegex(SystemExit, 'replace'):
                R.patch_spec(path, 'WS', {'material': 'stone', 'pv_arrays': [self.ARRAY]})
            self.assertEqual(path.read_text(), before)
            R.patch_spec(path, 'WS', {'material': 'stone', 'pv_arrays': [self.ARRAY]}, replace=True)
            got = json.loads(path.read_text())['roofs']['WS']
        self.assertEqual([a['source'] for a in got['pv_arrays']], ['new'])
        self.assertEqual(got['material'], 'stone')


class CrossCheckTest(unittest.TestCase):
    """Seam: the CLI refuses an image that does not fit its registration or mosaic grid."""

    def test_registration_must_name_the_image_and_fit_it(self):
        reg = {'image': 'aerial/site/roof.png', 'ties': [{'mc': [0, 0], 'px': [10, 10]}, {'mc': [5, 5], 'px': [90, 60]}]}
        R.check_registration(reg, Path('/x/roof.png'), (100, 80))
        with self.assertRaisesRegex(SystemExit, 'names'):
            R.check_registration(reg, Path('/x/other.png'), (100, 80))
        with self.assertRaisesRegex(SystemExit, 'outside'):
            R.check_registration(reg, Path('/x/roof.png'), (50, 50))

    def test_mosaic_size_and_name_must_match_the_tile_grid(self):
        R.check_mosaic(Path('mosaic-z19-4x4-260093-175744.png'), (1024, 1024), '19,260093,175744')
        R.check_mosaic(Path('crop.png'), (512, 768), '19,1,2')          # unnamed: size only
        with self.assertRaisesRegex(SystemExit, '256'):
            R.check_mosaic(Path('crop.png'), (500, 512), '19,1,2')
        with self.assertRaisesRegex(SystemExit, 'tile'):
            R.check_mosaic(Path('mosaic-z19-4x4-260093-175744.png'), (1024, 1024), '19,260091,175744')
        with self.assertRaisesRegex(SystemExit, 'size'):
            R.check_mosaic(Path('mosaic-z19-4x4-260093-175744.png'), (768, 1024), '19,260093,175744')



class HardeningTest(unittest.TestCase):
    """Synthetic image, shape and CLI regressions from the roof trial."""

    def test_resolution_boundary_accepts_roundoff_but_refuses_coarser(self):
        img = np.full((80, 80, 3), 200, dtype=np.uint8)
        foot = {(x, z) for x in range(5, 15) for z in range(5, 15)}
        for ppb in (2.5, 2.5 - 1e-12):
            self.assertEqual(R.derive(img, _affine(ppb, 0, 0), foot)['pv_arrays'], [])
        with self.assertRaisesRegex(ValueError, 'coarse'):
            R.derive(img, _affine(2.499, 0, 0), foot)

    def test_neutral_panels_are_available_with_explicit_colour_threshold(self):
        img, foot, _ = _fixture()
        img = np.repeat(img.mean(axis=2, keepdims=True).astype(np.uint8), 3, axis=2)
        self.assertFalse(R.derive(img, _affine(), foot)['pv_arrays'])
        self.assertTrue(R.derive(img, _affine(), foot, blue_margin=0)['pv_arrays'])

    def test_image_edge_does_not_drop_clipped_array(self):
        img, foot, _ = _fixture()
        # Clip both the footprint and image through the second array.
        img = img[:, :40 * PPB]
        foot = {c for c in foot if c[0] < 140}
        out = R.derive(img, _affine(), foot)
        self.assertTrue(any(a['box'][0] >= 133 for a in out['pv_arrays']))

    def test_angled_array_does_not_fill_its_rectangular_hull(self):
        img=np.full((50*PPB,60*PPB,3),ROOF,dtype=np.uint8)
        foot={(x,z) for x in range(105,155) for z in range(205,245)}
        _array(img,(113,213,137,237),'x')
        v,u=np.indices(img.shape[:2])
        outside=np.abs(X0+(u+0.5)/PPB-125)+np.abs(Z0+(v+0.5)/PPB-225)>12
        img[outside]=ROOF
        out=R.derive(img,_affine(),foot,min_cells=10)
        self.assertTrue(out['pv_arrays'])
        want={(x,z) for a in out['pv_arrays'] for x in range(a['box'][0],a['box'][2]+1) for z in range(a['box'][1],a['box'][3]+1) if ((z-a['box'][1]) if a['rows']=='x' else (x-a['box'][0]))%a['pitch']==0}
        self.assertTrue(all(abs(x-125)+abs(z-225)<=13 for x,z in want))
        self.assertTrue(any(abs(x-125)+abs(z-225)>=9 for x,z in want))

    def test_row_runs_preserve_diamond_and_holes_for_both_axes(self):
        cells = {(x,z) for x in range(9) for z in range(9) if abs(x-4)+abs(z-4)<=4}
        cells.remove((4,4))
        for axis in ('x','z'):
            boxes = R.row_boxes(cells, (0,0,8,8), axis, 2)
            got = {(x,z) for a,b,c,d in boxes for x in range(a,c+1) for z in range(b,d+1)}
            want = {(x,z) for x,z in cells if (z if axis=='x' else x)%2==0}
            self.assertEqual(got, want)

    def test_footprint_box_and_mapped_exclusions(self):
        self.assertEqual(R._footprint(SimpleNamespace(box='1,2,2,3')), {(1,2),(1,3),(2,2),(2,3)})
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'spec.json'
            p.write_text(json.dumps({'ways':{'7':'A'}, 'exclude':{'A':[[1,2,1,2]]}}))
            idx=SimpleNamespace(cells=lambda:{(1,2):0,(2,2):0,(8,8):1}, buildings=[(None,{'osm_way_id':7}),(None,{'osm_id':8})])
            with mock.patch('osm_footprints.OSMIndex', return_value=idx):
                self.assertEqual(R._footprint(SimpleNamespace(box=None,spec=str(p),label='A')), {(2,2)})

    def test_mosaic_affine_uses_explicit_coordinate_helpers(self):
        with mock.patch('world_frame.mc_batch_to_wgs84', side_effect=lambda pts:pts), mock.patch('world_frame.tile_float', side_effect=lambda lat,lon,z:(lat/256+3,lon/256+4)):
            A=R._mosaic_affine('19,3,4', (0,0,10,20))
        np.testing.assert_allclose(A, [[1,0,0],[0,1,0]], atol=1e-10)

    def test_main_forwards_controls_writes_and_renders_sheet(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            image=Path(tmp)/'crop.png'; Image.new('RGB',(256,256),(200,200,200)).save(image)
            sheet=Path(tmp)/'sheet.png'; spec=Path(tmp)/'spec.json'
            spec.write_text(json.dumps({'roofs':{'A':{'kind':'flat','material':'stone'}}}))
            derived={'px_per_block':5,'shift':[0,0],'material':'smooth_stone','pv_arrays':[{'box':[5,5,8,8],'rows':'x','pitch':2,'source':'fixture'}]}
            args=['derive','--image',str(image),'--mosaic','19,3,4','--spec',str(spec),'--label','A','--blue-margin','0','--min-cells','3','--sheet',str(sheet),'--write','--replace-pv']
            with mock.patch.object(R,'_footprint',return_value={(x,z) for x in range(5,10) for z in range(5,10)}), mock.patch.object(R,'_mosaic_affine',return_value=_affine(5,0,0)), mock.patch.object(R,'derive',return_value=derived) as derive, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
                self.assertEqual(R.main(args),0)
            self.assertEqual(derive.call_args.kwargs['blue_margin'],0)
            self.assertEqual(derive.call_args.kwargs['min_cells'],3)
            self.assertIn('no tile identity',err.getvalue())
            self.assertTrue(sheet.is_file())
            self.assertEqual(json.loads(spec.read_text())['roofs']['A']['pv_arrays'],derived['pv_arrays'])
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                R.main(['derive','--image',str(image),'--mosaic','19,3,4','--box','1,1,3,3','--replace-pv'])

if __name__ == '__main__':
    unittest.main()

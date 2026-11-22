"""Tests for water-body plan vocabulary, provenance and build (#148)."""
from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

import decision_record as dr
import provenance as prov
import water_body as W
import water_level as WL


class LoadSpecTest(unittest.TestCase):
    """Seam: load_spec validates outline, water level, bank/edge and provenance."""

    def write(self, spec: dict) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'pond.json'
        path.write_text(json.dumps(spec))
        return path

    def good(self) -> dict:
        return {
            'name': 'pond_demo',
            'outline': [[10, 20], [11, 20], [10, 21], [11, 21]],
            'water_level': 55,
            'bank': {'material': 'dirt', 'width': 1},
            'edge': {'material': 'cobblestone'},
            'provenance': {
                'source': 'vector_map',
                'confidence': 'high',
                'evidence_ids': [],
                'unresolved_reason': '',
            },
        }

    def test_loads_defaults_and_required_keys(self):
        spec = W.load_spec(self.write(self.good()))
        self.assertEqual(spec['name'], 'pond_demo')
        self.assertEqual(spec['water_level'], 55)
        self.assertEqual(spec['depth'], 1)
        self.assertEqual(spec['floor'], 'dirt')
        self.assertEqual(spec['modifies'], [])
        self.assertEqual(spec['bank'], {'material': 'dirt', 'width': 1, 'rise': 0})
        self.assertEqual(spec['edge'], {'material': 'cobblestone'})
        self.assertEqual(spec['provenance']['source'], 'vector_map')
        self.assertFalse(prov.is_unresolved(spec['provenance']))

    def test_missing_or_bad_fields_refused(self):
        good = self.good()
        cases = [
            {k: v for k, v in good.items() if k != 'name'},
            {k: v for k, v in good.items() if k != 'outline'},
            {k: v for k, v in good.items() if k != 'water_level'},
            {k: v for k, v in good.items() if k != 'bank'},
            {k: v for k, v in good.items() if k != 'provenance'},
            {**good, 'outline': []},
            {**good, 'outline': [[10]]},
            {**good, 'water_level': 55.5},
            {**good, 'depth': 0},
            {**good, 'bank': {'material': ''}},
            {**good, 'bank': 'dirt'},
            {**good, 'edge': {'material': ''}},
            {**good, 'provenance': {'source': 'guess', 'confidence': 'high',
                                   'evidence_ids': [], 'unresolved_reason': ''}},
        ]
        for bad in cases:
            with self.subTest(bad=bad):
                with self.assertRaises(SystemExit):
                    W.load_spec(self.write(bad))

    def test_edge_optional_default_none(self):
        base = {k: v for k, v in self.good().items() if k != 'edge'}
        spec = W.load_spec(self.write(base))
        self.assertIsNone(spec['edge'])

    def test_uses_shared_provenance_module_only(self):
        """#151: water body imports provenance and defines no private schema fields."""
        src = Path(W.__file__).read_text()
        self.assertIn('import provenance as prov', src)
        self.assertIn('prov.attach_provenance', src)
        self.assertIn('prov.provenance_record', src)
        for private in ('source_kind', 'confidence_score', 'evidence_refs'):
            self.assertNotIn(private, src)


class PlanItemTest(unittest.TestCase):
    """Seam: plan-item helper carries outline/level/bank/edge + shared provenance."""

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.root = Path(self._d.name)
        dr.add_evidence('vmap1', type='vector_map', provider='p', title='Map', root=self.root)

    def tearDown(self):
        self._d.cleanup()

    def test_plan_item_shape_and_unresolved(self):
        item = W.water_body_plan_item(
            label='Library pond',
            outline=[(10, 20), (11, 20), (10, 21)],
            cell=(48, -45),
            refs=['w999'],
            water_level=None,
            bank=None,
            edge=None,
            source='vector_map',
            confidence='medium',
            evidence_ids=['vmap1'],
            unresolved_reason='water level and bank not yet read',
            root=self.root,
        )
        self.assertEqual(item['kind'], 'water_body')
        self.assertEqual(item['label'], 'Library pond')
        self.assertEqual(item['refs'], ['w999'])
        self.assertEqual(item['cells'], [[48, -45]])
        self.assertEqual(item['outline'], [[10, 20], [11, 20], [10, 21]])
        self.assertIsNone(item['water_level'])
        self.assertEqual(item['provenance']['source'], 'vector_map')
        self.assertTrue(prov.is_unresolved(item['provenance']))
        found = prov.unresolved_for_cell((48, -45), [item])
        self.assertEqual(found, [item])

    def test_resolved_item_has_level_bank_edge(self):
        item = W.water_body_plan_item(
            label='pond',
            outline=[(1, 1), (2, 1)],
            cell=(48, -45),
            refs=['w1'],
            water_level=40,
            bank={'material': 'dirt', 'width': 1},
            edge={'material': 'stone'},
            source='aerial',
            confidence='high',
            evidence_ids=['vmap1'],
            root=self.root,
        )
        self.assertEqual(item['water_level'], 40)
        self.assertEqual(item['bank']['material'], 'dirt')
        self.assertEqual(item['edge']['material'], 'stone')
        self.assertFalse(prov.is_unresolved(item['provenance']))


class ComponentsTest(unittest.TestCase):
    """Seam: connected water columns become water-body candidates."""

    def test_components_split_disconnected_ponds(self):
        cols = {(0, 0), (1, 0), (0, 1), (1, 1), (50, 50), (51, 50)}
        bodies = W.water_body_components(cols, cell=(48, -45), min_columns=2)
        self.assertEqual(len(bodies), 2)
        areas = sorted(b['area'] for b in bodies)
        self.assertEqual(areas, [2, 4])
        for b in bodies:
            self.assertEqual(b['kind'], 'water_body')
            self.assertEqual(b['cells'], [[48, -45]])
            self.assertTrue(b['outline'])
            self.assertIn('bbox', b)

    def test_tiny_slivers_dropped(self):
        cols = {(0, 0), (10, 10), (11, 10), (10, 11), (11, 11)}
        bodies = W.water_body_components(cols, cell=(48, -45), min_columns=4)
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0]['area'], 4)

    def test_column_refs_and_vector_map_provenance(self):
        """#163: column_refs become candidate refs; provenance source vector_map."""
        cols = {(10, 10), (11, 10), (10, 11), (11, 11)}
        refs = {c: 'w141657072' for c in cols}
        bodies = W.water_body_components(
            cols,
            cell=(48, -45),
            min_columns=4,
            column_refs=refs,
            provenance_source='vector_map',
        )
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0]['refs'], ['w141657072'])
        self.assertEqual(bodies[0]['provenance']['source'], 'vector_map')


class BuildTest(unittest.TestCase):
    """Seam: build places water at level, bank ring, and shoreline edge."""

    def spec(self, **overrides) -> dict:
        base = {
            'name': 'unit_pond',
            'outline': [[10, 20], [11, 20], [10, 21], [11, 21]],
            'water_level': 55,
            'depth': 2,
            'floor': 'dirt',
            'bank': {'material': 'coarse_dirt', 'width': 1},
            'edge': {'material': 'cobblestone'},
            'modifies': [],
            'provenance': {
                'source': 'vector_map',
                'confidence': 'high',
                'evidence_ids': [],
                'unresolved_reason': '',
            },
        }
        base.update(overrides)
        return W.load_spec_dict(base)

    def test_water_floor_bank_and_edge_placed(self):
        plan = W.build(self.spec())
        # water surface on outline
        for x, z in ((10, 20), (11, 20), (10, 21), (11, 21)):
            self.assertEqual(plan.get(x, 55, z), 'minecraft:water')
            self.assertEqual(plan.get(x, 54, z), 'minecraft:water')
            self.assertEqual(plan.get(x, 53, z), 'minecraft:dirt')
        # shoreline bank cell gets edge material at water_level
        self.assertEqual(plan.get(9, 55, 20), 'minecraft:cobblestone')
        self.assertEqual(plan.get(12, 55, 20), 'minecraft:cobblestone')
        # outer bank (diagonal corner of N8 that is not 4-adjacent? width-1 N8)
        # (9,19) is N8 neighbour of (10,20) — shoreline → edge
        self.assertEqual(plan.get(9, 55, 19), 'minecraft:cobblestone')
        # a bank cell only touching via width but wait — width 1 is N8 ring
        # all bank cells touch water via N8, so all get edge when edge is set
        bank_only = W.build(self.spec(edge=None))
        self.assertEqual(bank_only.get(9, 55, 20), 'minecraft:coarse_dirt')
        # outside the width-1 bank ring: untouched
        self.assertIsNone(bank_only.get(8, 55, 20))

    def test_sunken_pond_clears_above_and_raises_bank(self):
        plan = W.build(self.spec(clear_above=1, bank={'material': 'coarse_dirt', 'rise': 1}, edge=None))
        self.assertEqual(plan.get(10, 55, 20), 'minecraft:water')
        self.assertEqual(plan.get(10, 56, 20), 'minecraft:air')
        self.assertEqual(plan.get(9, 56, 20), 'minecraft:coarse_dirt')
        self.assertIsNone(plan.get(9, 55, 20))
        for bad in ({'clear_above': -1}, {'bank': {'material': 'x', 'rise': -1}}):
            with self.assertRaises(SystemExit):
                self.spec(**bad)

    def test_clear_to_removes_only_ground_above_the_surface_per_column(self):
        # (10, 20) is water at 55, (9, 20) a bank cell at 55: clear up to each column's own ground height
        plan = W.build(self.spec(edge=None, clear_to=[[10, 20, 57], [9, 20, 56], [9, 21, 55], [100, 100, 60]]))
        self.assertEqual([plan.get(10, y, 20) for y in (56, 57)], ['minecraft:air'] * 2)
        self.assertIsNone(plan.get(10, 58, 20))
        self.assertEqual(plan.get(9, 56, 20), 'minecraft:air')
        self.assertIsNone(plan.get(9, 57, 20))
        self.assertIsNone(plan.get(9, 56, 21))          # ground at or below the surface: nothing cleared
        self.assertIsNone(plan.get(100, 56, 100))       # a column the body does not build is ignored
        self.assertIsNone(W.build(self.spec(edge=None)).get(9, 56, 20))
        with self.assertRaises(SystemExit):
            self.spec(clear_to=[[1, 2]])

    def test_summary_counts_water(self):
        plan = W.build(self.spec())
        summary = plan.summary()
        self.assertEqual(summary['name'], 'unit_pond')
        self.assertGreater(summary['materials'].get('minecraft:water', 0), 0)
        self.assertIn('minecraft:cobblestone', summary['materials'])


class ClippedBuildTest(unittest.TestCase):
    """Seam: a water body over a cell line builds per cell, clipped, banked only on real shores (#321).

    Two-cell fixture: a river strip crosses the x = 128 line between cells 0,0 and 1,0. The east
    cell also has a bump of water at (128, 9), so the west land column (127, 8) touches water only
    across the cell line: it is a real shore, and the west clip must bank it.
    """

    WEST, EAST = (0, 0), (1, 0)
    LEVEL = 55

    def river(self) -> list[list[int]]:
        cols = [[x, z] for x in range(122, 134) for z in range(10, 14)]
        return cols + [[128, 9]]

    def spec(self, clip=None) -> dict:
        base = {
            'name': 'unit_river',
            'outline': self.river(),
            'water_level': self.LEVEL,
            'depth': 2,
            'bank': {'material': 'coarse_dirt', 'width': 1},
            'edge': {'material': 'cobblestone'},
            'provenance': {'source': 'vector_map', 'confidence': 'high',
                           'evidence_ids': [], 'unresolved_reason': ''},
        }
        if clip is not None:
            base['clip'] = {'cell': list(clip)}
        return W.load_spec_dict(base)

    def test_each_clip_writes_only_its_own_cell(self):
        for cell in (self.WEST, self.EAST):
            plan = W.build(self.spec(cell))
            self.assertTrue(plan.cells)
            self.assertEqual({(x // 128, z // 128) for x, _y, z in plan.cells}, {cell})

    def test_no_bank_ring_on_the_clip_edge(self):
        west = W.build(self.spec(self.WEST))
        east = W.build(self.spec(self.EAST))
        for z in range(10, 14):
            self.assertEqual(west.get(127, self.LEVEL, z), 'minecraft:water')
            self.assertEqual(east.get(128, self.LEVEL, z), 'minecraft:water')
            self.assertIsNone(west.get(128, self.LEVEL, z))   # no wall in the neighbour's water
            self.assertIsNone(east.get(127, self.LEVEL, z))

    def test_real_shores_are_banked_on_both_sides_of_the_line(self):
        west = W.build(self.spec(self.WEST))
        east = W.build(self.spec(self.EAST))
        self.assertEqual(west.get(121, self.LEVEL, 11), 'minecraft:cobblestone')   # west end
        self.assertEqual(east.get(134, self.LEVEL, 11), 'minecraft:cobblestone')   # east end
        self.assertEqual(west.get(127, self.LEVEL, 9), 'minecraft:cobblestone')    # north shore
        self.assertEqual(east.get(129, self.LEVEL, 8), 'minecraft:cobblestone')    # bump shore
        # touches water only across the cell line: still a shore, owned by the west clip
        self.assertEqual(west.get(127, self.LEVEL, 8), 'minecraft:cobblestone')

    def test_two_clips_join_into_the_whole_body_without_a_seam(self):
        whole = W.build(self.spec())
        west = W.build(self.spec(self.WEST))
        east = W.build(self.spec(self.EAST))
        self.assertFalse(set(west.cells) & set(east.cells))
        self.assertEqual({**west.cells, **east.cells}, whole.cells)

    def edge_river(self, z0: int, width: int, clip=None) -> dict:
        """A river crossing x = 128 whose near shore runs ``z0`` blocks above the z = 0 cell line."""
        base = {'name': 'unit_edge_river', 'water_level': self.LEVEL,
                'outline': [[x, z] for x in range(122, 134) for z in range(z0, z0 + 3)],
                'bank': {'material': 'coarse_dirt', 'width': width}, 'edge': {'material': 'cobblestone'},
                'provenance': {'source': 'vector_map', 'confidence': 'high'}}
        if clip is not None:
            base['clip'] = {'cell': list(clip)}
        return W.load_spec_dict(base)

    def test_bank_in_a_cell_without_water_is_built_by_its_own_clip(self):
        """Water near a cell line it does not cross: every cell the bank reaches has a clip."""
        for z0, width in ((0, 1), (1, 2), (0, 3)):
            whole = W.build(self.edge_river(z0, width))
            cells = {(x // 128, z // 128) for x, _y, z in whole.cells}
            self.assertIn((0, -1), cells)                      # the bank spills over z = 0
            union: dict = {}
            for cell in cells:
                clip = W.build(self.edge_river(z0, width, cell))
                self.assertEqual({(x // 128, z // 128) for x, _y, z in clip.cells}, {cell})
                self.assertFalse(set(clip.cells) & set(union))
                union.update(clip.cells)
            self.assertEqual(union, whole.cells, (z0, width))
        bank_only = W.build(self.edge_river(0, 1, (0, -1)))
        self.assertEqual(set(bank_only.cells.values()), {'minecraft:cobblestone'})

    def test_clip_spec_is_validated(self):
        with self.assertRaises(SystemExit):
            self.spec((5, 5))                                  # no water or bank in that cell
        with self.assertRaises(SystemExit):
            self.edge_river(2, 1, (0, -1))                     # bank stops at z = 1, short of the cell
        bad = {'name': 'x', 'outline': self.river(), 'water_level': 1,
               'bank': {'material': 'dirt', 'width': W.CLIP_REACH + 1},
               'clip': {'cell': [0, 0]},
               'provenance': {'source': 'vector_map', 'confidence': 'high'}}
        with self.assertRaises(SystemExit):
            W.load_spec_dict(bad)                              # bank wider than the outline's reach
        with self.assertRaises(SystemExit):
            W.load_spec_dict({**bad, 'bank': {'material': 'dirt'}, 'clip': {'cell': [0]}})

    def test_non_integer_clip_cell_raises_water_body_error(self):
        """#353: clip.cell must be integers; raise WaterBodyError, not a bare ValueError."""
        bad = {'name': 'unit_river', 'outline': self.river(), 'water_level': self.LEVEL,
               'bank': {'material': 'dirt'}, 'clip': {'cell': ['west', 0]},
               'provenance': {'source': 'vector_map', 'confidence': 'high'}}
        with self.assertRaises(W.WaterBodyError):
            W.load_spec_dict(bad)
        with self.assertRaises(W.WaterBodyError):
            W.load_spec_dict({**bad, 'clip': {'cell': [0, 1.5]}})

    def test_clip_plan_id_name_requires_clip_field(self):
        """#353: copying a clip outline under a clip id without clip banks the artificial edge."""
        base = {
            'name': W.clip_plan_id('w9', self.WEST),
            'outline': self.river(),
            'water_level': self.LEVEL,
            'bank': {'material': 'coarse_dirt', 'width': 1},
            'provenance': {'source': 'vector_map', 'confidence': 'high'},
        }
        with self.assertRaises(W.WaterBodyError) as ctx:
            W.load_spec_dict(base)
        self.assertIn('clip plan id', str(ctx.exception))
        ok = W.load_spec_dict({**base, 'clip': {'cell': list(self.WEST)}})
        self.assertEqual(ok['clip'], {'cell': [0, 0]})
        with self.assertRaises(W.WaterBodyError) as mismatch:
            W.load_spec_dict({**base, 'clip': {'cell': list(self.EAST)}})
        self.assertIn('does not match', str(mismatch.exception))
        # Whole-body synthetic ids, free-form legacy names, and plain names stay valid without clip.
        whole = W.load_spec_dict({**base, 'name': 'water_122_10'})
        self.assertIsNone(whole['clip'])
        legacy = W.load_spec_dict({**base, 'name': 'river_itchen_63_m45'})
        self.assertIsNone(legacy['clip'])
        self.assertIsNone(W.parse_clip_plan_id('water_122_10'))
        self.assertIsNone(W.parse_clip_plan_id('river_itchen_63_m45'))
        self.assertEqual(W.parse_clip_plan_id('w9_0_0'), ('w9', (0, 0)))
        self.assertEqual(W.parse_clip_plan_id('water_122_10_0_0'), ('water_122_10', (0, 0)))

    def test_bank_width_greater_than_one_on_a_clip(self):
        """#353: clipped builds honour bank.width > 1 inside the clip cell only."""
        plan = W.build(self.edge_river(0, 2, self.WEST))
        self.assertEqual({(x // 128, z // 128) for x, _y, z in plan.cells}, {self.WEST})
        self.assertEqual(plan.get(121, self.LEVEL, 1), 'minecraft:cobblestone')  # width-2 shore
        self.assertIsNone(plan.get(128, self.LEVEL, 1))  # never writes the east cell

    def test_diagonal_corner_body_banks_only_the_clip_cell(self):
        """#353: a body that only touches a cell on the diagonal still clips cleanly."""
        # Water only in cell (1, 1), hugging the SW corner toward (0, 0).
        outline = [[x, z] for x in range(128, 132) for z in range(128, 132)]
        base = {
            'name': 'unit_corner',
            'outline': outline,
            'water_level': self.LEVEL,
            'bank': {'material': 'coarse_dirt', 'width': 1},
            'edge': {'material': 'cobblestone'},
            'provenance': {'source': 'vector_map', 'confidence': 'high'},
        }
        corner = W.build(W.load_spec_dict({**base, 'name': W.clip_plan_id('w1', (0, 0)),
                                           'clip': {'cell': [0, 0]}}))
        self.assertTrue(corner.cells)
        self.assertEqual({(x // 128, z // 128) for x, _y, z in corner.cells}, {(0, 0)})
        self.assertEqual(set(corner.cells.values()), {'minecraft:cobblestone'})
        self.assertEqual(corner.get(127, self.LEVEL, 127), 'minecraft:cobblestone')


class ProfileBuildTest(unittest.TestCase):
    """Synthetic reach profile, local heights and ownership across cell joins."""

    def raw(self, *, flat=False, clip=None, width=1, z0=10):
        outline = [[x, z] for x in range(112, 146) for z in range(z0, z0 + 4)]
        segments = WL.water_level_profile(
            [(0, 55), (16, 55 if flat else 54), (33, 55 if flat else 54)], 2)
        if clip is not None:
            x0, x1 = clip[0] * 128 - W.CLIP_REACH, (clip[0] + 1) * 128 - 1 + W.CLIP_REACH
            zmin, zmax = clip[1] * 128 - W.CLIP_REACH, (clip[1] + 1) * 128 - 1 + W.CLIP_REACH
            outline = [[x, z] for x, z in outline if x0 <= x <= x1 and zmin <= z <= zmax]
        raw = {
            'name': 'unit_profile', 'outline': outline, 'water_level': 55,
            'depth': 2, 'clear_above': 2, 'floor': 'clay',
            'bank': {'material': 'coarse_dirt', 'width': width, 'rise': 1},
            'edge': {'material': 'cobblestone'},
            'provenance': {'source': 'vector_map', 'confidence': 'high'},
            'water_level_profile': {
                'segments': [s._asdict() for s in segments],
                'columns': [[x, z, x - 112] for x, z in outline],
            },
        }
        if clip is not None:
            raw['clip'] = {'cell': list(clip)}
        return raw

    def test_flat_profile_matches_legacy_whole_and_clipped_plans(self):
        for clip in (None, (0, 0), (1, 0)):
            for width in (1, 3, W.CLIP_REACH):
                with self.subTest(clip=clip, width=width):
                    raw = self.raw(flat=True, clip=clip, width=width)
                    profile = W.build(W.load_spec_dict(raw))
                    del raw['water_level_profile']
                    legacy = W.load_spec_dict(raw)
                    self.assertNotIn('water_level_profile', legacy)
                    self.assertEqual(profile.cells, W.build(legacy).cells)

    def test_descending_reach_local_water_bed_air_and_true_banks(self):
        raw = self.raw()
        plan = W.build(W.load_spec_dict(raw))
        for x, z in raw['outline']:
            level = 55 if x < 128 else 54
            self.assertEqual(plan.get(x, level - 2, z), 'minecraft:clay')
            self.assertEqual(plan.get(x, level - 1, z), 'minecraft:water')
            self.assertEqual(plan.get(x, level, z), 'minecraft:water')
            self.assertEqual(plan.get(x, level + 1, z), 'minecraft:air')
            self.assertEqual(plan.get(x, level + 2, z), 'minecraft:air')
        # Water remains connected across the boundary; no internal coping/wall.
        for z in range(10, 14):
            self.assertEqual(plan.get(127, 54, z), 'minecraft:water')
            self.assertEqual(plan.get(128, 54, z), 'minecraft:water')
        self.assertEqual(plan.get(115, 56, 9), 'minecraft:cobblestone')
        self.assertEqual(plan.get(132, 55, 9), 'minecraft:cobblestone')
        self.assertIsNone(plan.get(132, 56, 9))
        # Equidistant shore water chooses the upstream distance deterministically.
        self.assertEqual(plan.get(128, 56, 9), 'minecraft:cobblestone')
        self.assertEqual(plan.get(129, 55, 9), 'minecraft:cobblestone')
        # Final endpoint (x=145) is included in the final segment.
        self.assertEqual(plan.get(145, 54, 11), 'minecraft:water')

    def test_bank_width_and_material_use_local_level(self):
        plan = W.build(W.load_spec_dict(self.raw(width=3)))
        self.assertEqual(plan.get(115, 56, 7), 'minecraft:coarse_dirt')
        self.assertEqual(plan.get(135, 55, 7), 'minecraft:coarse_dirt')
        self.assertIsNone(plan.get(135, 56, 7))
        raw = self.raw()
        raw['edge'] = None
        plain_bank = W.build(W.load_spec_dict(raw))
        self.assertEqual(plain_bank.get(132, 55, 9), 'minecraft:coarse_dirt')

    def test_equal_distance_assignment_order_does_not_change_banks(self):
        raw = self.raw(width=3)
        plan = W.build(W.load_spec_dict(raw))
        raw['water_level_profile']['columns'].sort(key=lambda row: (row[2], -row[1]))
        self.assertEqual(plan.cells, W.build(W.load_spec_dict(raw)).cells)

    def test_adjacent_context_clips_equal_whole_at_boundary_step(self):
        for width in (1, 3, W.CLIP_REACH):
            with self.subTest(width=width):
                whole = W.build(W.load_spec_dict(self.raw(width=width)))
                west = W.build(W.load_spec_dict(self.raw(clip=(0, 0), width=width)))
                east = W.build(W.load_spec_dict(self.raw(clip=(1, 0), width=width)))
                self.assertFalse(west.cells.keys() & east.cells.keys())
                self.assertEqual({**west.cells, **east.cells}, whole.cells)
                self.assertEqual({(x // 128, z // 128) for x, _, z in west.cells}, {(0, 0)})
                self.assertEqual({(x // 128, z // 128) for x, _, z in east.cells}, {(1, 0)})

    def test_bank_only_clips_keep_local_profile_heights(self):
        whole = W.build(W.load_spec_dict(self.raw(width=3, z0=0)))
        union = {}
        for cell in ((0, -1), (1, -1), (0, 0), (1, 0)):
            clip = W.build(W.load_spec_dict(self.raw(clip=cell, width=3, z0=0)))
            self.assertFalse(union.keys() & clip.cells.keys())
            self.assertEqual({(x // 128, z // 128) for x, _, z in clip.cells}, {cell})
            if cell[1] == -1:
                self.assertFalse({'minecraft:water', 'minecraft:clay', 'minecraft:air'}
                                 & set(clip.cells.values()))
            union.update(clip.cells)
        self.assertEqual(union, whole.cells)

    def test_single_point_profiler_output_supported(self):
        raw = self.raw()
        profile = raw['water_level_profile']
        profile['segments'] = [s._asdict() for s in WL.water_level_profile([(0, 55)], 2)]
        profile['columns'] = [[x, z, 0] for x, z in raw['outline']]
        plan = W.build(W.load_spec_dict(raw))
        del raw['water_level_profile']
        self.assertEqual(plan.cells, W.build(W.load_spec_dict(raw)).cells)

    def test_malformed_or_ambiguous_profiles_refused(self):
        good = self.raw()['water_level_profile']
        bad_profiles = [None, [], {}, {**good, 'segments': []}, {**good, 'columns': []}]
        for key, value in (('start', True), ('start', '0'), ('end', float('nan')),
                           ('end', float('inf')), ('end', -1), ('end', 0),
                           ('level', True), ('level', 55.5), ('level', 56)):
            profile = deepcopy(good)
            profile['segments'][0][key] = value
            bad_profiles.append(profile)
        for start in (15, 17, -1):  # overlap, gap, reversed ordering
            profile = deepcopy(good)
            profile['segments'][1]['start'] = start
            bad_profiles.append(profile)
        profile = deepcopy(good)
        profile['segments'][1]['level'] = 56  # downstream rise
        bad_profiles.append(profile)
        bad_profiles.extend([
            {**good, 'segments': ['bad']},
            {**good, 'segments': [{'start': 0, 'end': 33}]},
            {**good, 'segments': list(reversed(good['segments']))},
            {**good, 'columns': good['columns'][:-1]},  # uncovered outline
            {**good, 'columns': [good['columns'][0], *good['columns']]},  # overlap
            {**good, 'columns': list(reversed(good['columns']))},  # upstream order
        ])
        for row in ([112, 10], [112, 10, -1], [112, 10, 34],
                    [112, 10, float('inf')], [112, 10, float('nan')],
                    [112, 10, True], [112, 10, '0'], [112.5, 10, 0],
                    [True, 10, 0], [111, 10, 0]):
            bad_profiles.append({**good, 'columns': [row, *good['columns'][1:]]})
        for profile in bad_profiles:
            with self.subTest(profile=profile):
                with self.assertRaises(W.WaterBodyError):
                    W.load_spec_dict({**self.raw(), 'water_level_profile': profile})


class CandidateIdTest(unittest.TestCase):
    """Seam: water_body candidates get a stable plan id."""

    def test_candidate_plan_id_for_water_body(self):
        import cell_registry as CR
        item = {'kind': 'water_body', 'label': 'pond', 'refs': ['w555'], 'outline': [[1, 1]]}
        self.assertEqual(CR.candidate_plan_id(item), 'w555')
        with self.assertRaises(CR.RegistryError):
            CR.candidate_plan_id({'kind': 'water_body', 'label': 'x', 'refs': []})


if __name__ == '__main__':
    unittest.main()

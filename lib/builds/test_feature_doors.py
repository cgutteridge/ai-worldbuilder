"""Tests for feature doors seams."""

from __future__ import annotations

from contextlib import redirect_stdout
from unittest import mock
import io
import unittest

import numpy as np

from feature_build_fixtures import (
    _StoneWorld,
    _entrance_row,
    _glazing_plan,
    start_lidar_fixture as setUpModule,
    stop_lidar_fixture as tearDownModule,
)
from site_kit import door_frame_issues
import feature_build as F
import feature_spec as FS


class FacadePlanEntranceBuildTest(unittest.TestCase):
    """Seams: build places doors from facade_plan only (#146), including unresolved."""

    def setUp(self):
        # Wide enough for a 2-leaf door plus jambs on the south face.
        self.cells = {(x, z) for x in range(10, 17) for z in range(20, 25)}

    def spec(self, entrances, *, facades=None):
        return {
            'name': 'door_feature',
            'ways': {'99': 'A'},
            'storeys': {'A': 2},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'modifies': [],
            'floor_min': 50,
            'step_max': 4,
            'door_frame': True,
            'keep_edits': False,
            'wall': 'bricks',
            'floor': 'smooth_stone',
            'ledge_stair': False,
            
            'facade_plan': _glazing_plan(facades or {}, {'A': {'faces': {}, 'entrances': entrances}}),
        }

    def build_plan(self, entrances, *, facades=None, cells=None, world=None, zone_data=None):
        buf = io.StringIO()
        with redirect_stdout(buf):
            plan = F.build(
                self.spec(entrances, facades=facades),
                footprints={'A': cells if cells is not None else self.cells},
                world=world or _StoneWorld(),
                zone_data=zone_data,
            )
        return plan, buf.getvalue()

    def test_door_uses_lidar_and_refuses_stale_live_ground(self):
        class GroundWorld:
            def __init__(self, y):
                self.y = y

            def block(self, x, y, z):
                return 'minecraft:stone' if y <= self.y else 'minecraft:air'

        grids = {'A': {'x0': 9, 'z0': 19, 'ground_y': np.full((7, 9), 51)}}
        for live_y, offset, expected_y in ((49, 0, 52), (49, 2, 54), (42, 0, None)):
            with self.subTest(live_y=live_y, offset=offset):
                plan, out = self.build_plan(
                    [_entrance_row(floor_offset=offset)],
                    world=GroundWorld(live_y), zone_data=grids,
                )
                lower = [p for p, state in plan.cells.items()
                         if 'iron_door' in state and 'half=lower' in state]
                if expected_y is None:
                    self.assertEqual(lower, [])
                    self.assertIn('door T-A: refused', out)
                    self.assertIn('live y=42', out)
                    self.assertIn('lidar y=51', out)
                else:
                    self.assertEqual([p[1] for p in lower], [expected_y])

    def test_the_final_stair_pass_reads_the_world_and_reports_stairs_it_will_change(self):
        from unittest import mock
        class StairWorld:
            def block(self, x, y, z):
                return 'minecraft:stone' if y <= 49 else 'minecraft:air'
        with mock.patch.object(F, 'align_stair_shapes', wraps=F.align_stair_shapes) as aligned:
            plan, out = self.build_plan([_entrance_row()], world=StairWorld())
        self.assertTrue(callable(aligned.call_args.args[1]))
        self.assertGreaterEqual(aligned.call_count, 1)
        self.assertNotIn('already in the world will change shape', out)       # an empty world: nothing earlier to retarget

    def test_door_finds_live_ground_on_low_lying_cell(self):
        class LowWorld:
            def block(self, x, y, z):
                return 'minecraft:stone' if y <= 27 else 'minecraft:air'

        grids = {'A': {'x0': 9, 'z0': 19, 'ground_y': np.full((7, 9), 28)}}
        spec = self.spec([_entrance_row()])
        spec['floor_min'] = 28
        with redirect_stdout(io.StringIO()):
            plan = F.build(spec, footprints={'A': self.cells}, world=LowWorld(), zone_data=grids)
        lower = [p for p, state in plan.cells.items() if 'iron_door' in state and 'half=lower' in state]
        self.assertEqual([p[1] for p in lower], [29])
        # step_out also sees the live ground (27) below the door floor (28) and lays a stair
        stairs = [p for p, state in plan.cells.items() if 'stone_stairs' in state and p[1] == 28]
        self.assertTrue(stairs)

    def test_high_live_rim_does_not_put_unzoned_door_in_roof(self):
        class HighRimWorld:
            def block(self, x, y, z):
                return 'minecraft:stone' if y <= 58 else 'minecraft:air'
        spec = self.spec([_entrance_row(leaves=1, at=[13, 24])])
        spec['storeys']['A'] = 1
        spec['floor_min'] = 55
        grids = {'A': {'x0': 9, 'z0': 19, 'ground_y': np.full((7, 9), 55)}}
        with redirect_stdout(io.StringIO()):
            plan = F.build(spec, footprints={'A': self.cells}, world=HighRimWorld(), zone_data=grids)
        lower = [p for p, state in plan.cells.items() if '_door[' in state and 'half=lower' in state]
        self.assertEqual(len(lower), 1)
        self.assertEqual(lower[0][1], 56)
        self.assertIn('slab', plan.cells[(13, 58, 22)])

    def test_entrance_cover_preserves_other_real_ground_ownership(self):
        from journal_testkit import JournalWorld
        pos = (13, 52, 26)
        with JournalWorld() as journal:
            journal.build('ground_owner', 'ground', {pos: 'minecraft:light_gray_concrete'}, cell=(0, 0))
            plan, _ = self.build_plan([_entrance_row(awning_like={'kind': 'covered_area', 'depth': 3})])
        self.assertFalse(pos in plan.cells)

    def test_entrance_awning_survives_roof_and_keeps_door_walkable(self):
        plan, out = self.build_plan([
            _entrance_row(awning_like={'kind': 'awning'}),
        ])
        for x in (12, 13, 14):
            self.assertEqual(plan.cells.get((x, 52, 25)),
                             'minecraft:smooth_stone_slab[type=bottom]')
        self.assertEqual(plan.cells.get((13, 51, 25)), 'minecraft:air')
        self.assertEqual(plan.cells.get((13, 50, 25)), 'minecraft:air')
        self.assertIn('door T-A cover awning depth 1', out)

    def test_cover_without_slab_uses_full_block_and_warns(self):
        """Terracotta has no slab. A covered entrance must not raise (#355)."""
        spec = self.spec([_entrance_row(awning_like={'kind': 'covered_area', 'depth': 1})])
        spec['roofs']['A']['material'] = 'terracotta'
        buf = io.StringIO()
        with redirect_stdout(buf):
            plan = F.build(spec, footprints={'A': self.cells}, world=_StoneWorld())
        covers = [
            state for (x, y, z), state in plan.cells.items()
            if z == 25 and 'terracotta' in state
        ]
        self.assertTrue(covers)
        self.assertTrue(all(state == 'minecraft:terracotta' for state in covers))
        self.assertIn("roof material 'terracotta' has no slab", buf.getvalue())
        self.assertIn('using full block terracotta', buf.getvalue())

    def test_covered_area_depth_and_double_leaf_width(self):
        plan, out = self.build_plan([
            _entrance_row(leaves=2, awning_like={'kind': 'covered_area', 'depth': 3}),
        ])
        slabs = {pos for pos, state in plan.cells.items()
                 if pos[1] == 52 and pos[2] >= 25 and 'slab' in state}
        self.assertEqual(slabs, {(x, 52, z) for x in range(12, 16) for z in range(25, 28)})
        self.assertIn('door T-A cover covered_area depth 3', out)

    def test_porch_roof_posts_stand_at_front_corners(self):
        plan, out = self.build_plan([
            _entrance_row(awning_like={'kind': 'porch_roof', 'depth': 2}),
        ])
        for x in (12, 14):
            for y in (50, 51):
                self.assertEqual(plan.cells.get((x, y, 26)), 'minecraft:bricks')
            self.assertEqual(plan.cells.get((x, 52, 26)),
                             'minecraft:smooth_stone_slab[type=bottom]')
        for y in (50, 51):
            self.assertNotIn('bricks', plan.cells.get((13, y, 26), ''))
        self.assertIn('door T-A cover porch_roof depth 2', out)

    def test_low_ground_porch_posts_reach_the_surface(self):
        class LowWorld:
            def block(self, x, y, z):
                return 'minecraft:stone' if y <= 28 else 'minecraft:air'

        spec = self.spec([_entrance_row(awning_like={'kind': 'porch_roof', 'depth': 2})])
        spec['floor_min'] = None
        grids = {'A': {'x0': 9, 'z0': 19, 'ground_y': np.full((9, 9), 28)}}
        with redirect_stdout(io.StringIO()):
            plan = F.build(spec, footprints={'A': self.cells}, world=LowWorld(), zone_data=grids)
        for x in (12, 14):
            self.assertNotIn((x, 28, 26), plan.cells)
            for y in (29, 30):
                self.assertEqual(plan.cells[(x, y, 26)], 'minecraft:bricks')
            self.assertEqual(plan.cells[(x, 31, 26)], 'minecraft:smooth_stone_slab[type=bottom]')

    def test_zero_cover_depth_defaults_to_one(self):
        plan, out = self.build_plan([
            _entrance_row(awning_like={'kind': 'awning', 'depth': 0}),
        ])
        self.assertEqual(plan.cells.get((13, 52, 25)),
                         'minecraft:smooth_stone_slab[type=bottom]')
        self.assertNotIn('slab', plan.cells.get((13, 52, 26), ''))
        self.assertIn('cover awning depth 1', out)

    def test_east_facing_cover_extends_outward_and_uses_roof_material(self):
        spec = self.spec([_entrance_row(
            face='E', at=[16, 22], awning_like={'kind': 'awning', 'depth': 2},
        )])
        spec['roofs']['A']['material'] = 'brick'
        with redirect_stdout(io.StringIO()):
            plan = F.build(spec, footprints={'A': self.cells}, world=_StoneWorld())
        slabs = {pos for pos, state in plan.cells.items()
                 if pos[1] == 52 and pos[0] >= 17 and 'slab' in state}
        self.assertEqual(slabs, {(x, 52, z) for x in (17, 18) for z in (21, 22, 23)})
        self.assertEqual(plan.cells[(18, 52, 22)], 'minecraft:brick_slab[type=bottom]')

    def test_cover_skips_building_mass_beside_entrance(self):
        cells = self.cells | {(12, 25), (12, 26)}
        plain, _ = self.build_plan([_entrance_row()], cells=cells)
        covered, _ = self.build_plan([
            _entrance_row(awning_like={'kind': 'awning', 'depth': 2}),
        ], cells=cells)
        for z in (25, 26):
            self.assertEqual(covered.cells.get((12, 52, z)), plain.cells.get((12, 52, z)))
        self.assertEqual(covered.cells.get((13, 52, 26)),
                         'minecraft:smooth_stone_slab[type=bottom]')

    def test_none_cover_leaves_plan_unchanged(self):
        plain, _ = self.build_plan([_entrance_row()])
        none, out = self.build_plan([_entrance_row(awning_like={'kind': 'none'})])
        self.assertEqual(none.cells, plain.cells)
        self.assertNotIn(' cover ', out)

    def test_unresolved_entrance_builds_no_cover(self):
        import provenance as prov

        plan, out = self.build_plan([_entrance_row(
            awning_like={'kind': 'porch_roof', 'depth': 3},
            provenance=prov.provenance_record(
                source='inference', confidence='low', unresolved_reason='no photo',
            ),
        )])
        empty, _ = self.build_plan([])
        self.assertEqual(plan.cells, empty.cells)
        self.assertNotIn(' cover ', out)

    def test_double_door_mirrored_hinges_and_printed(self):
        plan, out = self.build_plan([_entrance_row(leaves=2, block='iron_door')])
        lowers = [(pos, st) for pos, st in plan.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 2, lowers)
        hinges = sorted(st.split('hinge=')[1].split(',')[0] for _, st in lowers)
        self.assertEqual(hinges, ['left', 'right'])
        xs = sorted(pos[0] for pos, _ in lowers)
        self.assertEqual(xs[1] - xs[0], 1)
        self.assertEqual(door_frame_issues(plan, _StoneWorld().block), [])
        self.assertIn('T-A', out)
        self.assertIn('iron_door', out)
        self.assertIn('2-leaf', out)

    def test_single_coloured_door_uses_block(self):
        plan, out = self.build_plan([_entrance_row(leaves=1, block='crimson_door')])
        lowers = [st for st in plan.cells.values() if 'crimson_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1)
        self.assertEqual(door_frame_issues(plan, _StoneWorld().block), [])
        self.assertIn('crimson_door', out)

    def test_double_door_survives_facade_glazing(self):
        """Both leaves stay doors when a facade table glazes the south face."""
        plan, _out = self.build_plan(
            [_entrance_row(leaves=2, block='iron_door')],
            facades={'A': {'S': [{'storey': 1, 'glazed_h': 3, 'pier': 1}]}},
        )
        lowers = [(pos, st) for pos, st in plan.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        uppers = [(pos, st) for pos, st in plan.cells.items()
                  if 'iron_door' in st and 'half=upper' in st]
        self.assertEqual(len(lowers), 2, lowers)
        self.assertEqual(len(uppers), 2, uppers)
        for (x, y, z), _ in lowers + uppers:
            self.assertNotIn('glass_pane', plan.cells.get((x, y, z), ''))

    def test_places_at_wall_nearest_entrance_at(self):
        """Designer ``at`` picks the wall cell; no portal-reach chooser."""
        cells = {(x, z) for x in range(10, 18) for z in range(20, 61)}
        for lo in (21, 24, 27, 31, 34, 37):
            cells |= {(18, lo), (18, lo + 1)}
        plan, out = self.build_plan(
            [_entrance_row(
                id='18-A', portal_id='18-A', face='E', leaves=1,
                block='iron_door', at=[18, 29],
            )],
            cells=cells,
        )
        self.assertNotIn(': skipped', out, out)
        lowers = [(pos, st) for pos, st in plan.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1, lowers)
        door_xz = (lowers[0][0][0], lowers[0][0][2])
        self.assertIn(door_xz, {(17, 29), (17, 30)}, door_xz)

    def test_unresolved_entrance_not_placed(self):
        import provenance as prov

        unresolved = _entrance_row(
            id='99-Z',
            provenance=prov.provenance_record(
                source='inference', confidence='low',
                unresolved_reason='no photo of this face',
            ),
        )
        # drop placement fields that placed rows need — unresolved may omit them
        for k in ('face', 'leaves', 'block', 'at'):
            unresolved.pop(k, None)
        placed = _entrance_row(id='T-A', leaves=1, block='iron_door')
        plan, out = self.build_plan([placed, unresolved])
        lowers = [st for st in plan.cells.values() if '_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1)
        self.assertIn('99-Z', out)
        self.assertIn('unresolved', out.lower())
        spruce = [st for st in plan.cells.values() if 'spruce_door' in st]
        self.assertEqual(spruce, [])

    def test_no_facade_entrances_places_no_legacy_spruce(self):
        plan, _out = self.build_plan([])
        doors = [st for st in plan.cells.values() if '_door' in st and 'trapdoor' not in st]
        self.assertEqual(doors, [])

    def test_door_row_follows_outside_ground_not_below(self):
        """I-017: door floor y is outside ground + floor_offset (never below outside)."""

        class HighPavementWorld:
            """Interior stone to y=49; pavement south of the wall at y=52."""

            def block(self, x, y, z):
                if z >= 25 and y <= 52:
                    return 'minecraft:stone'
                return 'minecraft:stone' if y <= 49 else 'minecraft:air'

        ground = np.full((6, 7), 49)
        ground[-1, :] = 52
        plan, _out = self.build_plan(
            [_entrance_row(leaves=1, block='iron_door', at=[13, 25], floor_offset=0)],
            world=HighPavementWorld(),
            zone_data={'A': {'x0': 10, 'z0': 20, 'ground_y': ground}},
        )
        lowers = [(pos, st) for pos, st in plan.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1, lowers)
        _x, y, _z = lowers[0][0]
        # outside ground top is 52 → floor block 52 → lower door leaf at 53
        self.assertEqual(y, 53)
        self.assertGreaterEqual(y - 1, 52)

    def test_door_clears_inward_vestibule_so_door_to_wall_is_zero(self):
        """#143: inward column at door height is air; floaters.door_to_wall does not fire."""
        import floaters as fl

        class HighPavementWorld:
            """Interior stone to y=49; pavement south of the wall at y=52."""

            def block(self, x, y, z):
                if z >= 25 and y <= 52:
                    return 'minecraft:stone'
                return 'minecraft:stone' if y <= 49 else 'minecraft:air'

        ground = np.full((6, 7), 49)
        ground[-1, :] = 52
        plan, _out = self.build_plan(
            [_entrance_row(leaves=1, block='iron_door', at=[13, 25], floor_offset=0)],
            world=HighPavementWorld(),
            zone_data={'A': {'x0': 10, 'z0': 20, 'ground_y': ground}},
        )
        lowers = [(pos, st) for pos, st in plan.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1, lowers)
        (x, y, z), st = lowers[0]
        # Face S → inward is north (−z). Opening height is lower + upper.
        for dy in (0, 1):
            key = (x, y + dy, z - 1)
            cell = plan.cells.get(key, 'minecraft:air')
            self.assertTrue(
                fl.is_air(cell) or cell.split('[')[0].endswith(':air'),
                f'inward vestibule {key} must be air, got {cell!r}',
            )

        def block_at(bx, by, bz):
            return plan.cells.get((bx, by, bz)) or HighPavementWorld().block(bx, by, bz)

        flags = fl.cell_glitches(block_at, x, y, z, st)
        self.assertFalse(
            any(r['kind'] == 'door_to_wall' for r in flags),
            flags,
        )

    def test_door_opening_clears_are_kept_against_earlier_build_clash(self):
        """#143: stoop/inward air survives owned-ground clash (keep_edits off)."""
        cells = {(x, z) for x in range(10, 17) for z in range(20, 25)}
        buf = io.StringIO()
        with redirect_stdout(buf):
            b = F.Build(
                self.spec([_entrance_row(leaves=1, block='iron_door', at=[13, 25])]),
                footprints={'A': cells},
                world=_StoneWorld(),
            )
            b.build()
        lowers = [(pos, st) for pos, st in b.p.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1, lowers)
        (x, y, z), _ = lowers[0]
        # Face S → out +z, in −z; floor under door is y-1.
        b_floor = y - 1
        for ox, oz in ((0, 1), (0, -1)):
            for yy in range(b_floor + 1, b_floor + 4):
                key = (x + ox, yy, z + oz)
                self.assertIn(key, b.keep, f'opening clear {key} must be kept')
                self.assertTrue(
                    'air' in (b.p.cells.get(key) or 'air'),
                    f'opening clear {key} missing from plan',
                )
        lintel = (x, b_floor + 3, z)
        self.assertIn(lintel, b.keep)
        self.assertIn('bricks', b.p.cells.get(lintel, ''), lintel)
        self.assertEqual(door_frame_issues(b.p, _StoneWorld().block), [])
        self.assertTrue(hasattr(b.p, 'keep') and lintel in b.p.keep,
                        'plan.keep must carry door keeps for apply clash')

    def test_stepped_wall_door_stays_on_register_with_built_jambs(self):
        """#164: stepped face keeps the door on the register cell; jambs built; guard clean."""
        # Long south face; after shell, strip coplanar jamb columns beside the
        # register (as on a 1–2 cell step) so walls-membership framing is gone
        # and solids at door height are air — the pre-fix pick then jumps away.
        cells = {(x, z) for x in range(10, 40) for z in range(18, 25)}
        reg = (25, 24)
        buf = io.StringIO()
        with redirect_stdout(buf):
            b = F.Build(
                self.spec([_entrance_row(
                    id='18-A', portal_id='18-A', face='S', leaves=1,
                    block='iron_door', at=list(reg),
                )]),
                footprints={'A': cells},
                world=_StoneWorld(),
            )
            # Shell + floors only; place the door ourselves after simulating the step.
            b.spec['facade_plan'] = {'A': {'faces': {}, 'entrances': []}}
            b.build()
            f = b.features[0]
            self.assertIn(reg, f.walls)
            for lx in (1, -1):
                n = (reg[0] + lx, reg[1])
                f.walls.discard(n)
                for y in range(f.b, f.b + f.storeys * FS.STOREY_H):
                    b.set(n[0], y, n[1], 'air')
            f.inner = f.cells - f.walls
            b.spec['facade_plan'] = {'A': {'faces': {}, 'entrances': [
                _entrance_row(
                    id='18-A', portal_id='18-A', face='S', leaves=1,
                    block='iron_door', at=list(reg),
                ),
            ]}}
            b.plan_doors(f)
        out = buf.getvalue()
        lowers = [(pos, st) for pos, st in b.p.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1, lowers)
        (x, y, z), _ = lowers[0]
        self.assertEqual((x, z), reg, f'door moved from register; out={out!r}')
        self.assertNotIn('moved', out.lower(), out)
        b_floor = y - 1
        for lx in (1, -1):
            for yy in (b_floor + 1, b_floor + 2):
                key = (x + lx, yy, z)
                self.assertTrue(
                    b._is_solid(x + lx, yy, z),
                    f'missing built jamb at {key}',
                )
                self.assertIn(key, b.keep, f'built jamb {key} must be kept')
        self.assertEqual(door_frame_issues(b.p, _StoneWorld().block), [])

    def test_set_door_leaf_builds_missing_jamb_solids(self):
        """#164: _set_door_leaf fills air jambs at b+1/b+2 and keeps them."""
        cells = {(x, z) for x in range(10, 17) for z in range(20, 25)}
        buf = io.StringIO()
        with redirect_stdout(buf):
            b = F.Build(
                self.spec([]),
                footprints={'A': cells},
                world=_StoneWorld(),
            )
            b.build()
        f = b.features[0]
        wall = next(c for c in sorted(f.walls) if b.exposed(c, 'S') and c[1] == 24)
        for lx in (1, -1):
            for yy in (f.b + 1, f.b + 2):
                b.set(wall[0] + lx, yy, wall[1], 'air')
        b._set_door_leaf(wall, 'S', 'iron_door', 'left', f.b)
        for lx in (1, -1):
            for yy in (f.b + 1, f.b + 2):
                key = (wall[0] + lx, yy, wall[1])
                self.assertTrue(b._is_solid(*key), key)
                self.assertIn(key, b.keep, key)
        self.assertEqual(door_frame_issues(b.p, _StoneWorld().block), [])

    def test_door_clears_keep_a_low_carpeted_deck_and_its_carpet(self):
        """#455: a deck row at b+2 inside the door keeps its wall top course and the carpet at b+3."""
        cells = {(x, z) for x in range(10, 17) for z in range(20, 25)}
        with redirect_stdout(io.StringIO()):
            b = F.Build(self.spec([]), footprints={'A': cells}, world=_StoneWorld())
            b.build()
        f = b.features[0]
        wall = next(c for c in sorted(f.walls) if b.exposed(c, 'S') and c[1] == 24)
        inn = (wall[0], wall[1] - 1)
        b._lay_carpet(f, inn, f.b + 2, 'gray_carpet')
        b._set_door_leaf(wall, 'S', 'iron_door', 'left', f.b)
        self.assertEqual(b.p.cells[(inn[0], f.b + 2, inn[1])], 'minecraft:bricks')
        self.assertEqual(b.p.cells[(inn[0], f.b + 3, inn[1])], 'minecraft:gray_carpet')
        self.assertEqual(b.p.cells[(inn[0], f.b + 1, inn[1])], 'minecraft:air')

    def test_moved_door_prints_distance_from_register(self):
        """#164: when the door cannot sit on the register coordinate, print distance."""
        cells = {(x, z) for x in range(10, 18) for z in range(20, 25)}
        # Register is south of the mass — nearest wall is one block north.
        plan, out = self.build_plan([
            _entrance_row(
                id='18-A', portal_id='18-A', face='S', leaves=1,
                block='iron_door', at=[13, 26],
            ),
        ], cells=cells)
        lowers = [(pos, st) for pos, st in plan.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1, lowers)
        (x, _y, z), _ = lowers[0]
        dist = abs(x - 13) + abs(z - 26)
        self.assertGreater(dist, 0)
        self.assertRegex(out, rf'door 18-A:.*moved {dist} .*from register')

    def test_stepped_wall_double_door_two_leaves_same_plane(self):
        """#165: double entrance on a stepped south face lands two coplanar leaves."""
        # Staircase mass: 2-cell treads stepping south. Primary at (15,22) has
        # interior; lateral (14,22) is a step-corner wall (no interior behind) so
        # the old face_ok candidate set refused it — pair must still land.
        cells = set()
        for x in range(10, 22):
            zmax = 20 + (x - 10) // 2
            for z in range(15, zmax + 1):
                cells.add((x, z))
        buf = io.StringIO()
        with redirect_stdout(buf):
            b = F.Build(
                self.spec([
                    _entrance_row(
                        id='40-A', portal_id='40-A', face='S', leaves=2,
                        block='iron_door', at=[15, 22],
                    ),
                ]),
                footprints={'A': cells},
                world=_StoneWorld(),
            )
            b.build()
        out = buf.getvalue()
        self.assertNotIn('no pair cell', out, out)
        self.assertIn('2-leaf', out)
        lowers = [(pos, st) for pos, st in b.p.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 2, lowers)
        xz = sorted((pos[0], pos[2]) for pos, _ in lowers)
        self.assertEqual(xz[0][1], xz[1][1], 'leaves must share one plane (same z)')
        self.assertEqual(abs(xz[0][0] - xz[1][0]), 1)
        # Outer jambs beyond the pair are solid; the free-standing step jamb
        # outside the footprint is written into keep (#164/#165).
        lo_x, hi_x = xz[0][0], xz[1][0]
        z = xz[0][1]
        b_floor = lowers[0][0][1] - 1
        for jx in (lo_x - 1, hi_x + 1):
            for yy in (b_floor + 1, b_floor + 2):
                self.assertTrue(
                    b._is_solid(jx, yy, z),
                    f'missing step/jamb at {(jx, yy, z)}',
                )
        built_step = [
            (jx, yy, z)
            for jx in (lo_x - 1, hi_x + 1)
            for yy in (b_floor + 1, b_floor + 2)
            if (jx, yy, z) in b.keep
        ]
        self.assertTrue(built_step, 'expected a kept step block at the shared step')
        self.assertEqual(door_frame_issues(b.p, _StoneWorld().block), [])

    def test_stepped_wall_double_falls_back_without_lateral_pair(self):
        """#165: no coplanar wall neighbour → 1-leaf and the 'no pair cell' line."""
        cells = {(x, z) for x in range(10, 40) for z in range(18, 25)}
        reg = (25, 24)
        buf = io.StringIO()
        with redirect_stdout(buf):
            b = F.Build(
                self.spec([]),
                footprints={'A': cells},
                world=_StoneWorld(),
            )
            b.spec['facade_plan'] = {'A': {'faces': {}, 'entrances': []}}
            b.build()
            f = b.features[0]
            for lx in (1, -1):
                n = (reg[0] + lx, reg[1])
                f.walls.discard(n)
                for y in range(f.b, f.b + f.storeys * FS.STOREY_H):
                    b.set(n[0], y, n[1], 'air')
            f.inner = f.cells - f.walls
            b.spec['facade_plan'] = {'A': {'faces': {}, 'entrances': [
                _entrance_row(
                    id='40-A', portal_id='40-A', face='S', leaves=2,
                    block='iron_door', at=list(reg),
                ),
            ]}}
            b.plan_doors(f)
        out = buf.getvalue()
        self.assertIn('no pair cell', out)
        self.assertIn('1-leaf', out)
        lowers = [(pos, st) for pos, st in b.p.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1, lowers)
        self.assertEqual(door_frame_issues(b.p, _StoneWorld().block), [])

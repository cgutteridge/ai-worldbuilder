"""Unit tests for scripts/ground_layer.py helpers (no Minecraft, no world writes)."""
import unittest
from collections import Counter

import ground_layer as gl


class TestGroundLayer(unittest.TestCase):
    """Naming, air rules and cell containment."""

    def test_plan_name_negative_tz(self):
        """Negative tz encodes as m prefix, matching cell_marker naming."""
        self.assertEqual(gl.plan_name(48, -44), 'highfield_48_m44_ground')

    def test_plan_name_positive_tz(self):
        """Non-negative tz stays numeric."""
        self.assertEqual(gl.plan_name(48, 3), 'highfield_48_3_ground')

    def test_flowers_and_stems_are_air(self):
        """§10: flowers and grass stems count as air for the ground step."""
        self.assertTrue(gl.is_air('minecraft:short_grass'))
        self.assertTrue(gl.is_air('minecraft:poppy[half=lower]'))
        self.assertFalse(gl.is_air('minecraft:grass_block'))

    def test_bare_strips_state(self):
        """World block states compare against plan ids without properties."""
        self.assertEqual(gl.bare('minecraft:oak_log[axis=y]'), 'minecraft:oak_log')

    def test_cell_contains_edges(self):
        """Cell 48,-44 includes its first and last column and excludes the next."""
        self.assertTrue(gl.cell_contains((48, -44), 6144, -5632))
        self.assertTrue(gl.cell_contains((48, -44), 6271, -5505))
        self.assertFalse(gl.cell_contains((48, -44), 6272, -5505))


class TestShadesGrass(unittest.TestCase):
    """#242: grass planned under an opaque trunk decays to dirt and fails readback."""

    def test_opaque_blocks_shade_grass(self):
        for b in ('minecraft:oak_log[axis=y]', 'minecraft:stone', 'minecraft:oak_planks'):
            self.assertTrue(gl.shades_grass(b), b)

    def test_clear_blocks_do_not(self):
        for b in (None, 'minecraft:air', 'minecraft:oak_leaves[distance=1]', 'minecraft:short_grass',
                  'minecraft:poppy', 'minecraft:glass', 'minecraft:oak_fence'):
            self.assertFalse(gl.shades_grass(b), b)


class TestGroundSources(unittest.TestCase):
    """Evidence rules: widths from tags, colour classes, speckle filter."""

    def test_road_width_from_lanes_and_cycle_lanes(self):
        import ground_sources as gs
        self.assertEqual(gs.road_width({'highway': 'unclassified', 'lanes': '2'}), 6.0)
        self.assertEqual(gs.road_width({'highway': 'unclassified', 'lanes': '2', 'cycleway:both': 'lane',
                                        'cycleway:width': '1.25'}), 8.5)
        self.assertEqual(gs.road_width({'highway': 'service', 'service': 'driveway'}), 3.0)
        self.assertEqual(gs.road_width({'highway': 'service', 'width': '4.5 m'}), 4.5)

    def test_aerial_classes(self):
        import ground_sources as gs
        self.assertEqual(gs.aerial_class((0.64, 0.60, 0.54)), 'lawn')      # khaki, sun-bleached grass
        self.assertEqual(gs.aerial_class((0.84, 0.76, 0.69)), 'brick')     # pink-orange block paving
        self.assertEqual(gs.aerial_class((0.85, 0.85, 0.85)), 'light')     # neutral paving
        self.assertEqual(gs.aerial_class((0.44, 0.39, 0.27)), 'shade')     # dark, saturated: canopy

    def test_majority_removes_a_speck(self):
        import ground_sources as gs
        grid = {(x, z): 'lawn' for x in range(5) for z in range(5)}
        grid[(2, 2)] = 'brick'
        self.assertEqual(gs.majority(grid, {(2, 2)})[(2, 2)], 'lawn')

    def test_poly_and_line_cells(self):
        import ground_sources as gs
        square = [[(0, 0), (4, 0), (4, 4), (0, 4)]]
        self.assertEqual(len(gs.poly_cells([square])), 16)
        self.assertIn((2, 0), gs.line_cells([(0.5, 0.5), (4.5, 0.5)], 0.6))


class TestAerialNoData(unittest.TestCase):
    """I-042/I-057: blank (black) mosaic pixels must not be read as dark paving."""

    def test_black_pixels_are_dropped_not_classed_dark(self):
        import tempfile
        from pathlib import Path
        import numpy as np
        from PIL import Image
        import ground_sources as gs
        im = np.full((40, 40, 3), 120, dtype=np.uint8)
        im[:, :20] = 0                                    # left half: no data
        with tempfile.TemporaryDirectory() as d:
            png = Path(d) / 'm.png'
            Image.fromarray(im).save(png)
            from unittest import mock
            import aerial_coverage as aerial
            source = aerial.Mosaic(png, 19, 0, 0)
            with mock.patch.object(aerial.Mosaic, 'pixels', lambda self, pts: {p: (p[0], 20) for p in pts}):
                out = aerial.samples([(10, 0), (30, 0)], [source], radius=2)
                with mock.patch.object(aerial, 'mosaics', return_value=[source]):
                    with self.assertRaisesRegex(aerial.CoverageError, 'aerial coverage'):
                        gs.aerial_rgb((0, 0), margin=0)
        self.assertNotIn((10, 0), out)
        self.assertIn((30, 0), out)


class TestGroundDecisions(unittest.TestCase):
    """Classification and levelling on small synthetic evidence."""

    class Osm:
        def __init__(self, kind=None, pri=None, road=(), marks=()):
            self.kind, self.pri, self.road, self.marks = kind or {}, pri or {}, set(road), set(marks)
            self.feat = {}
            self.hedge = self.wall = self.fence = set()

    def test_osm_surface_beats_aerial_and_shade_takes_neighbours(self):
        cell = (48, -44)
        x0, z0 = 6144, -5632
        live = {(x0 + i, z0 + j): (50, 'stone') for i in range(6) for j in range(6)}
        osm = self.Osm({(x0, z0): 'tarmac'}, {(x0, z0): 90})
        lawn, shade = (0.64, 0.60, 0.54), (0.44, 0.39, 0.27)
        rgb = {(x0 + i, z0 + j): lawn for i in range(-4, 9) for j in range(-4, 4)}
        rgb[(x0 + 2, z0 + 2)] = shade
        kind, _ = gl.classify(cell, live, osm, rgb)
        self.assertEqual(kind[(x0, z0)], 'tarmac')
        self.assertEqual(kind[(x0 + 1, z0)], 'grass')
        self.assertEqual(kind[(x0 + 2, z0 + 2)], 'grass')                      # shade takes the lawn around it

    def test_private_frontage_keeps_guessed_lawn_unpaved(self):
        """Guessed lawn between a road and a house is paving, unless it is a house's private frontage (#240)."""
        cell = (48, -44)
        x0, z0 = 6144, -5632
        road = {(x0 + i, z0): 'tarmac' for i in range(6)}
        osm = self.Osm(dict(road), {q: 90 for q in road}, road=road)
        fp = {(x0 + i, z0 + 5) for i in range(6)}
        garden = [(x0 + i, z0 + j) for i in range(6) for j in (2, 3)]
        live = {q: (50, 'stone') for q in garden}
        shade = (0.44, 0.39, 0.27)
        rgb = {q: shade for q in garden}
        plain, _ = gl.classify(cell, live, osm, rgb, fp)
        self.assertTrue(all(plain[q] == 'paving' for q in garden))
        kept, how = gl.classify(cell, live, osm, rgb, fp, set(garden))
        self.assertTrue(all(kept[q] == 'grass' for q in garden))
        self.assertEqual(how['guessed lawn kept: private residential frontage'], len(garden))

    def test_guessed_out_contains_only_final_guessed_grass(self):
        cell, osm, fp, garden, live, rgb = self._strip()
        observed = {(6155 + x, -5620 + z) for x in range(6) for z in range(4)}
        mapped = {(6165 + x, -5620 + z) for x in range(6) for z in range(4)}
        live.update({q: (50, 'stone') for q in observed | mapped})
        rgb.update({q: (0.64, 0.60, 0.54) for q in observed})
        osm.kind.update({q: 'grass' for q in mapped})
        osm.pri.update({q: 10 for q in mapped})
        route = set()
        guessed = set()
        kinds, how = gl.classify(cell, live, osm, rgb, fp, set(garden), route, guessed_out=guessed)
        self.assertEqual(guessed, {q for q in garden if kinds[q] == 'grass'})
        self.assertTrue(guessed)
        self.assertTrue(guessed.isdisjoint(route | observed | mapped))
        self.assertEqual((kinds, how), gl.classify(cell, live, osm, rgb, fp, set(garden), route))

        guessed = set()
        kinds, _ = gl.classify(cell, live, osm, rgb, fp, guessed_out=guessed)
        self.assertTrue(guessed.isdisjoint(garden))     # converted to paving by the road/house rule

    def test_guessed_out_preserves_mapped_grass_soft_grass_survivors(self):
        lawn = {(x, z) for x in range(2, 20) for z in range(2, 12)}
        osm = self.Osm({q: 'grass' for q in lawn}, {q: 10 for q in lawn})
        live = {q: (50, 'grass_block') for q in lawn}
        rgb = {q: (.84, .76, .69) for q in lawn}
        guessed = set()
        kinds, how = gl.classify((0, 0), live, osm, rgb, guessed_out=guessed)
        self.assertEqual(how['aerial brick away from paths -> lawn'], len(lawn))
        self.assertEqual(guessed, lawn)
        self.assertTrue(all(kinds[q] == 'grass' for q in guessed))

    def _strip(self):
        cell = (48, -44)
        x0, z0 = 6144, -5632
        road = {(x0 + i, z0): 'tarmac' for i in range(6)}
        osm = self.Osm(dict(road), {q: 90 for q in road}, road=road)
        fp = {(x0 + i, z0 + 5) for i in range(6)}
        garden = [(x0 + i, z0 + j) for i in range(6) for j in (2, 3)]
        live = {q: (50, 'stone') for q in garden}
        rgb = {q: (0.44, 0.39, 0.27) for q in garden}              # shade: lawn guessed, not seen
        return cell, osm, fp, garden, live, rgb

    def test_keep_class_frontage_never_becomes_paving_but_paving_class_does(self):
        """Behaviour change (#254, owner-confirmed): guessed lawn stays lawn in park, rural and natural land."""
        import garden_sources as gs
        cell, osm, fp, garden, live, rgb = self._strip()

        def front(cls):
            return {'eligible': cls in ('park', 'commercial'), 'private_surface': 'keep' if cls != 'commercial' else 'paving',
                    'extent': garden, 'surface_keep': garden if cls in ('rural', 'natural') else []}
        for cls in ('park', 'rural', 'natural'):
            kept = gs.private_frontage_columns([front(cls)])
            kind, _ = gl.classify(cell, live, osm, rgb, fp, kept)
            self.assertTrue(all(kind[q] == 'grass' for q in garden), cls)
        kind, _ = gl.classify(cell, live, osm, rgb, fp, gs.private_frontage_columns([front('commercial')]))
        self.assertTrue(all(kind[q] == 'paving' for q in garden))

    def test_public_pavement_strip_paves_guessed_lawn(self):
        cell = (48, -44)
        x0, z0 = 6144, -5632
        road = {(x0 + i, z0): 'tarmac' for i in range(12)}
        osm = self.Osm(dict(road), {q: 90 for q in road}, road=road)
        fp = {(x0 + i, z0 + 5) for i in range(12)}
        strip = {(x0 + i, z0 + 2) for i in range(12)}
        private = {(x0 + i, z0 + 3) for i in range(12)}
        live = {q: (50, 'stone') for q in strip | private}
        rgb = {q: (0.44, 0.39, 0.27) for q in live}                # shade: lawn guessed, not seen
        kind, how = gl.classify(cell, live, osm, rgb, fp, frozenset(private), frozenset(), strip)
        self.assertTrue(all(kind[q] == 'paving' for q in strip))
        self.assertTrue(all(kind[q] == 'grass' for q in private))
        self.assertEqual(how['guessed lawn on public pavement strip -> paving'], 12)

    def test_forecourt_paving_reads_apart_from_public_pavement(self):
        kind = {(0, 0): 'paving', (1, 0): 'paving', (2, 0): 'grass', (3, 0): 'gravel', (4, 0): 'tarmac', (5, 0): 'brick'}
        how = gl.split_forecourt(kind, {(0, 0), (2, 0), (3, 0), (4, 0), (5, 0), (9, 9)}, fp={(5, 0)})
        self.assertEqual(kind, {(0, 0): 'forecourt', (1, 0): 'paving', (2, 0): 'grass', (3, 0): 'gravel',
                                (4, 0): 'tarmac', (5, 0): 'brick'})
        self.assertEqual(sum(how.values()), 1)
        self.assertNotEqual(gl.MATERIAL['forecourt'], gl.MATERIAL['paving'])
        self.assertEqual(gl.BLOCK_KIND[gl.MATERIAL['forecourt']], 'forecourt')
        self.assertIn('forecourt', gl.KIND_RGB)

    def test_entrance_route_paves_guessed_ground_but_keeps_seen_lawn(self):
        """The onward route paves guessed (shaded) ground only; observed lawn stays (#240)."""
        cell = (48, -44)
        x0, z0 = 6144, -5632
        osm = self.Osm({}, {})
        area = [(x0 + i, z0 + j) for i in range(8) for j in range(8)]
        live = {q: (50, 'stone') for q in area}
        shade, lawn = (0.44, 0.39, 0.27), (0.64, 0.60, 0.54)
        rgb = {(x0 + i, z0 + j): lawn for i in range(-4, 12) for j in range(-4, 12)}
        patch = [(x0 + i, z0 + j) for i in range(2, 7) for j in range(2, 7)]    # shaded: lawn guessed, not seen
        for q in patch:
            rgb[q] = shade
        shaded = [(x0 + 4, z0 + j) for j in range(2, 7)]
        route = set(shaded) | {(x0 + 6, z0 + 1)}                   # one route column is seen lawn
        guessed = set()
        kind, how = gl.classify(cell, live, osm, rgb, set(), frozenset(), route, guessed_out=guessed)
        self.assertEqual(how['guessed ground on entrance route -> paving'], 5)
        self.assertTrue(all(kind[q] == 'paving' for q in shaded))
        self.assertEqual(kind[(x0 + 6, z0 + 1)], 'grass')          # seen lawn kept
        self.assertTrue(guessed.isdisjoint(route))
        self.assertTrue(all(kind[q] == 'grass' for q in guessed))
        plain, _ = gl.classify(cell, live, osm, rgb, set())
        self.assertTrue(all(plain[q] == 'grass' for q in shaded))

    def test_registered_aerial_lawn_beats_mapped_plaza_but_mosaic_lawn_does_not(self):
        import ground_sources as gs
        cell = (47, -45)
        x0, z0 = 6130, -5740
        cols = [(x0 + i, z0 + j) for i in range(8) for j in range(8)]
        live = {q: (50, 'stone') for q in cols}
        osm = self.Osm({q: 'paving' for q in cols}, {q: gs.PRI['pedestrian'] for q in cols})
        sharp = {q: gs.RegRGB((0.35, 0.45, 0.35)) for q in cols}          # registered user aerial: green
        kind, how = gl.classify(cell, live, osm, sharp)
        self.assertTrue(all(kind[q] == 'grass' for q in cols))
        self.assertGreater(how['registered aerial lawn inside mapped plaza'], 0)
        blurry = {q: (0.64, 0.60, 0.54) for q in cols}                    # mosaic lawn colour: plaza stays paved
        kind, _ = gl.classify(cell, live, osm, blurry)
        self.assertTrue(all(kind[q] == 'paving' for q in cols))

    def test_poly_cells_matches_the_per_column_reference(self):
        """The numpy even-odd fill must equal the old pure-Python scan, column for column."""
        import math
        import random
        import ground_sources as gs

        def reference(rings_list):
            out = set()
            for rings in rings_list:
                o = rings[0]
                for x in range(math.floor(min(p[0] for p in o)), math.ceil(max(p[0] for p in o)) + 1):
                    for z in range(math.floor(min(p[1] for p in o)), math.ceil(max(p[1] for p in o)) + 1):
                        c = (x + .5, z + .5)
                        if gs.inside(*c, o) and not any(gs.inside(*c, h) for h in rings[1:]):
                            out.add((x, z))
            return out

        rnd = random.Random(7)
        shapes = []
        for _ in range(40):
            cx, cz = rnd.uniform(-50, 50), rnd.uniform(-50, 50)
            n = rnd.randint(3, 12)
            ring = [(cx + rnd.uniform(3, 25) * math.cos(2 * math.pi * i / n),
                     cz + rnd.uniform(3, 25) * math.sin(2 * math.pi * i / n)) for i in range(n)]
            hole = [(cx + 1.5 * math.cos(2 * math.pi * i / 5), cz + 1.5 * math.sin(2 * math.pi * i / 5)) for i in range(5)]
            shapes.append([ring, hole] if rnd.random() < .4 else [ring])
        shapes.append([[(0, 0), (10, 0), (10, 0.5), (0, 0.5)]])                 # thin sliver, horizontal edges
        shapes.append([[(0.5, 0.5), (8.5, 0.5), (8.5, 8.5), (0.5, 8.5)]])       # edges exactly on column centres
        shapes.append([[(1, 1), (2, 2), (3, 3)]])                               # degenerate
        for s in shapes:
            self.assertEqual(gs.poly_cells([s]), reference([s]))
        self.assertEqual(gs.poly_cells(shapes), reference(shapes))

    def test_only_isolated_hard_spikes_are_levelled(self):
        live = {(x, z): (50, 'x') for x in range(5) for z in range(5)}
        live[(2, 2)] = (51, 'x')
        kind = {q: 'paving' for q in live}
        self.assertEqual(gl.level_spikes(live, kind, set())[(2, 2)], 50)
        kind[(2, 2)] = 'grass'
        self.assertEqual(gl.level_spikes(live, kind, set())[(2, 2)], 51)

    def test_ground_heights_raise_live_below_lidar(self):
        """#167: lidar base is HOW HIGH; a live dip below lidar rises to lidar.

        Wide wrong-baseline voids that neighbour-rim pits would leave low still
        rise. Footprint columns are not in ``live`` so stay untouched. Kind /
        paving hints are decided elsewhere and are not rewritten here.
        """
        from block_store import MemoryBlocks
        from site_kit import Plan, World

        # Live sits at 36–39 across a wide patch; lidar ground is 48 everywhere.
        live = {(x, z): (36 + (x % 4), 'grass_block') for x in range(0, 12) for z in range(0, 8)}
        lidar = {q: 48 for q in live}
        kind = {q: 'grass' for q in live}
        # Neighbour-rim pit fill would leave this wide depression at live height.
        self.assertGreater(gl._pit_chebyshev_radius(set(live)), gl.PIT_MAX_RADIUS)
        ys = gl.ground_heights(live, lidar)
        for q in live:
            self.assertEqual(ys[q], 48, q)
        self.assertEqual(ys.get('_from_lidar'), len(live))

        # MemoryBlocks: raise fill under the new lidar top; live-only paving
        # kind dict is unchanged; footprint columns (never in ``live``) stay absent.
        fp = {(-1, 0), (-2, 0)}  # excluded by free_columns; must not appear in ys
        blocks = {}
        for (x, z), (y, b) in live.items():
            blocks[(x, y, z)] = f'minecraft:{b}'
            for yy in range(y - 2, y):
                blocks[(x, yy, z)] = 'minecraft:dirt'
        world = World(store=MemoryBlocks(blocks, default='air'))
        plan = Plan('test_lidar_height')
        for (x, z), (old, _) in live.items():
            gy = ys[(x, z)]
            plan.set(x, gy, z, gl.MATERIAL[kind[(x, z)]])
            for y in range(old + 1, gy):
                if (world.block(x, y, z) or 'air').endswith('air'):
                    plan.set(x, y, z, gl.RAISE_FILL)
        self.assertEqual(plan.cells[(5, 48, 3)], 'minecraft:grass_block')
        self.assertEqual(plan.cells[(5, 40, 3)], 'minecraft:dirt')
        self.assertEqual(kind[(5, 3)], 'grass')
        for q in fp:
            self.assertNotIn(q, ys)

    def test_ground_heights_keep_lidar_depression(self):
        """#167: a real low in lidar stays low; live matching it is not raised to neighbours."""
        live = {(x, z): (40, 'grass_block') for x in range(6) for z in range(6)}
        lidar = {q: 40 for q in live}
        # Surrounding would-be rim at 48 is not in lidar_ys for these columns.
        ys = gl.ground_heights(live, lidar)
        for q in live:
            self.assertEqual(ys[q], 40, q)

    def test_ground_heights_fall_back_to_live_when_lidar_missing(self):
        """#167: missing lidar base (0 / absent) keeps the live surface Y."""
        live = {(0, 0): (42, 'grass_block'), (1, 0): (43, 'grass_block')}
        ys = gl.ground_heights(live, {(0, 0): 0})
        self.assertEqual(ys[(0, 0)], 42)
        self.assertEqual(ys[(1, 0)], 43)
        self.assertEqual(ys.get('_from_lidar'), 0)

    def test_lidar_base_map_reads_maps_across_columns(self):
        """#172: free columns prefer Maps.get(..., 'terrain'); fall back to 'base' (#167)."""
        fields = []

        class StubMaps:
            def get(self, x, z, field='h'):
                fields.append(field)
                if field == 'terrain':
                    raise KeyError('terrain')
                return 50 if x == 10 else 0

        got = gl.lidar_base_map([(10, 20), (11, 20)], StubMaps())
        self.assertEqual(got, {(10, 20): 50})
        self.assertEqual(fields, ['terrain', 'base', 'terrain', 'base'])

    def test_lidar_base_map_prefers_terrain_over_surface_base(self):
        """#172: car/planter surface above DTM must not raise free ground (I-026).

        Before/after on a cell after ground apply — count road/paving columns
        whose live Y is above the 8-neighbour median of the same kind (expect
        near 0)::

            python3 scripts/run_tests.py --only test_ground_layer
            # in-game / after apply, e.g. cell of I-026:
            # python3 -c "from lidar_heightmap import Maps; m=Maps(); ...
            #   sum(1 for ... if m.get(x,z,'base')>m.get(x,z,'terrain'))"
        """
        class StubMaps:
            def get(self, x, z, field='h'):
                # Surface (DSM top) is 45; bare terrain (DTM) is 40 — a parked car.
                if field == 'terrain':
                    return 40
                if field == 'base':
                    return 45
                raise KeyError(field)

        got = gl.lidar_base_map([(5, 5)], StubMaps())
        self.assertEqual(got, {(5, 5): 40})
        live = {(5, 5): (40, 'stone')}
        ys = gl.ground_heights(live, got)
        self.assertEqual(ys[(5, 5)], 40)
        self.assertEqual(ys.get('_from_lidar'), 1)

    def test_narrow_pit_is_raised_wide_depression_is_not(self):
        """#155: a 3-wide deep pit fills to the rim; a wide sunken area stays low."""
        from block_store import MemoryBlocks
        from site_kit import Plan, World

        # 20×12 patch of level ground at y=44; a 3-wide 8-deep pit; a 10-wide depression.
        live = {(x, z): (44, 'grass_block') for x in range(20) for z in range(12)}
        pit = {(x, z) for x in (5, 6, 7) for z in range(2, 10)}
        wide = {(x, z) for x in range(10, 20) for z in range(2, 10)}
        for q in pit:
            live[q] = (36, 'grass_block')
        for q in wide:
            live[q] = (36, 'grass_block')
        self.assertLessEqual(gl._pit_chebyshev_radius(pit), gl.PIT_MAX_RADIUS)
        self.assertGreater(gl._pit_chebyshev_radius(wide), gl.PIT_MAX_RADIUS)
        kind = {q: 'grass' for q in live}
        ys = gl.level_spikes(live, kind, set())
        ys = gl.level_pits(live, kind, set(), ys)
        for q in pit:
            self.assertEqual(ys[q], 44, q)
        for q in wide:
            self.assertEqual(ys[q], 36, q)

        # MemoryBlocks: raise fill under the new top for the narrow pit only.
        blocks = {}
        for (x, z), (y, b) in live.items():
            blocks[(x, y, z)] = b
            for yy in range(y - 2, y):
                blocks[(x, yy, z)] = 'dirt'
        world = World(store=MemoryBlocks(blocks, default='air'))
        plan = Plan('test_pit')
        for (x, z), (old, _) in live.items():
            gy = ys[(x, z)]
            plan.set(x, gy, z, 'grass_block')
            for y in range(old + 1, gy):
                plan.set(x, y, z, gl.RAISE_FILL)
        for x, z in ((6, 5),):
            self.assertEqual(plan.cells[(x, 44, z)], 'minecraft:grass_block')
            self.assertEqual(plan.cells[(x, 37, z)], 'minecraft:dirt')
        for x, z in ((15, 5),):
            self.assertEqual(plan.cells[(x, 36, z)], 'minecraft:grass_block')
            self.assertNotIn((x, 37, z), plan.cells)

    def test_wall_side_pit_beside_footprint_fills_to_outside_rim(self):
        """#160 / S-019: free columns next to a footprint fill; footprint tops are not the rim."""
        # Outside strip at y=44; 3-wide pit at y=36 hugging the east face of a building
        # whose interior columns sit at y=39 (must not become the fill target).
        live = {(x, z): (44, 'grass_block') for x in range(0, 10) for z in range(0, 8)}
        fp = {(x, z) for x in range(0, 4) for z in range(0, 8)}
        for q in fp:
            live.pop(q, None)  # footprint columns are not free
        pit = {(x, z) for x in (4, 5, 6) for z in range(1, 7)}
        for q in pit:
            live[q] = (36, 'grass_block')
        # Interior-like lows just west of the pit stay in fp (absent from live); add a
        # decoy free column far north that must not pull the rim down.
        kind = {q: 'grass' for q in live}
        ys = gl.level_pits(live, kind, fp)
        for q in pit:
            self.assertEqual(ys[q], 44, q)
        for q in live:
            if q not in pit:
                self.assertEqual(ys[q], 44, q)
        self.assertGreaterEqual(ys.get('_pits_filled', 0), len(pit))
        # Footprint never appears in the height map as a raised free column.
        for q in fp:
            self.assertNotIn(q, ys)

    def test_long_diagonal_thin_band_fills_despite_wide_bbox(self):
        """#160: thin diagonal snake (bbox wide, Chebyshev radius 1) fills; pond does not."""
        live = {(x, z): (44, 'grass_block') for x in range(0, 20) for z in range(0, 24)}
        # 3-wide diagonal with bbox x-span 12 — bbox gate would skip; radius gate must not.
        pit = set()
        for i, z in enumerate(range(2, 20)):
            x0 = 2 + i // 2
            for dx in (0, 1, 2):
                pit.add((x0 + dx, z))
        for q in pit:
            live[q] = (36, 'grass_block')
        # bbox x-span is 12 — old width gate would skip; Chebyshev radius still fills.
        self.assertLessEqual(gl._pit_chebyshev_radius(pit), gl.PIT_MAX_RADIUS)
        kind = {q: 'grass' for q in live}
        ys = gl.level_pits(live, kind, set())
        for q in pit:
            self.assertEqual(ys[q], 44, q)

    def test_path_is_carried_straight_across_shade(self):
        # a 3-wide brick path along x with a 9-long shaded gap; lawn above and below
        kind = {(x, z): 'brick' for x in list(range(0, 8)) + list(range(17, 25)) for z in (10, 11, 12)}
        kind.update({(x, z): 'grass' for x in range(0, 25) for z in list(range(4, 10)) + list(range(13, 19))})
        gap = {(x, z) for x in range(8, 17) for z in range(4, 19)}
        out = gl.bridge_paths(kind, gap)
        self.assertTrue(all(out.get((x, 11)) == 'brick' for x in range(8, 17)))
        self.assertNotIn((12, 5), out)                                     # lawn either side is not path

    def test_unrelated_paths_are_not_joined(self):
        kind = {(x, z): 'paving' for x in range(0, 6) for z in (0, 1)}
        kind.update({(x, z): 'paving' for x in range(0, 6) for z in (20, 21)})
        gap = {(x, z) for x in range(0, 6) for z in range(2, 20)}           # ends face sideways, not each other
        self.assertEqual(gl.bridge_paths(kind, gap), {})

    def test_noise_pass_removes_specks_but_keeps_long_thin_paths(self):
        kind = {(x, z): 'grass' for x in range(30) for z in range(30)}
        kind.update({(x, 15): 'paving' for x in range(30)})               # a 1-wide path, 30 long
        kind[(5, 5)] = 'brick'                                             # a single stray block
        kind.update({(10, 25): 'brick', (11, 25): 'brick'})                # a stray pair
        out = gl.denoise(kind, set(), Counter())
        self.assertEqual(out[(5, 5)], 'grass')
        self.assertEqual(out[(10, 25)], 'grass')
        self.assertEqual(out[(20, 15)], 'paving')

    def test_ignored_builds_forget_old_passes_but_not_neighbours(self):
        ign = gl.ignored_builds((48, -44))
        self.assertIn('highfield_q051_ur', ign)
        self.assertIn('highfield_48_m44_trees', ign)
        self.assertIn('ghost_48_m44_ground', ign)
        self.assertNotIn('ghost_48_m44_features', ign)
        self.assertNotIn('highfield_48_m45_trees', ign)


class TestPlanting(unittest.TestCase):
    """Ground planting uses synthetic plot geometry and an in-memory world only."""

    def setUp(self):
        from types import SimpleNamespace
        from block_store import MemoryBlocks
        from site_kit import Plan, World
        self.lawn = {(x, z) for x in range(2, 20) for z in range(2, 12)}
        self.fp = {(x, 12): 1 for x in range(2, 20)}
        self.idx = SimpleNamespace(cells=lambda: self.fp)
        self.owner = {q: 1 for q in self.lawn}
        self.info = {1: {'street': 'Test Road', 'number': '1', 'land_use': {'class': 'residential'}}}
        self.plots = (self.owner, self.info, {}, self.idx)
        self.segments = {(1, 'front'): [(x, 2) for x in range(2, 20)],
                         (1, 'side_front'): [(2, z) for z in range(2, 12)]}
        self.ys = {q: 50 for q in self.lawn}
        self.blocks = {(x, 50, z): 'grass_block' for x, z in self.lawn}
        self.world = World(store=MemoryBlocks(self.blocks, default='air'))
        self.plan = Plan('planting-test')
        for p, b in self.blocks.items():
            self.plan.set(*p, b)

    def planting(self, **kwargs):
        return gl.plan_planting(self.plan, self.world, self.lawn, self.ys, self.plots, self.segments, **kwargs)

    def test_shrub_density_order_height_and_provenance(self):
        rows = self.planting()
        row = rows[0]
        self.assertEqual(row['shrubs'], len(self.lawn) // 6)
        self.assertEqual(row['items'][0]['position'], [2, 51, 11])  # nearest wall, then boundary, then x,z
        for n, item in enumerate(row['items'], 1):
            x, y, z = item['position']
            self.assertEqual(item['height'], 2 if n % 3 == 0 else 1)
            self.assertEqual(item['label'], 'shrub inference (#265)')
            self.assertEqual(item['provenance']['source'], 'inference')
            self.assertEqual(item['provenance']['confidence'], 'low')
            self.assertIn((x, z), self.lawn)
            for dy in range(item['height']):
                self.assertEqual(self.plan.cells[(x, y + dy, z)], 'minecraft:oak_leaves[persistent=true]')
        positions = [i['position'] for i in row['items']]
        for n, (x, _, z) in enumerate(positions):
            self.assertTrue(all(max(abs(x - xx), abs(z - zz)) > 1 for xx, _, zz in positions[:n]))

    def test_shrubs_exclude_observed_or_non_grass_columns(self):
        observed = {(x, z) for x, z in self.lawn if x < 8}
        guessed = self.lawn - observed
        paved = {(x, z) for x, z in guessed if x < 12}
        for x, z in paved:
            self.plan.set(x, 50, z, 'light_gray_concrete')
        rows = gl.plan_planting(self.plan, self.world, guessed, self.ys, self.plots, self.segments)
        planted = {(x, z) for item in rows[0]['items'] for x, _, z in [item['position']]}
        self.assertTrue(planted)
        self.assertTrue(planted <= guessed - paved)

    def test_shrubs_clear_routes_openings_footprints_masks_and_barriers(self):
        clear = {(x, z) for x, z in self.lawn if x == 10} | {(2, 11)}
        masked = {(4, 90, 11): 'trees 0,0', (6, 70, 11): 'decor 0,0'}
        owned = {(8, 60, 11)}
        self.fp[(12, 11)] = 1
        barrier = {(14, 11)}
        rows = self.planting(clear=clear, masked=masked, owned=owned, barriers=barrier)
        planted = {(x, z) for item in rows[0]['items'] for x, _, z in [item['position']]}
        self.assertTrue(planted)
        self.assertTrue(planted.isdisjoint(clear | set(self.fp) | {(4, 11), (6, 11), (8, 11)}))
        self.assertTrue(all(max(abs(x - 14), abs(z - 11)) > 1 for x, z in planted))

    def test_shrubs_require_known_air_for_every_leaf(self):
        from unittest import mock
        with mock.patch.object(self.world, 'block', side_effect=lambda x, y, z: (
                None if x < 8 else 'minecraft:stone' if x < 14 else 'minecraft:air')):
            rows = self.planting()
        self.assertTrue(rows[0]['items'])
        self.assertTrue(all(item['position'][0] >= 14 for item in rows[0]['items']))

    def test_null_classes_and_missing_class_infer_nothing(self):
        for cls in ('park', 'rural', 'natural', 'water', 'unknown', None):
            self.info[1]['land_use'] = cls
            with self.subTest(cls=cls):
                self.assertEqual(self.planting()[0]['shrubs'], 0)

    def test_planting_uses_class_rule_without_residential_branch(self):
        from unittest import mock
        import land_use
        self.info[1]['land_use'] = {'class': 'park'}
        with mock.patch.object(land_use, 'planting_rule', return_value={'per_columns': 12}) as rule:
            row = self.planting()[0]
        rule.assert_called_once_with('park', 'shrubs')
        self.assertEqual(row['shrubs'], len(self.lawn) // 12)

    def ground_plan(self, world, *, vmap=None, rgb=None, masked=None, owned=()):
        from unittest import mock
        from types import SimpleNamespace
        from site_kit import Plan
        live = {q: (50, 'grass_block') for q in self.lawn}
        ev = {'vmap': vmap if vmap is not None else TestGroundDecisions.Osm(),
              'rgb': rgb if rgb is not None else {q: (.44, .39, .27) for q in self.lawn},
              'private_frontage': self.lawn, 'frontages': [], 'plots': self.plots, 'segments': self.segments,
              'lidar_maps': SimpleNamespace(get=lambda x, z, field='h': 50)}
        plan = Plan('ground-planting-test')
        with mock.patch('osm_footprints.OSMIndex', return_value=self.idx), \
                mock.patch.object(gl, 'free_columns', return_value=(live, Counter())), \
                mock.patch.object(gl, 'stitch_seams', return_value=0):
            stats, conflicts, _, _ = gl.plan_cell((0, 0), masked or {}, set(owned), plan, world, evidence=ev)
        self.assertEqual(conflicts, [])
        return plan, stats

    def test_ground_planting_excludes_mapped_soft_grass(self):
        vmap = TestGroundDecisions.Osm({q: 'grass' for q in self.lawn}, {q: 10 for q in self.lawn})
        plan, stats = self.ground_plan(self.world, vmap=vmap,
                                       rgb={q: (.84, .76, .69) for q in self.lawn})
        self.assertEqual(stats['how: aerial brick away from paths -> lawn'], len(self.lawn))
        self.assertEqual(stats['shrubs'], 0)
        self.assertEqual(stats['planting'][0]['guessed_lawn'], 0)
        self.assertFalse(any('leaves' in b for b in plan.cells.values()))

    def test_ground_shrubs_leave_gap_from_retained_neighbour_shrub(self):
        from block_store import MemoryBlocks
        from site_kit import World
        pos = (1, 51, 11)
        world = World(store=MemoryBlocks({**self.blocks, pos: 'oak_leaves[persistent=true]'}, default='air'))
        plan, stats = self.ground_plan(world, owned={pos})
        self.assertGreater(stats['shrubs'], 0)
        self.assertNotIn(pos, plan.cells)
        self.assertTrue(all(max(abs(item['position'][0] - pos[0]), abs(item['position'][2] - pos[2])) > 1
                            for row in stats['planting'] for item in row['items']))

    def test_ground_shrubs_leave_gap_from_unmapped_masked_fence(self):
        from block_store import MemoryBlocks
        from site_kit import World
        for y in (51, 54):
            for material in ('spruce_fence', 'brick_wall', 'spruce_planks', 'iron_bars',
                             'spruce_shelf[facing=north]', 'pale_oak_shelf[facing=east]'):
                with self.subTest(y=y, material=material):
                    pos = (1, y, 11)
                    world = World(store=MemoryBlocks({**self.blocks, pos: material}, default='air'))
                    plan, stats = self.ground_plan(world, masked={pos: 'decor 0,0'})
                    self.assertGreater(stats['shrubs'], 0)
                    self.assertNotIn(pos, plan.cells)
                    self.assertTrue(all(max(abs(item['position'][0] - pos[0]), abs(item['position'][2] - pos[2])) > 1
                                        for row in stats['planting'] for item in row['items']))

    def test_shrub_clearance_uses_planned_air_over_old_own_shrub(self):
        from block_store import MemoryBlocks
        from site_kit import World
        pos = (3, 51, 11)
        self.world = World(store=MemoryBlocks({**self.blocks, pos: 'oak_leaves[persistent=true]'}, default='air'))
        self.plan.set(*pos, 'air')
        row = self.planting()[0]
        self.assertEqual(row['shrubs'], len(self.lawn) // 6)
        self.assertEqual(row['items'][0]['position'], [2, 51, 11])

    def test_shrub_clearance_reads_planned_air_over_protected_fence(self):
        from block_store import MemoryBlocks
        from site_kit import World
        pos = (3, 51, 11)
        self.world = World(store=MemoryBlocks({**self.blocks, pos: 'spruce_fence'}, default='air'))
        self.plan.set(*pos, 'air')
        row = self.planting(masked={pos: 'decor 0,0'})[0]
        self.assertEqual(row['items'][0]['position'], [2, 51, 11])

    def test_ground_rerun_is_idempotent_and_redo_strips_shrubs(self):
        from block_store import MemoryBlocks
        from site_kit import Plan, World
        from foliage import is_refine_foliage, strip_foliage_above_surface
        first, stats = self.ground_plan(self.world)
        self.assertGreater(stats['shrubs'], 0)
        after = World(store=MemoryBlocks({**self.blocks, **first.cells}, default='air'))
        second, again = self.ground_plan(after)
        self.assertEqual(first.cells, second.cells)
        self.assertEqual(stats['planting'], again['planting'])
        strip = Plan('redo-strip-test')
        strip_foliage_above_surface(strip, after, self.lawn)
        leaves = {p for p, b in first.cells.items() if 'leaves' in b}
        self.assertEqual(set(strip.cells), leaves)
        self.assertTrue(all(b == 'minecraft:air' for b in strip.cells.values()))
        self.assertTrue(all(is_refine_foliage(first.cells[p]) for p in leaves))


class TestWarmSurfaceProbe(unittest.TestCase):
    """The surface probe must batch its reads, not issue one MCP call per block."""

    def test_prefetches_every_probed_block_in_strips(self):
        calls = []

        class Stub:
            def prefetch(self, positions):
                calls.append(set(positions))

        gl.warm_surface_probe(Stub(), 0, 0, 33, 1)
        ylo, yhi = gl.PROBE_Y_RANGE
        self.assertEqual(len(calls), 3)                      # 34 x columns in strips of 16
        got = set().union(*calls)
        self.assertEqual(len(got), 34 * 2 * (yhi - ylo + 1))
        self.assertIn((33, gl.SCAN_TOP, 1), got)
        self.assertEqual(yhi, gl.SCAN_TOP)
        self.assertIn((0, 31, 0), got)

    def test_world_without_prefetch_is_fine(self):
        gl.warm_surface_probe(object(), 0, 0, 3, 3)


if __name__ == '__main__':
    unittest.main()


class TestApplyReporting(unittest.TestCase):
    def test_apply_prints_exclusion_counts_and_saves_all_details(self):
        import io
        import json
        import tempfile
        from contextlib import redirect_stdout
        from pathlib import Path
        from unittest import mock
        from layer_reports import write_report

        excluded = [{'x': x, 'z': 0, 'reason': reason}
                    for reason in ('building footprint', 'water / bank', 'bridge / steps')
                    for x in range(20)]
        retained = [{'position': [x, 65, 0], 'reason': 'tree/decor mask'} for x in range(30)]
        stats = Counter(columns=1, top=1)
        stats['excluded_columns'] = excluded
        stats['retained_positions'] = retained
        skipped = Counter(item['reason'] for item in excluded)

        def plan_cell(cell, masked, owned, plan, world):
            plan.set(0, 64, 0, 'grass_block')
            return stats, [], {(0, 0): 'grass'}, skipped

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict('os.environ', {'SITE_OWNER': 'test'}), \
                mock.patch('readiness_evidence.require_ready'), \
                mock.patch.object(gl, '_fresh_world') as fresh_world, \
                mock.patch.object(gl, 'load_masked_positions', return_value={}), \
                mock.patch.object(gl, 'neighbour_owned', return_value=set()), \
                mock.patch.object(gl, 'plan_cell', side_effect=plan_cell), \
                mock.patch('site_kit.apply', return_value={'planned': 1, 'mismatches': 0}) as apply, \
                mock.patch('layer_reports.write_report', side_effect=lambda n, r: write_report(n, r, Path(tmp))), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(gl.apply_cell((0, 0)), 0)
            saved = json.loads((Path(tmp) / 'highfield_0_0_ground-report.json').read_text())

        self.assertEqual(saved['stats']['excluded_columns'], excluded)
        self.assertEqual(saved['stats']['retained_positions'], retained)
        self.assertEqual(saved['skipped'], dict(skipped))
        self.assertEqual(saved['planned_positions'], 1)
        self.assertEqual(saved['conflicts'], [])
        self.assertIn('skipped: building footprint 20, water / bank 20, bridge / steps 20', output.getvalue())
        self.assertIn('full report:', output.getvalue())
        self.assertNotIn('excluded_columns', output.getvalue())
        self.assertNotIn('retained_positions', output.getvalue())
        self.assertLess(len(output.getvalue().splitlines()), 10)
        fresh_world.assert_called_once_with(strict=True)
        apply.assert_called_once()
        self.assertEqual(apply.call_args.args[0].cells, {(0, 64, 0): 'minecraft:grass_block'})


class TestRegisteredAerial(unittest.TestCase):
    """Registered user aerials (Google 3D render) as a higher-priority colour source (I-042/I-057)."""

    def test_registered_green_is_lawn_but_same_colour_as_mosaic_is_not(self):
        import ground_sources as gs
        green = (0.35, 0.49, 0.38)                                   # Google lawn beside Building 2
        self.assertEqual(gs.aerial_class(gs.RegRGB(green)), 'lawn')
        self.assertNotEqual(gs.aerial_class(green), 'lawn')          # mosaic thresholds untouched (hue > 110)
        self.assertEqual(gs.aerial_class((0.64, 0.60, 0.54)), 'lawn')
        self.assertEqual(gs.aerial_class((0.84, 0.76, 0.69)), 'brick')

    def test_registered_paving_roof_and_brick(self):
        import ground_sources as gs
        R = gs.RegRGB
        self.assertEqual(gs.aerial_class(R((0.85, 0.81, 0.77))), 'light')    # cream promenade (hue 28, s 0.10)
        self.assertEqual(gs.aerial_class(R((0.93, 0.95, 0.96))), 'light')    # white roof / neutral paving
        self.assertEqual(gs.aerial_class(R((0.79, 0.56, 0.55))), 'brick')    # salmon block paving
        self.assertEqual(gs.aerial_class(R((0.72, 0.64, 0.66))), 'brick')    # pale pink block paving
        self.assertEqual(gs.aerial_class(R((0.44, 0.49, 0.61))), 'shade')    # leaning blue roof: ground unseen
        self.assertEqual(gs.aerial_class(R((0.1, 0.1, 0.1))), 'dark')

    def test_registered_rgb_skips_outside_edge_and_black(self):
        import numpy as np
        import ground_sources as gs
        im = np.zeros((200, 200, 3)) + (0.35, 0.49, 0.38)
        im[:, 100:] = 0                                              # black no-data half
        src = {'name': 't', 'A': np.array([[2.0, 0, 0], [0, 2.0, 0]]), 'im': im, 'ppb': 2.0}
        out = gs.registered_rgb([(30, 50), (1, 50), (30, 1), (60, 50), (80, 50)], [src])
        self.assertEqual(set(out), {(30, 50)})                       # (1,50),(30,1) near the edge; (60,50),(80,50) black
        self.assertEqual(gs.aerial_class(out[(30, 50)]), 'lawn')

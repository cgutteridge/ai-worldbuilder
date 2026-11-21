"""Road-name labels: spacing, clear air, SNBT, and summon/kill with no block writes."""
from __future__ import annotations

import math
import unittest

import cellmath
from cell_journal import APPROVAL_LAYERS, ELEMENTS
import road_labels as rl


def way(way_id, name, points, **kwargs):
    return rl.NamedWay(way_id, name, tuple(points), **kwargs)


def air_world(ground=64, leaves=(), bridges=()):
    """Flat lidar and air, with optional leaf columns and bridge columns."""
    leaf_cols = set(leaves)
    bridge_cols = set(bridges)

    def ground_y(x, z):
        return ground

    def block_at(x, y, z):
        if (x, z) in leaf_cols and y >= ground + 1:
            return 'minecraft:oak_leaves'
        return 'minecraft:air'

    def bridge_at(x, z):
        return (x, z) in bridge_cols

    return ground_y, block_at, bridge_at


def plan(ways, cells=None, **world):
    ground_y, block_at, bridge_at = air_world(**world)
    return rl.plan_labels(
        ways, ground_y=ground_y, block_at=block_at, bridge_at=bridge_at,
        cells=None if cells is None else frozenset(cells),
    )


class TestPlacement(unittest.TestCase):
    """Who gets a label, and how far apart they sit."""

    def test_unnamed_ways_are_skipped_quietly(self):
        result = plan([
            way('1', '', [(0, 0), (40, 0)]),
            way('2', '   ', [(0, 10), (40, 10)]),
            way('3', 'Lane', [(0, 20), (40, 20)]),
        ])
        self.assertEqual([label.name for label in result.labels], ['Lane'])
        self.assertEqual(result.skipped, ())

    def test_short_way_gets_one_label_at_the_midpoint(self):
        result = plan([way('1', 'Lane', [(0, 0), (40, 0)])])
        self.assertEqual(len(result.labels), 1)
        label = result.labels[0]
        self.assertEqual((label.x, label.z, label.y), (20.0, 0.0, 67))
        self.assertFalse(label.slid)
        self.assertEqual(label.cell, (0, 0))

    def test_a_way_that_crosses_a_cell_edge_is_not_labelled_once_per_cell(self):
        # 120 blocks from x=60 to x=180 crosses cell 0 and cell 1.
        result = plan([way('1', 'High Street', [(60, 0), (180, 0)])])
        self.assertEqual(len(result.labels), 1)
        self.assertEqual(result.labels[0].cell, cellmath.cell_of(100, 0))
        self.assertLess(result.labels[0].x, 128)

    def test_a_long_way_spaces_labels_between_60_and_100(self):
        result = plan([way('1', 'Long Road', [(0, 0), (400, 0)])])
        xs = [label.x for label in result.labels]
        self.assertGreaterEqual(len(xs), 2)
        gaps = [b - a for a, b in zip(xs, xs[1:])]
        self.assertTrue(all(rl.MIN_SPACING <= gap <= rl.MAX_GAP for gap in gaps))

    def test_the_same_name_does_not_repeat_across_a_cell_boundary(self):
        result = plan([
            way('a', 'Same', [(90, 0), (130, 0)]),
            way('b', 'Same', [(140, 0), (180, 0)]),
        ], cells={(1, 0)})
        self.assertEqual(result.labels, ())
        self.assertEqual(result.skipped[0].reason, 'too close to another label')
        self.assertEqual(result.skipped[0].way_id, 'b')

    def test_two_different_names_both_keep_a_label_at_a_junction(self):
        result = plan([
            way('h', 'High', [(0, 0), (200, 0)]),
            way('s', 'Side', [(100, -80), (100, 80)]),
        ])
        names = {label.name for label in result.labels}
        self.assertEqual(names, {'High', 'Side'})
        high = next(label for label in result.labels if label.name == 'High' and label.junction)
        side = next(label for label in result.labels if label.name == 'Side' and label.junction)
        gap = math.hypot(high.x - side.x, high.z - side.z)
        self.assertLessEqual(gap, rl.JUNCTION_CLUSTER)
        self.assertLess(gap, rl.MIN_SPACING)

    def test_a_tunnel_is_not_labelled(self):
        result = plan([way('t', 'Underpass', [(0, 0), (40, 0)], tunnel=True)])
        self.assertEqual(result.labels, ())
        self.assertEqual(result.skipped[0].reason, 'tunnel')


class TestClearAir(unittest.TestCase):
    """Slide off bridges and out of trees; give up when nothing nearby is clear."""

    def test_a_bridge_column_slides_to_the_nearest_clear_spot(self):
        result = plan(
            [way('1', 'Lane', [(0, 0), (40, 0)])],
            bridges={(20, 0)},
        )
        label = result.labels[0]
        self.assertEqual((label.x, label.z), (21.0, 0.0))
        self.assertTrue(label.slid)

    def test_leaves_above_the_road_count_as_canopy(self):
        result = plan(
            [way('1', 'Lane', [(0, 0), (40, 0)])],
            leaves={(20, 0)},
        )
        self.assertEqual(result.labels[0].x, 21.0)
        self.assertTrue(result.labels[0].slid)

    def test_no_clear_air_within_the_slide_drops_the_label(self):
        bridges = {(x, 0) for x in range(0, 41)}
        result = plan([way('1', 'Lane', [(0, 0), (40, 0)])], bridges=bridges)
        self.assertEqual(result.labels, ())
        self.assertEqual(result.skipped[0].reason, 'no clear air')

    def test_a_missing_chunk_is_not_treated_as_air(self):
        def block_at(x, y, z):
            return None

        result = rl.plan_labels(
            [way('1', 'Lane', [(0, 0), (40, 0)])],
            ground_y=lambda x, z: 64, block_at=block_at, bridge_at=lambda x, z: False,
        )
        self.assertEqual(result.labels, ())
        self.assertEqual(result.skipped[0].reason, 'no clear air')

    def test_missing_lidar_ground_is_not_clear(self):
        def ground_y(x, z):
            return None

        def block_at(x, y, z):
            return 'minecraft:air'

        result = rl.plan_labels(
            [way('1', 'Lane', [(0, 0), (40, 0)])],
            ground_y=ground_y, block_at=block_at, bridge_at=lambda x, z: False,
        )
        self.assertEqual(result.labels, ())
        self.assertEqual(result.skipped[0].reason, 'no clear air')


class TestSnbtAndCommands(unittest.TestCase):
    """The summon NBT matches the 1.21.5 text compound, and undo is a tag kill."""

    def test_text_is_an_snbt_compound_not_a_json_string(self):
        snbt = rl.label_snbt('University Road', (48, -44))
        self.assertTrue(snbt.startswith('{text:{text:"University Road",color:white}'))
        self.assertNotIn('{"text"', snbt)
        self.assertIn('billboard:center', snbt)
        self.assertIn('background:0', snbt)
        self.assertIn('text_opacity:127b', snbt)
        self.assertIn('view_range:4.0f', snbt)
        self.assertIn('scale:[2.0f,2.0f,2.0f]', snbt)
        self.assertIn('"wb_label"', snbt)
        self.assertIn('"wb_cell_48_-44"', snbt)

    def test_quotes_and_backslashes_in_a_name_are_escaped(self):
        snbt = rl.label_snbt('Queen\'s "Road" \\ north', (0, 0))
        self.assertIn('Queen\'s \\"Road\\" \\\\ north', snbt)

    def test_kill_can_cover_one_cell_or_every_label(self):
        self.assertEqual(rl.kill_command(), 'kill @e[tag=wb_label]')
        self.assertEqual(
            rl.kill_command((48, -44)),
            'kill @e[tag=wb_label,tag=wb_cell_48_-44]',
        )

    def test_labels_are_an_approval_layer_and_not_an_element(self):
        self.assertIn('labels', APPROVAL_LAYERS)
        self.assertNotIn('labels', ELEMENTS)
        self.assertEqual(ELEMENTS, ('feature', 'ground', 'trees', 'decor'))


class TestApplyWritesNoBlocks(unittest.TestCase):
    """Summon and kill go through entities. The chunk hop is restored."""

    def test_apply_kills_then_summons_and_restores_the_player(self):
        result = plan([way('1', 'Lane', [(0, 0), (40, 0)])])
        paced = _FakePaced()
        hops = []
        restored = []

        def hop(lo, hi, why):
            hops.append((lo, hi, why))
            return lambda: restored.append(cellmath.cell_of(0, 0))

        texts = rl.apply_labels(paced, result.labels, [(0, 0)], hop=hop)
        names = [name for name, _args in paced.calls]
        self.assertEqual(names, ['entity_query', 'entity_summon'])
        self.assertEqual(paced.calls[0][1]['selector'], '@e[tag=wb_label]')
        self.assertEqual(paced.calls[1][1]['entity_type'], 'minecraft:text_display')
        self.assertNotIn('{"text"', paced.calls[1][1]['nbt'])
        self.assertEqual(len(hops), 1)
        self.assertIn('I will put you back', hops[0][2])
        self.assertEqual(restored, [(0, 0)])
        self.assertEqual(len(texts), 1)   # no old labels to kill, one summon
        self.assertFalse(any(name.startswith('block_') for name, _args in paced.calls))

    def test_apply_kills_only_this_cells_old_labels_before_summoning(self):
        result = plan([way('1', 'Lane', [(0, 0), (40, 0)])])
        paced = _FakePaced(listing=_listing(
            ('aaa', ('wb_label', 'wb_cell_0_0')),
            ('bbb', ('wb_label', 'wb_cell_1_0')),
            ('ccc', ('wb_cell_0_0',)),
        ))
        rl.apply_labels(paced, result.labels, [(0, 0)])
        self.assertEqual(
            [name for name, _args in paced.calls],
            ['entity_query', 'entity_kill', 'entity_summon'],
        )
        self.assertEqual(paced.calls[1][1], {'uuid': 'aaa'})

    def test_never_calls_command_execute(self):
        result = plan([way('1', 'Lane', [(0, 0), (40, 0)])])
        paced = _FakePaced()
        rl.apply_labels(paced, result.labels, [(0, 0)])
        rl.undo_labels(paced, [(0, 0)])
        self.assertNotIn('command_execute', [name for name, _args in paced.calls])

    def test_undo_only_kills(self):
        paced = _FakePaced(listing=_listing(('aaa', ('wb_label', 'wb_cell_1_-1'))))
        rl.undo_labels(paced, [(1, -1)])
        self.assertEqual(
            [name for name, _args in paced.calls],
            ['entity_query', 'entity_kill'],
        )
        self.assertEqual(paced.calls[1][1], {'uuid': 'aaa'})

    def test_label_uuids_reads_the_servers_listing(self):
        listing = _listing(
            ('fa839abc-007d-4fd3-8a85-e7c0dd9382cd', ('wb_label', 'wb_cell_49_-45')),
            ('88ec593f-827f-45e1-ab52-8e26fd5c8ed6', ('wb_label', 'wb_cell_49_-44')),
        )
        self.assertEqual(
            rl.label_uuids(listing, (49, -45)),
            ['fa839abc-007d-4fd3-8a85-e7c0dd9382cd'],
        )
        self.assertEqual(rl.label_uuids('[0]:', (49, -45)), [])

    def test_a_failed_summon_still_restores_the_player(self):
        result = plan([way('1', 'Lane', [(0, 0), (40, 0)])])
        paced = _FakePaced(fail_on='entity_summon')
        restored = []
        with self.assertRaises(RuntimeError):
            rl.apply_labels(
                paced, result.labels, [(0, 0)],
                hop=lambda lo, hi, why: (lambda: restored.append(True)),
            )
        self.assertEqual(restored, [True])

    def test_a_failed_kill_stops_before_any_summon_and_restores_the_player(self):
        result = plan([way('1', 'Lane', [(0, 0), (40, 0)])])
        paced = _FakePaced(
            fail_on='entity_kill',
            listing=_listing(('aaa', ('wb_label', 'wb_cell_0_0'))),
        )
        restored = []
        with self.assertRaises(RuntimeError):
            rl.apply_labels(
                paced, result.labels, [(0, 0)],
                hop=lambda lo, hi, why: (lambda: restored.append(True)),
            )
        self.assertNotIn('entity_summon', [name for name, _args in paced.calls])
        self.assertEqual(restored, [True])

    def test_a_capped_listing_refuses_rather_than_stack_duplicates(self):
        result = plan([way('1', 'Lane', [(0, 0), (40, 0)])])
        paced = _FakePaced(listing=f'[{rl._QUERY_LIMIT}]:\n  - uuid: aaa\n    tags[2]: wb_label,wb_cell_5_5')
        with self.assertRaises(RuntimeError):
            rl.apply_labels(paced, result.labels, [(0, 0)])
        self.assertNotIn('entity_summon', [name for name, _args in paced.calls])

    def test_an_unreadable_listing_refuses_rather_than_stack_duplicates(self):
        paced = _FakePaced(listing='[2]:\n  - id: aaa\n  - id: bbb')
        with self.assertRaises(RuntimeError):
            rl.undo_labels(paced, [(0, 0)])


class TestMapRows(unittest.TestCase):
    """Map property rows become ways, and a bridge centreline blocks a column."""

    def test_name_comes_from_tags_and_a_bridge_is_flagged(self):
        rows = [
            ({'osm_id': '9', 'highway': 'residential', 'name': 'Lane', 'bridge': 'yes'},
             [(0, 0), (10, 0)]),
            ({'osm_id': '8', 'highway': 'residential', 'tunnel': 'yes', 'name': 'Hole'},
             [(0, 5), (10, 5)]),
        ]
        ways = rl.ways_from_map(rows)
        by_id = {item.way_id: item for item in ways}
        self.assertTrue(by_id['9'].bridge)
        self.assertFalse(by_id['9'].tunnel)
        self.assertTrue(by_id['8'].tunnel)
        self.assertEqual(by_id['9'].name, 'Lane')

    def test_a_repeated_way_id_keeps_the_longer_line(self):
        short = ({'osm_id': '9', 'name': 'Lane'}, [(0, 0), (5, 0)])
        long = ({'osm_id': '9', 'name': 'Lane'}, [(0, 0), (5, 0), (30, 0)])
        ways = rl.ways_from_map([short, long])
        self.assertEqual(len(ways), 1)
        self.assertEqual(len(ways[0].points), 3)

    def test_bridge_predicate_matches_columns_near_the_deck(self):
        ways = rl.ways_from_map([
            ({'osm_id': '9', 'name': 'Deck', 'bridge': 'yes'}, [(0, 0), (20, 0)]),
        ])
        bridge_at = rl.bridge_predicate(ways)
        self.assertTrue(bridge_at(10, 0))
        self.assertFalse(bridge_at(10, 30))


def _listing(*entries):
    """An ``entity_query`` listing: ``(uuid, tags)`` pairs in the server's text form."""
    rows = [
        f'  - uuid: {uuid}\n    type: "minecraft:text_display"\n    tags[{len(tags)}]: {",".join(tags)}'
        for uuid, tags in entries
    ]
    return f'[{len(rows)}]:\n' + '\n'.join(rows)


class _FakePaced:
    """Records calls. ``entity_query`` answers with ``listing``; every other call says ok."""

    def __init__(self, fail_on=None, listing='[0]:'):
        self.calls = []
        self.fail_on = fail_on
        self.listing = listing

    def text(self, name, args):
        self.calls.append((name, args))
        if name == self.fail_on:
            raise RuntimeError(name)
        if name == 'entity_query':
            return self.listing
        return 'ok'


if __name__ == '__main__':
    unittest.main()

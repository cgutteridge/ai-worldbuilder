"""Fill transport skips cells the journal begin snapshot already accepts."""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

from journal_testkit import FakePaced, LiveApplyCase
from site_kit import FILL_BATCH, Plan, cells_to_fill


def _writes(cells, signs=()):
    return SimpleNamespace(
        intent=None, kind='build', plan='t', pass_='build', signs=signs, entities=(),
        cells=dict(cells))


class TestCellsToFill(unittest.TestCase):
    def test_no_snapshot_sends_every_cell(self):
        cells = {(0, 60, 0): 'minecraft:stone'}
        self.assertIs(cells_to_fill(cells, None), cells)

    def test_matching_state_is_skipped(self):
        cells = {(0, 60, 0): 'minecraft:stone', (1, 60, 0): 'minecraft:dirt'}
        observed = {(0, 60, 0): 'minecraft:stone', (1, 60, 0): 'minecraft:air'}
        self.assertEqual(cells_to_fill(cells, observed), {(1, 60, 0): 'minecraft:dirt'})

    def test_unknown_and_missing_positions_are_sent(self):
        cells = {(0, 60, 0): 'minecraft:stone', (1, 60, 0): 'minecraft:stone'}
        observed = {(0, 60, 0): None}
        self.assertEqual(set(cells_to_fill(cells, observed)), set(cells))

    def test_readback_ignores_door_open_and_stair_shape(self):
        door = 'minecraft:oak_door[facing=north,half=lower,hinge=left,open=false]'
        open_door = 'minecraft:oak_door[facing=north,half=lower,hinge=left,open=true]'
        stair = 'minecraft:oak_stairs[facing=north,half=bottom,shape=straight]'
        shaped = 'minecraft:oak_stairs[facing=north,half=bottom,shape=inner_left]'
        cells = {(0, 64, 0): door, (1, 64, 0): stair}
        observed = {(0, 64, 0): open_door, (1, 64, 0): shaped}
        self.assertEqual(cells_to_fill(cells, observed), {})
        observed[(1, 64, 0)] = 'minecraft:oak_stairs[facing=south,half=bottom,shape=straight]'
        self.assertEqual(set(cells_to_fill(cells, observed)), {(1, 64, 0)})

    def test_remerge_drops_a_correct_run_and_keeps_the_changed_cell(self):
        plan = Plan('row')
        for x in range(4):
            plan.set(x, 60, 0, 'stone')
        plan.set(8, 60, 0, 'dirt')
        self.assertEqual(len(plan.boxes()), 2)
        pending = cells_to_fill(plan.cells, {
            (x, 60, 0): 'minecraft:stone' for x in range(4)
        })
        boxes = plan.boxes(pending)
        self.assertEqual(boxes, [((8, 60, 0), (8, 60, 0), 'minecraft:dirt')])


class TestMcpWriterFills(unittest.TestCase):
    def _run(self, writes, observed=None):
        from site_kit import McpWriter
        m = mock.Mock()
        m.text.return_value = 'ok'
        m.call.return_value = {'content': [{'type': 'text', 'text': 'done'}]}
        writer = McpWriter(m)
        writer.observed = observed
        with mock.patch('site_kit._bring_player', return_value=None), \
                mock.patch('mc_player.tell_player'):
            writer(writes)
        return m, writer

    def test_batches_above_the_fill_limit(self):
        cells = {(i * 2, 60, 0): 'minecraft:stone' for i in range(FILL_BATCH + 1)}
        m, _writer = self._run(_writes(cells))
        fills = [c.args[1]['fills'] for c in m.text.call_args_list
                 if c.args[0] == 'block_fill_batch']
        self.assertEqual(len(fills), 2)
        self.assertEqual(len(fills[0]), FILL_BATCH)
        self.assertEqual(len(fills[1]), 1)

    def test_matching_snapshot_sends_no_fill_and_is_one_shot(self):
        cells = {(0, 60, 0): 'minecraft:stone', (1, 60, 0): 'minecraft:dirt'}
        observed = dict(cells)
        m, writer = self._run(_writes(cells), observed)
        self.assertEqual(m.text.call_count, 0)
        self.assertIsNone(writer.observed)
        with mock.patch('site_kit._bring_player', return_value=None), \
                mock.patch('mc_player.tell_player'):
            writer(_writes(cells))
        self.assertEqual(m.text.call_args_list[0].args[0], 'block_fill_batch')

    def test_already_correct_slab_is_not_placed_again(self):
        slab = 'minecraft:brick_slab[type=bottom,waterlogged=false]'
        m, _writer = self._run(_writes({(1, 60, 1): slab}), {(1, 60, 1): slab})
        self.assertEqual(m.call.call_count, 0)
        self.assertEqual(m.text.call_count, 0)

    def test_changed_slab_is_still_placed_one_by_one(self):
        slab = 'minecraft:brick_slab[type=bottom,waterlogged=false]'
        m, _writer = self._run(_writes({(1, 60, 1): slab, (2, 60, 1): 'minecraft:bricks'}),
                               {(1, 60, 1): 'minecraft:air', (2, 60, 1): 'minecraft:bricks'})
        self.assertEqual(m.text.call_args_list[0].args[0], 'block_fill_batch')
        fills = m.text.call_args_list[0].args[1]['fills']
        self.assertEqual(fills, [{'from': [1, 60, 1], 'to': [1, 60, 1], 'block': slab}])
        self.assertEqual([c.args[0] for c in m.call.call_args_list], ['block_set_state'])

    def test_signs_are_rewritten_when_the_block_already_matches(self):
        cells = {(0, 64, 0): 'minecraft:oak_sign'}
        signs = (((0, 64, 0), ['hi', '', '', ''], 'black', False),)
        m, _writer = self._run(_writes(cells, signs), dict(cells))
        self.assertEqual(m.text.call_count, 0)
        self.assertEqual([c.args[0] for c in m.call.call_args_list], ['block_entity_set_nbt'])

    def test_nothing_to_send_does_not_move_the_player(self):
        from site_kit import McpWriter
        cells = {(0, 60, 0): 'minecraft:stone'}
        m = mock.Mock()
        writer = McpWriter(m)
        writer.observed = dict(cells)
        with mock.patch('site_kit._bring_player') as hop:
            writer(_writes(cells))
        hop.assert_not_called()
        self.assertEqual(m.text.call_count, 0)


class TestApplySkipsUnchangedFills(LiveApplyCase):
    def test_repeat_apply_of_an_unchanged_plan_sends_no_fills(self):
        plan = Plan('unchanged_fill')
        for x in range(4):
            plan.set(x, 60, 0, 'stone')
        with redirect_stdout(io.StringIO()):
            first = self.run_apply(plan)
        self.assertEqual(first['mismatches'], 0)
        self.assertGreaterEqual(FakePaced.fills_done, 1)
        sent = FakePaced.fills_done
        with redirect_stdout(io.StringIO()):
            second = self.run_apply(plan)
        self.assertEqual(second['mismatches'], 0)
        self.assertEqual(FakePaced.fills_done, sent)
        self.assertEqual(second['counts']['already_correct'], 4)
        self.assertEqual(second['counts']['journal_write_positions'], 4)
        self.assertEqual(second['counts']['verified_positions'], 4)
        self.assertEqual(second['fill_results'], [])
        self.assertEqual(self.entries(plan.name)[-1].status, 'landed')

    def test_mostly_unchanged_apply_fills_only_the_changed_box(self):
        plan = Plan('mostly_fill')
        for x in range(3):
            plan.set(x, 60, 0, 'stone')
            self.blocks.write((x, 60, 0), 'minecraft:stone')
        plan.set(6, 60, 0, 'dirt')
        self.assertEqual(len(plan.boxes()), 2)
        with redirect_stdout(io.StringIO()):
            report = self.run_apply(plan)
        self.assertEqual(report['mismatches'], 0)
        self.assertEqual(report['counts']['already_correct'], 3)
        self.assertEqual(report['counts']['positions_requiring_change'], 1)
        self.assertEqual(report['counts']['journal_write_positions'], 4)
        self.assertEqual(FakePaced.fills_done, 1)
        self.assertEqual(self.blocks.read((6, 60, 0)), 'minecraft:dirt')
        self.assertEqual(self.entries(plan.name)[0].status, 'landed')


if __name__ == '__main__':
    unittest.main()

"""Layer-rebuild fixture helper: ghost base + N rebuilds for undo tests (#133)."""
from __future__ import annotations

import json
import time
import unittest
from unittest import mock

import element_undo
import site_kit
import site_lock
from journal_testkit import (
    LiveApplyCase,
    REBUILD_SHAPES,
    setup_layer_rebuilds,
)

CELL = (48, -44)
X0, Z0 = 6150, -5600
ORIGIN = (X0, 60, Z0)
GREY = 'minecraft:light_gray_concrete'
GRASS = 'minecraft:grass_block'
STONE = 'minecraft:stone'
DIRT = 'minecraft:dirt'
BRICKS = 'minecraft:bricks'


class RebuildFixtureCase(LiveApplyCase):
    """LiveApplyCase with a claimed lock for ``element_undo.run``."""

    def setUp(self):
        super().setUp()
        hb = time.time()
        site_lock.LOCK.write_text(json.dumps({
            'owner': 'alice', 'task': 't', 'claimed': hb, 'heartbeat': hb,
            'claimed_at': 'x', 'heartbeat_at': 'x',
        }))

    def run_live(self, **kw):
        with mock.patch.object(site_lock, 'check', lambda owner=None: None):
            return element_undo.run(**kw)

    def land(self, name, pass_, cells, *, cell=None, modifies=()):
        """``setup_layer_rebuilds`` land callback over ``run_apply``."""
        plan = site_kit.Plan(name)
        for p, s in cells.items():
            plan.set(*p, s)
        kw = {}
        if pass_ == 'other':
            kw.update(stage='other', cell=cell)
        elif pass_ == 'ground':
            kw.update(stage='ground-layer', cell=cell)
        elif pass_ in ('trees', 'decor'):
            kw.update(stage=pass_, cell=cell)
        if modifies:
            kw['modifies'] = list(modifies)
        return self.run_apply(plan, **kw)

    def begin_lines(self, plan=None):
        """Journal ``begin`` build lines, optionally filtered by plan name."""
        path = self.t / 'registry' / 'journal.jsonl'
        out = []
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row.get('op') != 'begin' or row.get('kind') != 'build':
                continue
            if plan is not None and row.get('plan') != plan:
                continue
            out.append(row)
        return out


class TestSetupLayerRebuilds(RebuildFixtureCase):
    def test_default_shapes_are_same_wider_narrower(self):
        self.assertEqual(REBUILD_SHAPES, ('same', 'wider', 'narrower'))

    def test_ghost_base_uses_other_stage_not_ghost_ground(self):
        info = setup_layer_rebuilds(
            self.land, cell=CELL, origin=ORIGIN, shapes=('same',),
            blocks=self.blocks,
        )
        entries = [e for e in self.reg().entries() if e.kind == 'build' and not e.undone]
        self.assertEqual(info['base_name'], 'ghost')
        base = next(e for e in entries if e.plan == 'ghost')
        self.assertEqual(base.pass_, 'other')
        self.assertNotEqual(base.pass_, 'ghost-ground')
        ground = self.begin_lines(plan='g')[0]
        self.assertIn('ghost', ground['modifies'])

    def test_later_rebuild_lists_base_and_same_plan_in_modifies(self):
        setup_layer_rebuilds(
            self.land, cell=CELL, origin=ORIGIN, shapes=('same', 'same'),
            blocks=self.blocks,
        )
        grounds = self.begin_lines(plan='g')
        self.assertEqual(len(grounds), 2)
        self.assertIn('ghost', grounds[0]['modifies'])
        self.assertNotIn('g', grounds[0]['modifies'])
        self.assertIn('ghost', grounds[1]['modifies'])
        self.assertIn('g', grounds[1]['modifies'])

    def test_wider_then_narrower_cover_expected_columns(self):
        far = (X0 + 20, 60, Z0)
        info = setup_layer_rebuilds(
            self.land, cell=CELL, origin=ORIGIN,
            shapes=('same', 'wider', 'narrower'),
            rebuild_blocks=(GRASS, STONE, DIRT),
            blocks=self.blocks,
        )
        self.assertEqual(info['start'][ORIGIN], GREY)
        self.assertEqual(info['start'][far], GREY)
        self.assertEqual(self.blocks.read(ORIGIN), DIRT)  # last (narrower) write
        self.assertEqual(self.blocks.read(far), STONE)    # wider only; narrower skipped it

    def test_interleaved_feature_listed_on_later_rebuild(self):
        mid = (X0 + 1, 60, Z0)
        setup_layer_rebuilds(
            self.land, cell=CELL, origin=ORIGIN,
            shapes=('same', 'wider'),
            far_dx=1,
            interleaved={'name': 'house', 'pos': mid, 'block': BRICKS, 'after': 0},
            blocks=self.blocks,
        )
        grounds = self.begin_lines(plan='g')
        self.assertEqual(len(grounds), 2)
        self.assertNotIn('house', grounds[0]['modifies'])
        self.assertIn('house', grounds[1]['modifies'])
        # second rebuild is wider (far_dx=1) and covers mid over the house
        self.assertEqual(self.blocks.read(ORIGIN), STONE)
        self.assertEqual(self.blocks.read(mid), STONE)


class TestPropertyUndoRebuildsNoBase(RebuildFixtureCase):
    def test_undo_n_rebuilds_without_base_restores_start_world(self):
        # Property: for any N, rebuilds with no base owner then cell-pass undo
        # leave the world equal to the start (the bent #123 fixture hid this).
        for n in (1, 2, 3, 5):
            with self.subTest(n=n):
                self._fresh_world()
                shapes = tuple(['same'] * n)
                blocks = (GRASS, STONE, DIRT, BRICKS, 'minecraft:cobblestone')
                info = setup_layer_rebuilds(
                    self.land, cell=CELL, origin=ORIGIN, shapes=shapes,
                    with_base=False, rebuild_blocks=blocks[:n],
                    blocks=self.blocks, start_block=GREY,
                )
                self.assertIsNone(info['base_name'])
                self.assertEqual(self.run_live(cell=CELL, pass_name='ground', live=True), 0)
                for pos, want in info['start'].items():
                    self.assertEqual(self.blocks.read(pos), want, msg=f'n={n} pos={pos}')

    def _fresh_world(self):
        """Reset MemoryBlocks + journal between property cases (one setUp for the class)."""
        import cell_registry
        self.blocks._blocks.clear()
        self.blocks.flush()
        from journal_testkit import FakePaced
        FakePaced.reset(self.blocks)
        journal = self.t / 'registry' / 'journal.jsonl'
        if journal.exists():
            journal.write_text('')
        # Drop cached registry views if any
        cell_registry.view_registry(self.t)


class TestUndoViaFixture(RebuildFixtureCase):
    def test_cell_pass_undo_after_rebuild_shapes_restores_ghost_base(self):
        info = setup_layer_rebuilds(
            self.land, cell=CELL, origin=ORIGIN,
            shapes=('same', 'wider', 'narrower'),
            blocks=self.blocks,
        )
        self.assertEqual(self.run_live(cell=CELL, pass_name='ground', live=True), 0)
        for pos, want in info['start'].items():
            self.assertEqual(self.blocks.read(pos), want)

    def test_interleaved_shared_column_undo_restores_feature(self):
        mid = ORIGIN
        setup_layer_rebuilds(
            self.land, cell=CELL, origin=ORIGIN,
            shapes=('same', 'same'),
            interleaved={'name': 'house', 'pos': mid, 'block': BRICKS, 'after': 0},
            blocks=self.blocks,
        )
        self.assertEqual(self.run_live(cell=CELL, pass_name='ground', live=True), 0)
        self.assertEqual(self.blocks.read(mid), BRICKS)


if __name__ == '__main__':
    unittest.main()

"""Ownership fold equivalence (task 343): the bulk per-cell table matches the old per-position replay.

The oracle below is the pre-343 ``CellRegistry._cell_owners`` loop, kept verbatim in spirit. The
fixture journal is written by hand so it can hold states ``change()`` would refuse: begun and
abandoned entries, undone builds, ghost-ground under landed and mismatched real ground, cancelling
``air`` / ``light`` blocks, multi-cell blobs and repeated plans.
"""
from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path

from block_store import MemoryBlocks, block_id
from cell_journal import AIR, CANCEL, GHOST_PASSES, CellRegistry, _Owner

SIZE = 16
STONE, DIRT, GREY, LOG = 'minecraft:stone', 'minecraft:dirt', 'minecraft:gray_concrete', 'oak_log[axis=y]'
LIGHT = 'minecraft:light[level=15]'


def oracle_cell_owners(reg: CellRegistry, cell, *, exclude=frozenset(), skip=frozenset()) -> dict:
    """The pre-343 ownership replay, one position at a time."""
    owners: dict = {}
    for seq in reg._by_cell.get(cell, ()):
        e = reg._entries[seq]
        if e.kind != 'build' or e.undone or e.status == 'begun' or seq in skip or e.plan in exclude:
            continue
        ghost = e.pass_ in GHOST_PASSES
        tops: dict = {}
        for pos, state in reg._blob(e.blob).items():
            if (pos[0] // SIZE, pos[2] // SIZE) != cell:
                continue
            if block_id(state) in CANCEL:
                owners.pop(pos, None)
            else:
                owners[pos] = _Owner(seq, e.plan, state, ghost, e.pass_)
                if e.pass_ == 'ground':
                    xz = (pos[0], pos[2])
                    if pos[1] > tops.get(xz, -10 ** 9):
                        tops[xz] = pos[1]
        if e.pass_ == 'ground' and e.status == 'landed' and e.cell == cell and tops:
            for pos in [p for p, o in owners.items() if o.pass_ == 'ghost-ground']:
                top = tops.get((pos[0], pos[2]))
                if top is not None and pos[1] > top:
                    del owners[pos]
    return owners


def column(x, z, y0, y1, state):
    return {(x, y, z): state for y in range(y0, y1)}


class Journal:
    """Hand-written journal lines with real blobs."""

    def __init__(self, root: Path):
        self.root = root
        self.reg = CellRegistry(root, MemoryBlocks(default=AIR), None, cell_size=SIZE)
        self.lines: list[dict] = []

    def _line(self, **rec) -> int:
        rec['seq'] = len(self.lines) + 1
        self.lines.append(rec)
        return rec['seq']

    def begin(self, plan, pass_, cell, cells: dict, *, kind='build', targets=()) -> int:
        key = self.reg._store_blob(cells)
        touched = sorted({(x // SIZE, z // SIZE) for x, _y, z in cells} | {tuple(cell)})
        return self._line(op='begin', kind=kind, plan=plan, **{'pass': pass_}, cell=list(cell),
                          cells=[list(c) for c in touched], blob=key, n=len(cells),
                          targets=list(targets))

    def finish(self, ref, status='landed') -> None:
        self._line(op='finish', ref=ref, status=status)

    def build(self, plan, pass_, cell, cells, status='landed') -> int:
        seq = self.begin(plan, pass_, cell, cells)
        if status != 'begun':
            self.finish(seq, status)
        return seq

    def write(self) -> CellRegistry:
        d = self.root / 'registry'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'journal.jsonl').write_text(''.join(json.dumps(l) + '\n' for l in self.lines))
        return CellRegistry(self.root, MemoryBlocks(default=AIR), None, cell_size=SIZE)


class OwnershipEquivalenceTest(unittest.TestCase):
    CELLS = [(0, 0), (1, 0), (0, 1), (1, 1), (-1, 0), (5, 5)]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        j = Journal(Path(tmp.name))
        ghost00 = {}
        for x in range(0, 16, 2):
            for z in range(0, 16, 3):
                ghost00.update(column(x, z, 55, 66, GREY))      # ghost substrate and above-top part
        self.ghost00 = j.build('ghost_0_0_ground', 'ghost-ground', (0, 0), ghost00)
        j.build('ghost_1_0_ground', 'ghost-ground', (1, 0),
                {(16 + i, 60, 4): GREY for i in range(10)})
        # real ground in (0, 0): covers some ghost columns, with cancelling air and light
        ground = {}
        for x in range(0, 16, 4):
            for z in range(0, 16, 3):
                ground.update(column(x, z, 58, 62, STONE))
        ground[(0, 70, 0)] = AIR                         # cancels nothing yet owned there
        ground[(2, 65, 0)] = 'air'                       # cancels ghost above
        ground[(2, 64, 3)] = LIGHT
        ground[(17, 60, 4)] = STONE                      # spills into (1, 0)
        self.ground = j.build('ground_0_0', 'ground', (0, 0), ground)
        # mismatched ground in (1, 0): owns, but does not supersede ghost
        j.build('ground_1_0', 'ground', (1, 0), {(18, 59, 4): DIRT, (19, 58, 4): DIRT},
                status='mismatched')
        # feature spanning four cells, built twice (later wins, same plan name)
        feat = {(x, 63, z): 'minecraft:bricks' for x in range(12, 20) for z in range(12, 20)}
        j.build('house_a', 'feature', (0, 0), feat)
        feat2 = dict(feat)
        feat2[(13, 63, 13)] = AIR
        feat2[(18, 63, 18)] = 'minecraft:oak_planks'
        self.house2 = j.build('house_a', 'feature', (0, 0), feat2)
        # trees cancelling a ground position, then a begun (in-flight) and an abandoned build
        j.build('trees_0_0', 'trees', (0, 0), {(4, 62, 0): LOG, (4, 61, 0): AIR, (8, 62, 3): LOG})
        j.build('decor_0_0', 'decor', (0, 0), {(4, 63, 0): 'minecraft:poppy'}, status='begun')
        j.build('decor_0_0b', 'decor', (0, 0), {(8, 63, 3): 'minecraft:poppy'}, status='abandoned')
        # an undone build: undo entry landed with targets
        self.trees2 = j.build('trees_0_1', 'trees', (0, 1), {(1, 64, 17): LOG, (2, 64, 17): LOG})
        u = j.begin('undo_trees_0_1', 'trees', (0, 1), {(1, 64, 17): AIR}, kind='undo',
                    targets=[self.trees2])
        j.finish(u)
        j.build('trees_0_1', 'trees', (0, 1), {(3, 64, 17): LOG})
        # a second landed ground in (0, 0) after more ghost, and a neighbour-cell ground in (-1, 0)
        j.build('ghost_0_0_ground', 'ghost-ground', (0, 0), column(6, 6, 50, 70, GREY))
        j.build('ground_0_0', 'ground', (0, 0), column(6, 6, 55, 60, STONE))
        j.build('ground_m1_0', 'ground', (-1, 0), {(-1, 60, 0): STONE, (0, 66, 0): STONE})
        self.reg = j.write()
        self.reg._sync()

    def variants(self):
        yield frozenset(), frozenset()
        yield frozenset({'house_a'}), frozenset()
        yield frozenset({'ground_0_0'}), frozenset()
        yield frozenset({'ghost_0_0_ground', 'trees_0_0'}), frozenset()
        yield frozenset(), frozenset({self.ground})
        yield frozenset({'nope'}), frozenset({self.house2})

    def test_cell_owners_match_the_old_replay_in_content_and_order(self):
        for exclude, skip in self.variants():
            for cell in self.CELLS:
                with self.subTest(cell=cell, exclude=sorted(exclude), skip=sorted(skip)):
                    want = oracle_cell_owners(self.reg, cell, exclude=exclude, skip=skip)
                    got = self.reg._cell_owners(cell, exclude=exclude, skip=skip)
                    self.assertEqual(list(want.items()), list(got.items()))

    def test_fixture_exercises_every_rule(self):
        owners = oracle_cell_owners(self.reg, (0, 0))
        passes = {o.pass_ for o in owners.values()}
        self.assertTrue({'ghost-ground', 'ground', 'feature', 'trees'} <= passes)
        self.assertNotIn((0, 64, 0), owners)              # ghost above real top dropped
        self.assertEqual(owners[(0, 57, 0)].pass_, 'ghost-ground')   # substrate kept
        self.assertEqual(owners[(2, 64, 0)].pass_, 'ghost-ground')   # unwritten ghost column kept
        self.assertNotIn((4, 61, 0), owners)              # trees air cancelled ground
        self.assertNotIn((13, 63, 13), owners)            # rebuilt feature cancelled its own brick
        self.assertNotIn((4, 63, 0), owners)              # begun decor owns nothing
        self.assertNotIn((8, 63, 3), owners)              # abandoned decor owns nothing
        self.assertNotIn((2, 64, 17), oracle_cell_owners(self.reg, (0, 1)))   # undone
        self.assertIn((3, 64, 17), oracle_cell_owners(self.reg, (0, 1)))
        self.assertIn((17, 60, 4), oracle_cell_owners(self.reg, (1, 0)))      # spilled ground

    def test_owners_owned_and_states_match_the_old_merge(self):
        for exclude, _skip in self.variants():
            for real_only in (False, True):
                with self.subTest(exclude=sorted(exclude), real_only=real_only):
                    cells = self.CELLS
                    want = {}
                    for cell in {tuple(c) for c in cells}:
                        for p, o in oracle_cell_owners(self.reg, cell, exclude=exclude).items():
                            if not (real_only and o.ghost):
                                want[p] = (o.plan, o.state)
                    got = self.reg.owners(cells, exclude, real_only=real_only)
                    self.assertEqual(list(want.items()), list(got.items()))
                    states = self.reg.owner_states(cells, exclude, real_only=real_only)
                    self.assertEqual([(p, s) for p, (_pl, s) in want.items()], list(states.items()))
                    probe = list(want) + [(100, 1, 100), (0, 0, 0), (-1, 60, 0)]
                    random.Random(7).shuffle(probe)
                    owned = self.reg.owned(probe, exclude, real_only=real_only)
                    self.assertEqual({p: v for p, v in want.items()}, owned)

    def test_owners_in_matches_with_skip(self):
        positions = [p for c in self.CELLS for p in oracle_cell_owners(self.reg, c)]
        positions += [(0, 64, 0), (13, 63, 13)]
        for exclude, skip in self.variants():
            want = {}
            for p in positions:
                o = oracle_cell_owners(self.reg, (p[0] // SIZE, p[2] // SIZE),
                                       exclude=exclude, skip=skip).get(p)
                if o is not None:
                    want[p] = o
            got = self.reg._owners_in(positions, exclude=exclude, skip=skip)
            self.assertEqual(want, got)

    def test_random_journals_match(self):
        rng = random.Random(343)
        states = [STONE, DIRT, GREY, LOG, AIR, 'air', LIGHT, 'minecraft:cave_air']
        passes = ['ghost-ground', 'ground', 'feature', 'trees', 'decor']
        for trial in range(20):
            with tempfile.TemporaryDirectory() as tmp, self.subTest(trial=trial):
                j = Journal(Path(tmp))
                for _ in range(rng.randint(3, 12)):
                    cell = (rng.randint(0, 1), rng.randint(0, 1))
                    cells = {}
                    for _ in range(rng.randint(1, 60)):
                        x = cell[0] * SIZE + rng.randint(-3, 18)
                        z = cell[1] * SIZE + rng.randint(-3, 18)
                        cells[(x, rng.randint(55, 70), z)] = rng.choice(states)
                    j.build(f'p{rng.randint(0, 4)}', rng.choice(passes), cell, cells,
                            status=rng.choice(['landed', 'landed', 'mismatched', 'begun', 'abandoned']))
                reg = j.write()
                reg._sync()
                plans = {f'p{i}' for i in range(5)}
                for cell in [(0, 0), (1, 0), (0, 1), (1, 1), (-1, 0)]:
                    for exclude in (frozenset(), frozenset(rng.sample(sorted(plans), 2))):
                        want = oracle_cell_owners(reg, cell, exclude=exclude)
                        got = reg._cell_owners(cell, exclude=exclude)
                        self.assertEqual(list(want.items()), list(got.items()))


if __name__ == '__main__':
    unittest.main()

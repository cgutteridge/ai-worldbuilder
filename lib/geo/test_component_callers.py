"""Ordering regressions for component callers consolidated into cellmath (#399)."""
import random
import unittest
from collections import Counter
from unittest import mock

import cellmath
import garden_sources as gs
import ground_layer as gl


def _legacy_denoise(kind: dict, locked: set, how: Counter) -> dict:
    """Noise reduction: a small isolated patch of one kind takes the kind that surrounds it.

    Patches are 4-connected runs of one kind. One smaller than gl.MIN_PATCH, that is not mapped tarmac
    (roads and car parks are real however small), is re-labelled to the most common kind on its
    border. Thin features survive because they are long: a 2-wide path is 8 columns after 4 blocks.
    Runs three times so a patch exposed by the first pass can still go.
    """
    out = dict(kind)
    for _ in range(3):
        changed = 0
        for k in gl.MIN_PATCH:
            cols = {q for q, v in out.items() if v == k}
            left = set(cols)
            while left:
                seed = left.pop()
                comp, todo = {seed}, [seed]
                while todo:
                    q = todo.pop()
                    for n in ((q[0] + 1, q[1]), (q[0] - 1, q[1]), (q[0], q[1] + 1), (q[0], q[1] - 1)):
                        if n in left:
                            left.discard(n)
                            comp.add(n)
                            todo.append(n)
                if len(comp) >= gl.MIN_PATCH[k] or comp & locked:
                    continue
                border = Counter(out[(q[0] + a, q[1] + b)] for q in comp for a in (-1, 0, 1) for b in (-1, 0, 1)
                                 if (q[0] + a, q[1] + b) in out and (q[0] + a, q[1] + b) not in comp)
                if border:
                    new = border.most_common(1)[0][0]
                    for q in comp:
                        out[q] = new
                    changed += len(comp)
        how['noise removed'] += changed
        if not changed:
            break
    return out


class ComponentCallerOrderTest(unittest.TestCase):
    def test_denoise_preserves_border_ties_and_sequential_relabelling(self):
        # The old Counter broke ties by component-set encounter order. Include
        # diagonal patches and unlocked neighbours relabelled earlier in a pass.
        samples = [
            {(x, z): ('grass' if (x, z) == (0, 0) else
                       'stone' if x < 0 else 'sand')
             for x in range(-1, 2) for z in range(-1, 2)},
            {(0, 0): 'grass', (1, 1): 'grass', (0, 1): 'sand',
             (1, 0): 'stone', (2, 1): 'paving', (2, 0): 'brick'},
        ]
        rng = random.Random(399)
        for _ in range(80):
            samples.append({(x, z): rng.choice(('grass', 'paving', 'brick', 'stone', 'sand'))
                            for x in range(-3, 4) for z in range(-3, 4)})
        for kind in samples:
            for locked in (set(), {(0, 0), (1, 1)}):
                old_how, new_how = Counter(), Counter()
                expected = _legacy_denoise(kind, locked, old_how)
                self.assertEqual(gl.denoise(kind, locked, new_how), expected)
                self.assertEqual(new_how, old_how)

    def test_garden_preserves_sorted_members_and_component_minimum_order(self):
        cols = [(0, 0), (10, 0), (11, 0), (12, 0), (1, 1), (2, 2)]
        owner = {q: 1 for q in cols}
        self.assertEqual(gs.flatten_panel_run(cols, 1, owner), sorted(cols))
        # Both are short; use longer groups to exercise the actual walk order.
        cols += [(3, 3), (13, 0)]
        with mock.patch.object(gs, 'walk', wraps=gs.walk) as walk:
            gs.flatten_panel_run(cols, 1, {q: 1 for q in cols})
        self.assertEqual([c.args[0] for c in walk.call_args_list],
                         [[(0, 0), (1, 1), (2, 2), (3, 3)],
                          [(10, 0), (11, 0), (12, 0), (13, 0)]])

    def test_garden_diagonal_tie_retains_facing_closers_and_output_order(self):
        cols = [(0, 0), (1, 1), (2, 2), (10, 0), (11, 0), (12, 0)]
        run = gs.panel_runs(cols, 1, 'front', {q: 1 for q in cols}, {}, (2, 4))
        expected_cells = [(0, 0), (0, 1), (1, 1), (1, 2), (2, 2), (10, 0), (11, 0), (12, 0)]
        self.assertEqual(run['cells'], expected_cells)
        self.assertEqual(list(run['facing'].items()),
                         [('0,0', 'north'), ('0,1', 'west'), ('1,1', 'north'),
                          ('1,2', 'west'), ('2,2', 'north'), ('10,0', 'north'),
                          ('11,0', 'north'), ('12,0', 'north')])
        self.assertEqual(run['closers'], [(0, 1), (1, 2)])
        self.assertEqual(run['cross'], {'0,1': 'west', '1,2': 'west'})
        self.assertTrue(gs.boards_joined(run))

    def test_outward_words_keep_garden_tie_order(self):
        self.assertEqual(list(cellmath.FACING), ['east', 'west', 'south', 'north'])
        self.assertEqual(cellmath.FACING['north'], cellmath.DIRS['N'])
        self.assertEqual(cellmath.FACING['south'], cellmath.DIRS['S'])

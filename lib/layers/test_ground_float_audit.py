"""Unit tests for the read-only floating ground audit (#296); synthetic journal owners only."""
import unittest
from types import SimpleNamespace

import ground_float_audit as gfa


def column(x, z, owners):
    """``{(x, y, z): (plan, state)}`` from ``{y: (plan, short block)}``."""
    return {(x, y, z): (plan, f'minecraft:{b}') for y, (plan, b) in owners.items()}


GHOST = {y: ('ghost', 'stone') for y in range(1, 37)}


class TestFloatingGroundTops(unittest.TestCase):
    def setUp(self):
        self.owners = {
            **column(0, 0, {**GHOST, 49: ('g', 'andesite')}),                                    # floats
            **column(1, 0, {**GHOST, **{y: ('g', 'dirt') for y in range(37, 49)}, 49: ('g', 'andesite')}),
            **column(2, 0, {**GHOST, 47: ('ghost', 'stone'), 48: ('ghost', 'stone'), 50: ('g', 'grass_block')}),
            **column(3, 0, {**GHOST, 37: ('g', 'grass_block'), 38: ('g', 'air')}),               # cleared above
            **column(4, 0, {**GHOST, 49: ('ghost', 'stone')}),                                   # ghost only
        }

    def test_lists_a_floating_column_and_only_those(self):
        got = gfa.floating_ground_tops(self.owners, {'g'})
        self.assertEqual([(r['x'], r['z'], r['surface_y'], r['support_y'], r['gap']) for r in got],
                         [(0, 0, 49, 36, 12), (2, 0, 50, 36, 11)])
        self.assertEqual(got[0]['plan'], 'g')

    def test_audit_reads_cells_with_live_ground_from_the_view(self):
        entries = [SimpleNamespace(kind='build', pass_='ground', plan='g', cell=(0, 0), undone=False, status='landed'),
                   SimpleNamespace(kind='build', pass_='ground', plan='old', cell=(1, 0), undone=True, status='landed'),
                   SimpleNamespace(kind='build', pass_='ghost-ground', plan='ghost', cell=(0, 0), undone=False,
                                   status='landed')]
        view = SimpleNamespace(entries=lambda: entries, owners=lambda cells: self.owners)
        got = gfa.audit(view)
        self.assertEqual(list(got), [(0, 0)])
        self.assertEqual(len(got[(0, 0)]), 2)



class TestWhoseFloatingColumn(unittest.TestCase):
    """#297: each floating column names whose it is to settle: the ground's or a feature's."""

    PASSES = {'ghost': 'ghost-ground', 'g': 'ground', 'west': 'feature', 'hall': 'feature'}

    def setUp(self):
        self.owners = {
            # a door stoop buried at y43 under a ground top at y49, unowned air between: ground's
            **column(0, 0, {**GHOST, **{y: ('ghost', 'stone') for y in range(37, 43)},
                            43: ('west', 'smooth_stone'), 47: ('ghost', 'stone'), 48: ('ghost', 'stone'),
                            49: ('g', 'gray_concrete')}),
            # a feature wall through the old top: the feature's
            **column(1, 0, {**GHOST, 40: ('hall', 'bricks'), 49: ('g', 'gray_concrete'), 50: ('hall', 'bricks')}),
            # the gap is the feature's own clearance air above its stoop: the feature's
            **column(2, 0, {**GHOST, **{y: ('ghost', 'stone') for y in range(37, 43)},
                            43: ('west', 'smooth_stone'), 44: ('west', 'air'), 45: ('west', 'air'),
                            46: ('g', 'dirt'), 47: ('g', 'dirt'), 48: ('g', 'dirt'), 49: ('g', 'gray_concrete')}),
            # no feature at all: ground's
            **column(3, 0, {**GHOST, 49: ('g', 'andesite')}),
        }

    def test_audit_names_the_owner_and_whose_column_it_is(self):
        got = {r['x']: r for r in gfa.floating_ground_tops(self.owners, {'g'}, self.PASSES)}
        self.assertEqual(sorted(got), [0, 1, 2, 3])
        self.assertEqual((got[0]['belongs'], got[0]['owner'], got[0]['owner_y'], got[0]['gap']),
                         ('ground', 'west', 43, 3))
        self.assertEqual((got[1]['belongs'], got[1]['owner'], got[1]['owner_y']), ('feature', 'hall', 50))
        self.assertEqual((got[2]['belongs'], got[2]['owner']), ('feature', 'west'))
        self.assertEqual((got[3]['belongs'], got[3]['owner']), ('ground', None))
        self.assertIn("feature hall's", gfa.describe(got[1]))
        self.assertIn('above west y43', gfa.describe(got[0]))

    def test_clear_names_the_feature_command_or_the_ground_redo(self):
        from unittest import mock
        entries = [SimpleNamespace(kind='build', pass_='feature', plan='hall', cell=(0, 1), undone=False,
                                   status='landed'),
                   SimpleNamespace(kind='build', pass_='ground', plan='g', cell=(0, 0), undone=False, status='landed'),
                   SimpleNamespace(kind='build', pass_='trees', plan='t', cell=(0, 0), undone=False, status='landed'),
                   SimpleNamespace(kind='build', pass_='ghost-ground', plan='ghost', cell=(0, 0), undone=False,
                                   status='landed')]
        view = SimpleNamespace(entries=lambda cells=None: entries, owners=lambda cells: self.owners)
        rows = {r['x']: r for r in gfa.audit(view, [(0, 0)])[(0, 0)]}
        self.assertEqual([rows[x]['belongs'] for x in (0, 1)], ['ground', 'feature'])
        with mock.patch('cell_readiness.reapply_command', return_value='REAPPLY') as reapply:
            self.assertEqual(gfa.clear_command(view, (0, 0), rows[1]), 'REAPPLY')
        reapply.assert_called_once_with('hall', 'feature', (0, 1), registry=view)   # the feature's own cell
        ground = gfa.clear_command(view, (0, 0), rows[0])
        self.assertIn('redo_layer.py', ground)
        self.assertIn('--layers ground,trees', ground)

if __name__ == '__main__':
    unittest.main()

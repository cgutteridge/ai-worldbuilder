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


if __name__ == '__main__':
    unittest.main()

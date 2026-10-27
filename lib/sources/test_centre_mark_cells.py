"""Centre-line dashes must be one cell wide (#147 / I-020, S-025)."""
from __future__ import annotations

import unittest

import ground_sources as gs


def _dash_width(marks: set[tuple[int, int]], axis: str) -> set[int]:
    """Perpendicular span sizes along the road (``axis`` is the along axis: 'x' or 'z')."""
    buckets: dict[int, set[int]] = {}
    for x, z in marks:
        key, cross = (x, z) if axis == 'x' else (z, x)
        buckets.setdefault(key, set()).add(cross)
    return {len(cols) for cols in buckets.values()}


class CentreMarkCellsTest(unittest.TestCase):
    def test_block_centre_line_is_one_cell_wide(self):
        """A centre-line through block centres was already one wide; keep that."""
        cells = gs.line_cells([(0.5, 5.5), (20.5, 5.5)], half=4.0)
        marks = gs.centre_mark_cells(cells)
        self.assertTrue(marks)
        self.assertEqual(_dash_width(marks, 'x'), {1})
        self.assertEqual({z for _, z in marks}, {5})

    def test_block_edge_line_is_one_cell_wide_not_two(self):
        """A centre-line on a block edge must not paint both neighbours (#147)."""
        cells = gs.line_cells([(0.0, 5.0), (20.0, 5.0)], half=4.0)
        old = {q for q, (d, a) in cells.items() if d <= 0.5 and int(a) % 6 < 3}
        self.assertEqual(_dash_width(old, 'x'), {2}, 'precondition: old rule is two wide on an edge')
        marks = gs.centre_mark_cells(cells)
        self.assertTrue(marks)
        self.assertEqual(_dash_width(marks, 'x'), {1})
        # Tie-break prefers the lexicographically smaller neighbour (lower z here).
        self.assertEqual({z for _, z in marks}, {4})

    def test_vertical_block_edge_line_is_one_cell_wide(self):
        cells = gs.line_cells([(5.0, 0.0), (5.0, 20.0)], half=4.0)
        marks = gs.centre_mark_cells(cells)
        self.assertTrue(marks)
        self.assertEqual(_dash_width(marks, 'z'), {1})
        self.assertEqual({x for x, _ in marks}, {4})

    def test_dashes_stay_three_on_three_off(self):
        """Thinning width must not collapse the 3-on / 3-off dash along the line."""
        cells = gs.line_cells([(0.0, 5.0), (24.0, 5.0)], half=4.0)
        marks = gs.centre_mark_cells(cells)
        along = sorted(x for x, _ in marks)
        # First dash occupies three consecutive columns, then a three-column gap.
        self.assertEqual(along[:3], [0, 1, 2])
        self.assertEqual(along[3:6], [6, 7, 8])

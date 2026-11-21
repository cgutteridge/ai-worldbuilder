"""Admission of hand-off tree-line crowns after the source gate (spike #323), synthetic inputs."""
from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tree_lines_trial as cli  # noqa: E402


@dataclass
class Inputs:
    crowns: list
    aerial_sheet: set
    crown_origins: list | None = None
    source_counts: dict = field(default_factory=dict)


class AdmitTest(unittest.TestCase):
    def test_admits_only_on_line_evidence(self):
        cell = (1, -1)
        x0, z0 = 128, -128
        inputs = Inputs(crowns=[(10, 10, 3.0)], aerial_sheet=set(), crown_origins=[(x0 + 10, z0 + 10)],
                        source_counts={'detected': 1})
        belt = {(x0 + 50, z0 + 50)}
        rows = [{'x': x0 + 51, 'z': z0 + 50, 'r': 3},      # on evidence
                {'x': x0 + 90, 'z': z0 + 90, 'r': 3},      # open pasture: refused
                {'x': x0 + 11, 'z': z0 + 10, 'r': 3},      # duplicates a retained crown
                {'x': x0 - 5, 'z': z0 + 10, 'r': 3}]       # outside the cell
        belt |= {(x0 + 11, z0 + 10), (x0 - 5, z0 + 10)}
        new, added, refused = cli.admit(inputs, cell, rows, belt)
        self.assertEqual(added, [(x0 + 51, z0 + 50, 3.0)])
        self.assertEqual(sorted(r[2] for r in refused),
                         ['duplicate of a retained crown', 'no hedge or lane line evidence', 'outside cell'])
        self.assertEqual(new.crowns[-1], (51, 50, 3.0))
        self.assertEqual(new.crown_origins[-1], (x0 + 51, z0 + 50))
        self.assertIn((x0 + 51, z0 + 50), new.aerial_sheet)
        self.assertEqual(new.source_counts, {'detected': 1, 'tree_lines': 1})
        self.assertEqual(len(inputs.crowns), 1)            # input not mutated


if __name__ == '__main__':
    unittest.main()

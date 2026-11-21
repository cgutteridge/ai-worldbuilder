"""Admission of hand-off tree-line crowns after the source gate (spike #323), synthetic inputs."""
from __future__ import annotations

import sys
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tree_lines_trial as cli  # noqa: E402
from trees_layer import TreeInputs  # noqa: E402


class AdmitTest(unittest.TestCase):
    def test_admits_only_on_line_evidence(self):
        cell = (1, -1)
        x0, z0 = 128, -128
        inputs = TreeInputs(detected=1, extra=set(), owned=set(), ground_cols=None, masked={}, footprints=set(), crowns=[(10, 10, 3.0)], aerial_sheet=set(), crown_origins=[(x0 + 10, z0 + 10)],
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


class LinesPlanTest(unittest.TestCase):
    def test_additive_trial_reuses_current_crowns_species_and_height_provenance(self):
        import trees_layer
        inputs = TreeInputs([], 0, set(), set(), set(), None, {}, set(), height_source='last-return')
        report = {'trees': [(20, 20, 3., 'tree')], 'skipped': [(40, 40, 3., 'water')],
                  'rooting': [], 'species': {'birch': 1},
                  'tree_species': [{'position': [20, 20], 'species': 'birch', 'class': 'unknown'}]}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(cli, 'load_handoff', return_value=[{'x': 20, 'z': 20, 'r': 3}]), \
                mock.patch('tree_lines.propose', return_value={'height_source': 'first-return',
                                                              '_evidence': {'belt': {(20, 20)}}}), \
                mock.patch('site_kit.fresh_planning_world', return_value=object()), \
                mock.patch.object(trees_layer, 'planning_inputs', return_value=inputs), \
                mock.patch.object(trees_layer, 'plan_cell', return_value=({(20, 1, 20): 'birch_log'}, report)) as plan, \
                mock.patch('ground_layer.ignored_builds', return_value=()), \
                mock.patch('site_kit.preview'), mock.patch('site_kit.apply') as apply, \
                mock.patch.object(cli, '_artifact', side_effect=lambda c, suffix: Path(tmp) / suffix):
            self.assertEqual(cli.cmd_lines((0, 0), Path('synthetic.json'), live=False), 0)
            record = json.loads((Path(tmp) / 'lines-dry.json').read_text())
            self.assertEqual(record['trees'], [[20, 20, 3., 'tree']])
            self.assertEqual(record['tree_species'], report['tree_species'])
            self.assertEqual(record['species'], {'birch': 1})
            self.assertEqual(record['height_source'], 'last-return')
            self.assertEqual(record['line_height_source'], 'first-return')
            self.assertEqual(plan.call_args.args[3].crowns, [(20, 20, 3.)])
            self.assertTrue(apply.call_args.kwargs['dry'])


class SeamTest(unittest.TestCase):
    def test_reader_comparisons_use_shared_contract_and_restore_overrides(self):
        import tree_lines
        import trees_layer
        for reader, expected_first in [('auto', True), ('first', True), ('last', False)]:
            with self.subTest(reader=reader), \
                    mock.patch('tree_lines.standing_height', return_value=(None, 'first-return')) as read:
                def run(cell):
                    tree_lines.standing_height(cell, margin=2)
                    return 0
                with mock.patch.object(trees_layer, 'run', side_effect=run):
                    self.assertEqual(cli.cmd_seam((0, 0), Path('synthetic.json'), reader), 0)
                self.assertIs(tree_lines.standing_height, read)
                if reader == 'auto':
                    read.assert_called_once_with((0, 0), margin=2)
                else:
                    read.assert_called_once_with((0, 0), margin=2, prefer_first=expected_first)

    def test_required_first_reader_refuses_fallback(self):
        import trees_layer
        original_path = trees_layer.EXTRA_CROWNS
        def run(cell):
            import tree_lines
            tree_lines.standing_height(cell, margin=2)
        with mock.patch('tree_lines.standing_height', return_value=(None, 'last-return')), \
                mock.patch.object(trees_layer, 'run', side_effect=run):
            with self.assertRaisesRegex(SystemExit, 'first-return standing height unavailable'):
                cli.cmd_seam((0, 0), Path('synthetic.json'), 'first')
        self.assertEqual(trees_layer.EXTRA_CROWNS, original_path)


if __name__ == '__main__':
    unittest.main()

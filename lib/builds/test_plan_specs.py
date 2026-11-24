"""#461: a regenerated read replaces the spec of the built plan it covers, so a rebuild uses it (no world data)."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import feature_spec as FS
import plan_specs as P
import typology_spec as T
from test_typology_spec import STREET, building, rect

PITCHED = {
    'name': 'Fixture',
    'classes': {'commercial': {'palette': ['bricks'], 'roofs': ['gable'], 'storeys': {'min': 2, 'max': 2}}},
}
RETAIL = {'building': 'retail', 'building:levels': '2'}
REAL_BUILT_PLANS = P.built_plans


def entry(plan, path, builder='feature_build', undone=False):
    return SimpleNamespace(plan=plan, kind='build', pass_='feature', status='landed', undone=undone,
                           meta={'builder_name': builder, 'spec_path': str(path)})


class Registry:
    def __init__(self, entries):
        self._entries = entries

    def entries(self, plan=None):
        return [e for e in self._entries if plan is None or e.plan == plan]


def write(path, name, ways):
    path.write_text(json.dumps({'name': name, 'ways': {w: 'A' for w in ways}}))
    return path


class BuiltPlans(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name).resolve()
        self.addCleanup(self.tmp.cleanup)

    def test_only_feature_build_plans_with_a_readable_spec_are_matched(self):
        registry = Registry([
            entry('w1', write(self.dir / 'w1.json', 'w1', ['1'])),
            entry('w2', write(self.dir / 'w2.json', 'w2', ['2', '3'])),
            entry('h4', write(self.dir / 'h4.json', 'h4', ['4']), builder='house_build'),
            entry('w5', self.dir / 'absent.json'),
            entry('w6', write(self.dir / 'w6.json', 'w6', ['6']), undone=True),
        ])
        plans, skipped = P.built_plans(registry)
        self.assertEqual({name: info['ways'] for name, info in plans.items()}, {'w1': ['1'], 'w2': ['2', '3']})
        self.assertEqual({row['plan']: row['reason'] for row in skipped},
                         {'h4': 'built by house_build, not feature_build',
                          'w5': 'recorded spec file is missing'})

    def test_way_plans_take_one_way_plans_and_name_the_rest(self):
        plans = {'w1': {'path': 'a', 'ways': ['1']}, 'w2': {'path': 'b', 'ways': ['2', '3']},
                 'w7': {'path': 'c', 'ways': ['7']}, 'w8': {'path': 'd', 'ways': ['7']}}
        mapping, skipped = P.way_plans(plans)
        self.assertEqual(mapping, {'1': 'w1'})
        self.assertEqual({row['plan'] for row in skipped}, {'w2', 'w7', 'w8'})

    def test_replace_paths_refuses_a_spec_whose_ways_differ_from_the_plan(self):
        item = {'plan': 'w1', 'spec': {'ways': {'9': 'a'}}}
        with self.assertRaisesRegex(SystemExit, 'not replacing'):
            P.replace_paths({'specs': [item]}, {'w1': {'path': self.dir / 'w1.json', 'ways': ['1']}})


class Route(unittest.TestCase):
    """typology_spec --replan: regenerate, replace the plan's spec, and the rebuild reads the new one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name).resolve()
        self.addCleanup(self.tmp.cleanup)
        self.old = write(self.dir / 'w2.json', 'w2', ['2'])
        self.old.write_text(json.dumps({
            'name': 'w2', 'ways': {'2': 'A'}, 'storeys': {'A': 1}, 'roofs': {'A': {'kind': 'flat', 'material': 'brick'}},
            'groups': [{'nos': ['A'], 'mode': 'each', 'front': 'S'}], 'wall': 'bricks'}))
        self.shops = [building(i + 1, rect(i * 6, 0, 6, 7), dict(RETAIL), lidar=7.0, profile=PITCHED)
                      for i in range(3)]
        self.registry = Registry([entry('w2', self.old)])

    def run_cli(self, *args):
        out = io.StringIO()
        with mock.patch('osm_footprints.OSMIndex'), \
                mock.patch.object(T, 'load_buildings', return_value=(self.shops, STREET)), \
                mock.patch('plan_specs.built_plans', side_effect=lambda: REAL_BUILT_PLANS(self.registry)), \
                redirect_stdout(out):
            self.assertEqual(T.main(['--ways', '1', '2', '3', '--no-read', '--replan', *args]), 0)
        return json.loads(out.getvalue())

    def test_dry_run_names_the_plan_its_read_and_the_file_it_would_replace(self):
        before = self.old.read_text()
        report = self.run_cli()
        self.assertEqual(self.old.read_text(), before)
        self.assertNotIn('wrote', report)
        self.assertEqual(report['plan_summary'],
                         [f'w2 (way 2): 2 storey(s), pitched roof brick, stairs fit; replaces {self.old}'])
        self.assertEqual(report['plans_skipped'], [])

    def test_write_replaces_only_the_plan_spec_and_the_rebuild_would_read_it(self):
        report = self.run_cli('--write', '--out', str(self.dir / 'out'))
        self.assertIn(str(self.old), report['wrote'])
        self.assertTrue((self.dir / 'out' / 'typology_shop_1.json').is_file())   # the other ways, one row spec
        spec = FS.load_spec(self.old)           # the file reapply_feature validates and rebuilds from
        self.assertEqual((spec['name'], spec['ways']), ('w2', {'2': 'a'}))
        self.assertEqual(spec['storeys'], {'a': 2})
        self.assertEqual(spec['roofs']['a']['kind'], 'pitched')
        self.assertEqual(spec['roofs']['a']['stairs'], 'fit')

    def test_a_second_write_replaces_the_plan_again_but_never_the_row_spec(self):
        self.run_cli('--write', '--out', str(self.dir / 'out'))
        with self.assertRaisesRegex(SystemExit, 'already exists'):
            self.run_cli('--write', '--out', str(self.dir / 'out'))

    def test_without_replan_nothing_is_split(self):
        out = io.StringIO()
        with mock.patch('osm_footprints.OSMIndex'), \
                mock.patch.object(T, 'load_buildings', return_value=(self.shops, STREET)), \
                redirect_stdout(out):
            T.main(['--ways', '1', '2', '3', '--no-read'])
        report = json.loads(out.getvalue())
        self.assertEqual(len(report['specs']), 1)
        self.assertNotIn('plan', report['specs'][0])
        self.assertNotIn('plan_summary', report)


if __name__ == '__main__':
    unittest.main()

"""Tests for door_audit --plans on a synthetic world (#249)."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import script_env  # noqa: E402,F401

import door_audit as da  # noqa: E402

LOW = 'spruce_door[facing=north,half=lower,hinge=left,open=false]'
UP = 'spruce_door[facing=north,half=upper,hinge=left,open=false]'


class FakeWorld:
    def __init__(self, blocks):
        self.blocks = blocks

    def block(self, x, y, z):
        return self.blocks.get((x, y, z), 'minecraft:air')


def build_door(world, plan_blocks, x, *, upper=True, door=True, street=True, clear=True):
    """Door at (x, 65, 10) facing north: outward is z=9. Floor y=64."""
    plan_blocks[(x, 65, 10)] = LOW
    if upper:
        plan_blocks[(x, 66, 10)] = UP
    world[(x, 64, 10)] = 'stone'
    if street:
        world[(x, 64, 9)] = 'stone'
    if door:
        world[(x, 65, 10)] = LOW
    if upper and door:
        world[(x, 66, 10)] = UP
    if not clear:
        world[(x, 66, 9)] = 'oak_fence'


class FakeRegistry:
    def __init__(self, plans):
        self.plans = plans

    def entries(self):
        return [SimpleNamespace(kind='build', pass_='feature', status='landed', undone=False, plan=p)
                for p in self.plans]

    def recorded(self, plan):
        return self.plans[plan]


class DoorAuditPlans(unittest.TestCase):
    def setUp(self):
        self.world, self.plan = {}, {}
        build_door(self.world, self.plan, 0)                                    # good
        build_door(self.world, self.plan, 10, door=False)                       # missing
        build_door(self.world, self.plan, 20)
        del self.world[(20, 66, 20 - 20 + 10)]                                  # half_height
        build_door(self.world, self.plan, 30, street=False)                     # below_street
        build_door(self.world, self.plan, 40, clear=False)                      # blocked
        self.reg = FakeRegistry({'houses_x': self.plan})

    def run_audit(self, patterns=('*',)):
        return da.audit_plans(FakeWorld(self.world), self.reg, patterns)

    def test_one_failure_per_class_and_good_door_passes(self):
        n, bad = self.run_audit()
        self.assertEqual(n, 5)
        self.assertEqual({(x, k) for _p, x, _y, _z, k in bad},
                         {(10, 'missing'), (20, 'half_height'), (30, 'below_street'), (40, 'blocked')})

    def test_pattern_filters_plans(self):
        self.assertEqual(self.run_audit(('other_*',)), (0, []))

    def test_door_failure_good(self):
        build_door(self.world, {}, 50)
        self.assertIsNone(da.door_failure(FakeWorld(self.world), 50, 65, 10, 'N'))

    def test_exit_code_is_one_on_failure(self):
        from unittest import mock
        with mock.patch.object(da, 'World', return_value=FakeWorld(self.world)), \
                mock.patch.object(da, 'view_registry', return_value=self.reg), \
                mock.patch('osm_footprints.OSMIndex') as idx:
            idx.return_value.cells.return_value = {}
            self.assertEqual(da.plans_main(['*']), 1)
            self.assertEqual(da.plans_main(['nothing*']), 0)


if __name__ == '__main__':
    unittest.main()

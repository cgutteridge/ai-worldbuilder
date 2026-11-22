"""Barrier vocabulary: plan kinds, convention defaults, plank tones and the fence alias."""
from __future__ import annotations

import unittest

import barrier_vocab as bv
from tree_species import DEFAULT_LEAVES


class TestBarrierVocabulary(unittest.TestCase):
    """One owner for kinds, defaults and the ``fence`` alias."""

    def test_plan_kinds_exclude_the_fence_alias(self):
        self.assertEqual(bv.BARRIER_KINDS, frozenset({
            'hedge', 'brick_wall', 'iron_bars', 'spruce_fence', 'plank_fence'}))
        self.assertNotIn(bv.FENCE_ALIAS, bv.BARRIER_KINDS)
        self.assertEqual(bv.HEDGE_BLOCK, DEFAULT_LEAVES)
        self.assertEqual(bv.DEFAULT_BLOCK['hedge'], DEFAULT_LEAVES)
        self.assertEqual(bv.DEFAULT_BLOCK['plank_fence'], 'oak_planks')
        self.assertEqual(bv.DEFAULT_HEIGHT['spruce_fence'], 2)
        self.assertEqual(bv.PLANK_HEIGHT, bv.DEFAULT_HEIGHT['plank_fence'])

    def test_convention_defaults_match_plan_kinds_except_the_fence_alias(self):
        for kind in ('hedge', 'brick_wall', 'iron_bars'):
            self.assertEqual(bv.CONVENTION_BLOCK[kind], bv.DEFAULT_BLOCK[kind])
            self.assertEqual(bv.CONVENTION_HEIGHT[kind], bv.DEFAULT_HEIGHT[kind])
        self.assertEqual(bv.CONVENTION_BLOCK['fence'], 'spruce_fence')
        self.assertEqual(bv.CONVENTION_HEIGHT['fence'], bv.DEFAULT_HEIGHT['spruce_fence'])
        self.assertNotIn('spruce_fence', bv.CONVENTION_BLOCK)
        self.assertNotIn('plank_fence', bv.CONVENTION_BLOCK)

    def test_fence_alias_and_tone_table(self):
        self.assertEqual(bv.barrier_kind('fence', 'minecraft:spruce_planks'), 'plank_fence')
        self.assertEqual(bv.barrier_kind('fence', 'oak_planks'), 'plank_fence')
        self.assertEqual(bv.barrier_kind('fence', 'pale_oak_planks'), 'plank_fence')
        self.assertEqual(bv.barrier_kind('fence', None), 'spruce_fence')
        self.assertEqual(bv.barrier_kind('fence', 'oak_fence'), 'spruce_fence')
        self.assertEqual(bv.barrier_kind('fence', 'spruce_fence'), 'spruce_fence')
        self.assertEqual(bv.barrier_kind('picket'), 'picket')
        self.assertEqual(bv.barrier_kind('spruce_fence', 'oak_planks'), 'spruce_fence')
        self.assertEqual(bv.PLANK_TONES['oak'], ('oak_planks',))
        self.assertIn('mangrove_planks', bv.PLANK_TONES['reddy'])
        self.assertIn('pale_oak_planks', bv.PLANK_BLOCKS)
        self.assertNotIn('oak_fence', bv.PLANK_BLOCKS)

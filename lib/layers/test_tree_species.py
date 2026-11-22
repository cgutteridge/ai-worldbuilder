"""The one tree-species table (#378): golden voxels, and a fixture species that is one row."""
import copy
import hashlib
import json
import random
import unittest
from unittest import mock

import land_use
import tree_species as ts
import tree_voxels as tv
import trees_layer as tl


def digest(voxels):
    return len(voxels), hashlib.sha256(repr(sorted(voxels.items())).encode()).hexdigest()


# Recorded from trees_layer.voxels_for before the species table was shared (cell origin, ground 60).
GOLDEN = {
    ('oak', 4.0, 20, 24): (248, 'd60523aac3b5847e15865c3f6dd8ae6c3f4015aaec43bc155a42c328243c4e2a'),
    ('oak', 6.5, -7, 33): (199, '7e39ade5cad1b0d608eea0f3d3b9b857f3a6ed51734abea7c2874642086316da'),
    ('oak', 1.5, 20, 24): (6, 'cb1e3383b34cb4372cd2809a37f7dab2566dd42ca9de24ae0c889ae8e2637c21'),
    ('birch', 4.0, 20, 24): (53, 'e8a71d60b6be63b00e89b9288e7b1746e5e979f8d0912b00e659e0266272cbcf'),
    ('birch', 6.5, 101, -55): (55, '3bef8d46f1e702c70b2fc21ace48a0547a84bdee9d758f78bb0f836aa51d1d72'),
    ('dark_oak', 4.0, 20, 24): (132, 'a203eda7f2528de7d55f75930ee1347c0707ea067a94f3d8677dfec5bfee6308'),
    ('dark_oak', 3.0, -7, 33): (136, 'f135bc0aa4c9f96b263838be7bb8e0a8effa5c15befbc7bfe362654f47fd9abe'),
}


def table_with_weights(weights):
    doc = json.loads(land_use.TABLE_PATH.read_text())
    for row in doc['classes']:
        if row['id'] == 'natural':
            row['trees']['species_weights'] = weights
    return copy.deepcopy(doc)


class SpeciesTableGoldenTest(unittest.TestCase):
    def test_voxels_for_matches_the_pre_table_output(self):
        for (species, r, x, z), expected in GOLDEN.items():
            with self.subTest(species=species, r=r, x=x, z=z):
                self.assertEqual(digest(tl.voxels_for(x, z, 60, r, species=species)), expected)

    def test_mixable_species_keep_their_order_and_the_shipped_mix_validates(self):
        self.assertEqual(ts.mixable(), ('oak', 'birch', 'dark_oak'))
        land_use.validate_table(table_with_weights({'oak': 60, 'birch': 25, 'dark_oak': 15}))

    def test_species_outside_the_table_or_without_a_silhouette_are_refused(self):
        for name in ('elm', 'spruce', 'cherry'):
            with self.subTest(name), self.assertRaises(land_use.TableError) as caught:
                land_use.validate_table(table_with_weights({name: 1}))
            self.assertIn("('oak', 'birch', 'dark_oak')", str(caught.exception))

    def test_one_default_leaf_block(self):
        self.assertEqual(ts.SPECIES['oak'].leaves, ts.DEFAULT_LEAVES)
        self.assertEqual(set(tv.bush_voxels(0, 0, 0).values()), {ts.DEFAULT_LEAVES})


class FixtureSpeciesIsOneRowTest(unittest.TestCase):
    ROW = ts.Species('spruce_log', 'spruce_leaves[persistent=true]', crown=(2.5, 4.0), trunk_width=1,
                     heights=(9, 10))

    def test_a_row_flows_through_validation_voxels_and_heights(self):
        with mock.patch.dict(ts.SPECIES, {'spruce': self.ROW}):
            self.assertIn('spruce', ts.mixable())
            land_use.validate_table(table_with_weights({'oak': 1, 'spruce': 2}))
            vox = tl.voxels_for(20, 24, 60, 4.0, species='spruce')
            self.assertEqual(set(vox.values()), {'spruce_log', 'spruce_leaves[persistent=true]'})
            trunk = sorted(y for (x, y, z), b in vox.items() if (x, z) == (20, 24) and b == 'spruce_log')
            self.assertIn(len(trunk), self.ROW.heights)
            self.assertEqual(trunk[0], 61)
            self.assertEqual(digest(vox), digest(tv.tree_voxels(
                20, 24, 60, height=len(trunk), species='spruce', style='silhouette',
                rng=random.Random(tl.RNG_SEED + 20 * 31 + 24))))
        self.assertNotIn('spruce', ts.mixable())
        with self.assertRaises(land_use.TableError):
            land_use.validate_table(table_with_weights({'spruce': 1}))

    def test_a_silhouette_needs_crown_and_heights_together(self):
        with self.assertRaises(ValueError):
            ts.Species('x_log', 'x_leaves', crown=(2.0, 3.0))

    def test_silhouette_style_refuses_a_species_without_one(self):
        with self.assertRaises(ValueError):
            tv.tree_voxels(0, 0, 0, height=5, species='spruce', style='silhouette')
        with self.assertRaises(ValueError):
            tv.tree_voxels(0, 0, 0, height=5, style='birch')


if __name__ == '__main__':
    unittest.main()

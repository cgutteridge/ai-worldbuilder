"""Golden output of the tree and bush voxel generators (recorded before the module was renamed)."""
import hashlib
import random
import unittest

import tree_voxels as tv

# (voxel count, sha256 of the sorted voxel items), recorded from plant_cell_canopy before its removal.
GOLDEN = {
    'oak_round': (123, '01593f2eb53f5744a45b6920c22b270edf65dc06171adac786d3823a2bbad598'),
    'oak_round_default_rng': (124, '38c2ab17d74cd680b3081b70334c94adcff65e5217ec3e4bcecbd716076097c3'),
    'oak_plane': (231, '89d322cf17de96c0b8da6641ec4305d89440bbb619f6ec3c493b843439a62128'),
    'oak_plane_species': (193, 'd0f2557b712fee71f81dd05fdda4335d6e083f61cfb3ec854179e94577c2d39c'),
    'birch': (55, 'a351a4c00f42ccec8abccfe7e081ab90380048dc6fa155cc581363d3a4e04966'),
    'dark_oak': (136, '40062b5239b2cd13061dedab334079b3ad393cdcd9ee3f15781a5ec2ba3f9d14'),
    'bush': (6, '991db57f855fd94cc19dbdfbad40f52c950bc2545fbb096fe88cebd3598daaca'),
}


def digest(voxels):
    return len(voxels), hashlib.sha256(repr(sorted(voxels.items())).encode()).hexdigest()


class TreeVoxelsGoldenTest(unittest.TestCase):
    def check(self, name, voxels):
        self.assertEqual(digest(voxels), GOLDEN[name], name)

    def test_oak_round(self):
        self.check('oak_round', tv.tree_voxels(10, 20, 50, height=5, style='round', rng=random.Random(1)))

    def test_oak_round_default_rng(self):
        self.check('oak_round_default_rng', tv.tree_voxels(-7, 33, 60, height=6, style='round'))

    def test_oak_plane(self):
        self.check('oak_plane', tv.tree_voxels(10, 20, 50, height=10, style='plane', rng=random.Random(2)))

    def test_oak_plane_with_species(self):
        self.check('oak_plane_species', tv.tree_voxels(
            3, -4, 41, height=12, style='plane', species='oak', rng=random.Random(3)))

    def test_birch(self):
        self.check('birch', tv.tree_voxels(
            10, 20, 50, height=8, style='silhouette', species='birch', rng=random.Random(4)))

    def test_dark_oak(self):
        self.check('dark_oak', tv.tree_voxels(
            10, 20, 50, height=7, style='silhouette', species='dark_oak', rng=random.Random(5)))

    def test_bush(self):
        self.check('bush', tv.bush_voxels(10, 20, 50))

    def test_rng_seed_is_unchanged(self):
        self.assertEqual(tv.RNG_SEED, 48045)


if __name__ == '__main__':
    unittest.main()

"""#159: building evidence table carries map roof/height hints and snapped entrances."""
from __future__ import annotations

import unittest

import building_evidence as BE


def _props(**tags) -> dict:
    """Fixture footprint properties: top-level columns plus other_tags hstore."""
    top = {k: v for k, v in tags.items() if k in ('building', 'name', 'osm_id', 'osm_way_id')}
    other = {k: v for k, v in tags.items() if k not in top}
    p = dict(top)
    if other:
        p['other_tags'] = ','.join(f'"{k}"=>"{v}"' for k, v in other.items())
    return p


class MapHintsEvidenceTable(unittest.TestCase):
    """Proof: roof:shape / roof:levels / height sit beside lidar as hints only."""

    def test_fixture_footprint_tags_appear_as_hints_beside_lidar(self):
        props = _props(
            building='university',
            osm_way_id='1001',
            **{
                'building:levels': '4',
                'roof:shape': 'hipped',
                'roof:levels': '1',
                'height': '14 m',
            },
        )
        hints = BE.map_building_hints(props)
        self.assertEqual(hints['roof:shape'], 'hipped')
        self.assertEqual(hints['roof:levels'], '1')
        self.assertEqual(hints['height'], '14 m')
        self.assertEqual(hints['building:levels'], '4')

        lidar = {'storeys': 3, 'roof': 'flat', 'ridge_axis': 'x', 'height': 11}
        table = BE.evidence_table(lidar=lidar, map_hints=hints)
        text = '\n'.join(BE.format_evidence_table(table))

        # Lidar remains the height / roof authority in the table body.
        self.assertIn('lidar', text.lower())
        self.assertIn('3 storeys', text)
        self.assertIn('roof flat', text)

        # Map tags are labelled hints, never an override of lidar figures.
        self.assertIn('hint', text.lower())
        self.assertIn('roof:shape=hipped', text)
        self.assertIn('roof:levels=1', text)
        self.assertIn('height=14 m', text)
        self.assertIn('building:levels=4', text)
        # Hints are labelled as such; lidar storeys stay the estimate (not replaced by 4).
        self.assertIn('never override', text.lower())
        self.assertIn('3 storeys', text)
        self.assertNotIn('4 storeys', text)

    def test_missing_map_tags_yield_empty_hints(self):
        self.assertEqual(BE.map_building_hints(_props(building='yes', osm_id='9')), {})
        table = BE.evidence_table(lidar={'storeys': 2, 'roof': 'pitched'}, map_hints={})
        text = '\n'.join(BE.format_evidence_table(table))
        self.assertIn('lidar', text.lower())
        self.assertNotIn('roof:shape', text)

    def test_hints_alone_without_lidar_still_labelled_hint(self):
        hints = BE.map_building_hints(_props(
            building='yes',
            **{'roof:shape': 'gabled', 'height': '8'},
        ))
        table = BE.evidence_table(lidar=None, map_hints=hints)
        text = '\n'.join(BE.format_evidence_table(table))
        self.assertIn('hint', text.lower())
        self.assertIn('roof:shape=gabled', text)


class EntranceSnapAndRank(unittest.TestCase):
    """Proof: entrance=* nodes snap to the footprint edge and rank main first."""

    # Axis-aligned 10×10 footprint: outer ring (MC floats), closed.
    RINGS = [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]]

    def test_entrance_node_snaps_to_nearest_footprint_edge(self):
        # Point sits 2 blocks south of the south edge (z=0).
        snapped = BE.snap_entrance_to_edge(5.2, -2.0, self.RINGS)
        self.assertEqual(snapped['at'], (5, 0))
        self.assertAlmostEqual(snapped['raw'][0], 5.2, places=5)
        self.assertAlmostEqual(snapped['raw'][1], -2.0, places=5)
        self.assertLess(snapped['dist'], 2.1)

    def test_entrances_ranked_main_before_service_and_emergency(self):
        nodes = [
            {'x': 5.0, 'z': -1.0, 'entrance': 'emergency', 'osm_id': '3'},
            {'x': 9.0, 'z': 5.0, 'entrance': 'service', 'osm_id': '2'},
            {'x': 1.0, 'z': 5.0, 'entrance': 'main', 'osm_id': '1'},
            {'x': 5.0, 'z': 11.0, 'entrance': 'yes', 'osm_id': '4'},
        ]
        ranked = BE.mapped_entrance_candidates(nodes, self.RINGS)
        kinds = [c['entrance'] for c in ranked]
        self.assertEqual(kinds[:3], ['main', 'yes', 'service'])
        self.assertEqual(kinds[-1], 'emergency')
        # Every candidate carries a snapped integer column on the edge.
        for c in ranked:
            self.assertIn('at', c)
            ax, az = c['at']
            self.assertTrue(ax == 0 or ax == 10 or az == 0 or az == 10)

    def test_format_lists_candidates_for_facade_plan(self):
        nodes = [
            {'x': 1.0, 'z': 5.0, 'entrance': 'main', 'osm_id': '1'},
            {'x': 9.0, 'z': 5.0, 'entrance': 'service', 'osm_id': '2'},
        ]
        lines = BE.format_entrance_candidates(
            BE.mapped_entrance_candidates(nodes, self.RINGS),
        )
        text = '\n'.join(lines)
        self.assertIn('main', text)
        self.assertIn('service', text)
        self.assertIn('candidate', text.lower())
        # main appears before service in the brief.
        self.assertLess(text.index('main'), text.index('service'))


class EntranceExtractPreset(unittest.TestCase):
    """Entrance points are part of the standard cell extract vocabulary."""

    def test_entrances_preset_is_in_cell_extract_layers(self):
        import osm_cell_extract as oce
        self.assertIn('entrances', oce.CELL_EXTRACT_LAYERS)
        presets: dict = {}
        oce.install_cell_extract_layer_presets(presets)
        self.assertIn('entrances', presets)
        gdal_layer, where, _models = presets['entrances'][0]
        self.assertEqual(gdal_layer, 'points')
        self.assertIn('entrance', where.lower())


if __name__ == '__main__':
    unittest.main()

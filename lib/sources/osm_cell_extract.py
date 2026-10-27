"""Standard per-cell OSM extract job list (landscape set).

The world ``osm_extract`` provider owns buildings / highways / landuse / railway
presets. Landscape families the planner layers already read (water, waterway,
barriers, furniture, pedestrian areas, natural, leisure) live here so every
world gets the same vocabulary via ``shims/osm_extract.py``.

**Standard step:** ``python3 shims/osm_extract.py --at x,z --cell --place PLACE``
(or a place bbox). When ``--layers`` is omitted the shim inserts
:data:`CELL_EXTRACT_LAYERS`. Agents and ``cell_sources.fetch_commands`` use the
same list — no hand-picked ``buildings,highways`` subset.
"""
from __future__ import annotations

from street_furniture import EXTRACT_JOBS as FURNITURE_EXTRACT_JOBS
from street_furniture import install_furniture_layer_preset

# Preset name → list of (gdal_layer, SQL WHERE, default model tags).
# Installed into world ``LAYER_PRESETS`` by :func:`install_cell_extract_layer_presets`.
_LANDSCAPE_PRESETS: dict[str, list[tuple[str, str, list[str]]]] = {
    'water': [
        (
            'multipolygons',
            "natural = 'water'",
            ['landscape'],
        ),
    ],
    'waterway': [
        (
            'lines',
            "waterway IS NOT NULL AND waterway <> ''",
            ['landscape'],
        ),
    ],
    'barrier': [
        (
            'lines',
            (
                "barrier IN ("
                "'fence','hedge','wall','retaining_wall','railings','guard_rail'"
                ")"
            ),
            ['context'],
        ),
    ],
    'pedestrian': [
        (
            'multipolygons',
            "highway = 'pedestrian'",
            ['landscape'],
        ),
    ],
    'natural': [
        (
            'multipolygons',
            "natural IN ('wood','scrub')",
            ['landscape'],
        ),
    ],
    'leisure': [
        (
            'multipolygons',
            "leisure IS NOT NULL AND leisure <> ''",
            ['landscape'],
        ),
    ],
    # Surveyed trees for trees_layer (#157): point crowns + tree_row polylines.
    'trees': [
        (
            'points',
            "natural = 'tree'",
            ['landscape'],
        ),
        (
            'lines',
            "natural = 'tree_row'",
            ['landscape'],
        ),
    ],
}

# Ordered job list for one cell (or place) extract. Base map + landscape.
# Furniture jobs come from street_furniture; installed under the same name.
CELL_EXTRACT_LAYERS: tuple[str, ...] = (
    'buildings',
    'highways',
    'landuse',
    'water',
    'waterway',
    'barrier',
    'furniture',
    'pedestrian',
    'natural',
    'leisure',
    'trees',
)


def landscape_layer_presets() -> dict[str, list[tuple[str, str, list[str]]]]:
    """Copy of tool-owned landscape presets including furniture extract jobs.

    @returns: preset name → GDAL extract jobs
    """
    out: dict[str, list[tuple[str, str, list[str]]]] = {
        name: list(jobs) for name, jobs in _LANDSCAPE_PRESETS.items()
    }
    out['furniture'] = list(FURNITURE_EXTRACT_JOBS)
    return out


def cell_extract_layers_csv() -> str:
    """Comma-separated :data:`CELL_EXTRACT_LAYERS` for ``--layers``.

    @returns: CSV of preset names
    """
    return ','.join(CELL_EXTRACT_LAYERS)


def install_cell_extract_layer_presets(
    layer_presets: dict[str, list[tuple[str, str, list[str]]]],
) -> None:
    """Wire landscape + furniture presets into an osm_extract ``LAYER_PRESETS`` dict.

    Overwrites matching keys so the tool vocabulary is the single source of
    truth for fetching landscape clips into ``reference/vector_map``.

    @param layer_presets: mutable world (or test) ``LAYER_PRESETS`` mapping
    """
    for name, jobs in _LANDSCAPE_PRESETS.items():
        layer_presets[name] = list(jobs)
    install_furniture_layer_preset(layer_presets)


def with_default_cell_extract_layers(argv: list[str]) -> list[str]:
    """Insert ``--layers`` :func:`cell_extract_layers_csv` when the caller omitted it.

    Leaves argv alone when ``--layers`` or ``--gdal-layer`` is already present
    (including ``--layers=…`` forms). Used by ``shims/osm_extract.py`` so a new
    cell or place extract gets the full landscape set without a manual list.

    @param argv: CLI args without the program name
    @returns: possibly extended argv list (new list)
    """
    args = list(argv)
    for a in args:
        if a == '--layers' or a.startswith('--layers='):
            return args
        if a == '--gdal-layer' or a.startswith('--gdal-layer='):
            return args
    return ['--layers', cell_extract_layers_csv(), *args]

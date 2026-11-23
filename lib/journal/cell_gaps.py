"""Cell completion gaps from journal views, mapped features and canopy review notes.

This is the shared completion check used by status pages and cell orchestration.
The cell registry remains the source of truth (ADR 004).
"""
from __future__ import annotations

TREES_OVER_ROOF = 'trees-over-roof'  # review-note ref: trees-over-roof:x0,z0,x1,z1


def _view(cell):
    """The cell's journal view (``CellRegistry.view``)."""
    from cell_registry import view_registry
    return view_registry().cell(tuple(cell))


def residual_features(cell, idx=None):
    """Mapped features no feature build covers.

    Empty-world DoD (ADR 003): vector-map footprints still missing a house/feature build.

    @param cell: (tx, tz)
    @param idx: optional shared OSMIndex
    @returns: [{'kind': 'building'|'houses', 'label', 'street', 'refs', 'bbox'}]
    """
    from cell_features import missing_features
    from osm_footprints import OSMIndex
    tx, tz = cell
    try:
        return missing_features(tx, tz, idx or OSMIndex())
    except (OSError, ValueError, KeyError, SystemExit):
        return []


def lidar_lumps(cell):
    """Lidar lumps of a cell (lidar_heightmap: national composite where cached), [] when unavailable.

    @param cell: (tx, tz)
    @returns: lidar_heightmap.lumps() items
    """
    try:
        import lidar_heightmap as lh
        return lh.lumps(lh.reference_world().heightmap(cell))
    except (OSError, ValueError, KeyError, SystemExit, ImportError, LookupError):
        return []


def _inside(inner, outer):
    return inner[0] >= outer[0] and inner[1] >= outer[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def resolved_trees_over_roof(cell):
    """Trees-over-roof decisions journalled as review notes on the cell: [{'bbox': [...], 'reason': str}].

    Recorded with ``cell_registry.py note tx,tz --kind review --ref trees-over-roof:x0,z0,x1,z1 --body <reason>``.

    @param cell: (tx, tz)
    @returns: list ([] if none)
    """
    out = []
    for n in _view(cell).notes:
        if n.kind == 'review' and n.ref.startswith(TREES_OVER_ROOF + ':'):
            try:
                bbox = [int(v) for v in n.ref.split(':', 1)[1].split(',')]
            except ValueError:
                continue
            if len(bbox) == 4:
                out.append({'bbox': bbox, 'reason': n.body})
    return out


def trees_over_roof_open(cell, lumps=None):
    """Lidar lumps with trees over the roof that no journal review note resolves (I-071).

    A lump is resolved when its bbox lies inside a recorded ``trees-over-roof:<bbox>`` review note (a tree-pass
    decision: crown kept over the roof, or the roof rebuilt).

    @param cell: (tx, tz)
    @param lumps: optional lidar_lumps(cell) result
    @returns: [lump] still open
    """
    lumps = lidar_lumps(cell) if lumps is None else lumps
    done = [r['bbox'] for r in resolved_trees_over_roof(cell) if r.get('bbox')]
    return [lp for lp in lumps if lp.get('trees_over') and not any(_inside(lp['bbox'], d) for d in done)]


def cell_gaps(cell, features=True):
    """What keeps a cell from being done (the cell journal is the source of truth — ADR 004).

    Layers, features and in-flight entries come from the cell's journal view. Optional slow
    checks still report residual vector-map buildings and lidar trees-over-roof. In-flight
    entries (``begun``, an aborted apply) are listed so site_recover can finish them.
    When ``features`` is true, also records built vs unbuilt footprint area (#142); a
    non-empty ``unbuilt_footprint`` means the cell must not show as Completed.

    @param cell: (tx, tz)
    @param features: also compute residual_features and footprint progress (slow)
    @returns: gaps dict
    """
    v = _view(cell)
    layers = []
    if v.ground != 'generated':
        layers.append('ground')
    if v.ground == 'generated' and not v.trees:
        layers.append('trees')
    if v.ground == 'generated' and not v.decor:
        layers.append('decor')
    feature_gaps = [f'{f.id}={f.status}' for f in v.features if f.status != 'built']
    in_flight = [f'in-flight: {f.plan}' for f in v.in_flight]
    stale = False
    if 'trees' not in layers and v.trees:
        from pass_mask import trees_rule_current
        stale = not trees_rule_current(cell)
    footprint = {'built_area': 0, 'unbuilt_area': 0, 'unbuilt': [], 'unbuilt_names': []}
    residual = []
    if features:
        try:
            from cell_features import footprint_progress
            footprint = footprint_progress(int(cell[0]), int(cell[1]))
            residual = list(footprint.get('unbuilt') or [])
        except (OSError, ValueError, KeyError, RuntimeError, SystemExit):
            residual = residual_features(cell)
    return {
        'in_flight': in_flight,
        'layers': layers,
        'trees_stale': stale,
        'features': residual + feature_gaps,
        'trees_over_roof': trees_over_roof_open(cell) if features else [],
        'hold': v.hold,
        'done': v.done,
        'footprint': footprint,
        'unbuilt_footprint': list(footprint.get('unbuilt_names') or []),
    }

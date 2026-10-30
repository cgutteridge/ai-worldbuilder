#!/usr/bin/env python3
"""Find major features (buildings and water bodies) a cell still lacks, packaged for queueing.

Each building feature also lists the university entrances ("portals", scripts/portals.py)
on its footprint: designer inputs for ``facade_plan`` entrances (positions, names, photos).

A feature is any build recorded in the cell journal that is not a layer pass
(ground, trees, decor, foliage, canopy, undo, markers). A vector-map building is "in a
feature" when at least COVERED_FRAC of its footprint columns are touched by one.
Mapped water bodies (#148) use the same coverage rule on their outline columns.

Packaging: each non-home building (campus block, shop, church...) is its own feature.
Homes that overlap the cell and are not yet in a feature are grouped by street, then
split where the gap between neighbouring footprints exceeds JOIN_GAP, so one terrace
or run of houses becomes one feature. Outbuildings (sheds, garages) are left to their
house plot. Connected polygon water columns become ``kind: water_body`` candidates
(streams stay off the pond list; #163).

  python3 scripts/cell_features.py --cell 48,-43
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

from script_env import ROOT  # noqa: E402

from coverage import (  # noqa: E402
    HOMES,
    OUTBUILDINGS,
    cell_bbox,
    osm_ref,
    property_label,
    scan_polygon,
    touched_by_build,
)
from osm_footprints import OSMIndex  # noqa: E402
from portals import describe, for_footprint  # noqa: E402
from buildings import describe as describe_building, for_footprint as pictures_for  # noqa: E402
from water_body import MIN_WATER_COLUMNS, water_body_components  # noqa: E402

COVERED_FRAC = 0.5
JOIN_GAP = 12         # blocks between footprints that still count as one run of houses
STREET_REACH = 40     # nearest named street for homes with no addr:street
MIN_COLUMNS = 12      # ignore slivers (kiosks, bin stores)

LAYER_BUILD = re.compile(
    r'(_ground|_trees|_decor|_foliage|_canopy|_undo|_grounds_\w+)$|^cell_marker_|^clear_floating|'
    r'^highfield_grounds_|_grounds$|^ghost_')


def clip_coverage(cell: tuple[int, int]) -> float:
    """Fraction of this cell queried by vector-map extracts, independent of build coverage."""
    from osm_footprints import clip_coverage as query_coverage
    try:
        return query_coverage(cell)
    except FileNotFoundError:
        return 0.0


def is_feature_build(name: str) -> bool:
    """Whether a build name is a feature rather than a layer pass.

    @param name: build (plan) name
    @returns: True for features
    """
    return not LAYER_BUILD.search(name)


def feature_columns() -> set:
    """Columns touched by any feature build.

    @returns: set of (x, z)
    """
    cols = set()
    for name, c in touched_by_build().items():
        if is_feature_build(name):
            cols |= c
    return cols


def _street_of(p: dict, bbox, streets) -> str | None:
    from osm_footprints import _addr
    _num, street = _addr(p)
    if street:
        return street
    cx, cz = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    best, best_d = None, STREET_REACH
    for name, _hw, pts in streets:
        if not name:
            continue
        for (ax, az), (bx, bz) in zip(pts, pts[1:]):
            dx, dz = bx - ax, bz - az
            t = 0 if dx == dz == 0 else max(0, min(1, ((cx - ax) * dx + (cz - az) * dz) / (dx * dx + dz * dz)))
            d = math.hypot(cx - (ax + t * dx), cz - (az + t * dz))
            if d < best_d:
                best, best_d = name, d
    return best


def _gap(a, b) -> float:
    dx = max(a[0] - b[2], b[0] - a[2], 0)
    dz = max(a[1] - b[3], b[1] - a[3], 0)
    return math.hypot(dx, dz)


def _split_runs(items: list) -> list:
    """Single-link cluster footprints whose bbox gap <= JOIN_GAP.

    @param items: [{'bbox': (x0, z0, x1, z1), ...}]
    @returns: list of lists
    """
    groups: list[list] = []
    for it in items:
        hit = [g for g in groups if any(_gap(it['bbox'], o['bbox']) <= JOIN_GAP for o in g)]
        merged = [it] + [o for g in hit for o in g]
        groups = [g for g in groups if g not in hit] + [merged]
    return groups


def _package_unbuilt(singles: list, homes: list, streets) -> list:
    """Package uncovered building items into missing-feature / footprint rows.

    @param singles: non-home items with ref/label/bbox/area/portals/pictures
    @param homes: home items (same shape) still needing a street
    @param streets: OSMIndex.streets for nearest-street lookup
    @returns: buildings first, then house runs (each with ``area`` column count)
    """
    out = [{'kind': 'building', 'label': s['label'], 'street': None, 'refs': [s['ref']],
            'bbox': s['bbox'], 'area': s['area'], 'portals': s['portals'], 'pictures': s['pictures']}
           for s in sorted(singles, key=lambda s: s['bbox'][:2])]
    by_street: dict = {}
    for h in homes:
        h['street'] = _street_of(h['p'], h['bbox'], streets)
        by_street.setdefault(h['street'], []).append(h)
    for street in sorted(by_street, key=lambda s: s or ''):
        for run in sorted(_split_runs(by_street[street]), key=lambda g: min(o['bbox'][:2] for o in g)):
            out.append({
                'kind': 'houses', 'street': street,
                'label': f'{street or "unnamed street"} ({len(run)} houses)',
                'refs': sorted(o['ref'] for o in run),
                'bbox': (min(o['bbox'][0] for o in run), min(o['bbox'][1] for o in run),
                         max(o['bbox'][2] for o in run), max(o['bbox'][3] for o in run)),
                'area': sum(o['area'] for o in run),
                'portals': [q for o in run for q in o['portals']],
                'pictures': [q for o in run for q in o['pictures']],
            })
    return out


def _portal_column(portal: dict) -> tuple[int, int]:
    """Integer XZ column of a portal entrance.

    @param portal: portal with ``x`` / ``z`` (Minecraft blocks)
    @returns: ``(x, z)`` rounded to the nearest block
    """
    return (int(round(portal['x'])), int(round(portal['z'])))


def _uncovered_portals_in_cell(rings, covered: set, cb: tuple[int, int, int, int]) -> list:
    """Portals on a footprint whose entrance column is in the cell and not built (#154).

    Used when the footprint itself is already judged built (e.g. one large outline
    that swallowed neighbouring houses the feature build excluded on purpose).

    @param rings: OSM rings for the footprint
    @param covered: columns touched by any feature build
    @param cb: cell bbox ``(x0, z0, x1, z1)``
    @returns: portals in cell whose column is not in ``covered``
    """
    x0, z0, x1, z1 = cb
    out = []
    for portal in for_footprint(rings):
        col = _portal_column(portal)
        if col in covered:
            continue
        if not (x0 <= col[0] <= x1 and z0 <= col[1] <= z1):
            continue
        out.append(portal)
    return out


def _houses_from_uncovered_portals(portals: list, streets) -> dict:
    """One unbuilt houses row for portals left inside an otherwise-built footprint (#154).

    @param portals: uncovered portals (same building outline, this cell)
    @param streets: OSMIndex.streets for nearest-street lookup
    @returns: a ``kind: houses`` row shaped like :func:`_package_unbuilt` output
    """
    buildings = sorted({str(p.get('building') or p['id']) for p in portals})
    xs = [p['x'] for p in portals]
    zs = [p['z'] for p in portals]
    pad = 2
    bbox = (min(xs) - pad, min(zs) - pad, max(xs) + pad, max(zs) + pad)
    street = _street_of({'building': 'house'}, bbox, streets)
    n = len(buildings)
    return {
        'kind': 'houses',
        'street': street,
        'label': f'{street or "unnamed street"} ({n} houses)',
        'refs': [f'uos:{b}' for b in buildings],
        'bbox': bbox,
        'area': max(1, n),
        'portals': list(portals),
        'pictures': [],
    }


def footprint_progress(tx: int, tz: int, idx: OSMIndex | None = None, covered: set | None = None) -> dict:
    """Built vs unbuilt vector-map building footprint area overlapping a cell (#142).

    Area is the count of building columns that fall inside the cell. A building is
    unbuilt when fewer than COVERED_FRAC of its full footprint columns are touched
    by a feature build — the same rule as :func:`missing_features`.

    When a footprint is judged built, portals on it whose entrance columns are still
    untouched (and lie in this cell) are still emitted as unbuilt homes (#154): a
    large outline can swallow houses that the feature build excluded on purpose.

    @param tx: cell X
    @param tz: cell Z
    @param idx: optional shared OSMIndex
    @param covered: optional precomputed feature_columns()
    @returns: ``{built_area, unbuilt_area, unbuilt, unbuilt_names}`` where ``unbuilt``
        mirrors :func:`missing_features` rows plus an ``area`` column count
    """
    idx = idx or OSMIndex()
    covered = feature_columns() if covered is None else covered
    cb = cell_bbox(tx, tz, tx, tz)
    built_area = 0
    singles, homes = [], []
    orphan_houses: list = []
    for rings, p in idx.buildings:
        if p.get('building') in OUTBUILDINGS:
            continue
        cols = list(scan_polygon(rings, cb))
        if len(cols) < MIN_COLUMNS:
            continue
        # whole footprint, so a building straddling the cell edge is judged as one
        full = list(scan_polygon(rings, (-10**9, -10**9, 10**9, 10**9))) if len(cols) < 4000 else cols
        area = len(cols)
        if sum(c in covered for c in full) >= COVERED_FRAC * len(full):
            built_area += area
            orphans = _uncovered_portals_in_cell(rings, covered, cb)
            if orphans:
                orphan_houses.append(_houses_from_uncovered_portals(orphans, idx.streets))
            continue
        xs = [x for x, _ in rings[0]]
        zs = [z for _, z in rings[0]]
        item = {
            'ref': osm_ref(p), 'label': property_label(p), 'p': p,
            'portals': for_footprint(rings), 'pictures': pictures_for(rings),
            'bbox': (min(xs), min(zs), max(xs), max(zs)), 'area': area,
        }
        (homes if p.get('building') in HOMES else singles).append(item)
    unbuilt = _package_unbuilt(singles, homes, idx.streets) + orphan_houses
    return {
        'built_area': built_area,
        'unbuilt_area': sum(u['area'] for u in unbuilt),
        'unbuilt': unbuilt,
        'unbuilt_names': [u['label'] for u in unbuilt],
    }


def missing_water_bodies(
    tx: int,
    tz: int,
    *,
    water_columns: set | None = None,
    water_refs: dict | None = None,
    covered: set | None = None,
) -> list:
    """Mapped water bodies overlapping the cell that no feature build covers (#148, #163).

    Uses polygon ``natural=water`` only (not stream/drain line columns). Candidates
    carry OSM way refs and provenance ``source: vector_map``.

    @param tx: cell X
    @param tz: cell Z
    @param water_columns: optional precomputed polygon water columns; default loads ``OSMGround.water``
    @param water_refs: optional ``(x, z) -> 'w<id>'`` map; default loads ``OSMGround.water_refs``
    @param covered: optional precomputed feature_columns()
    @returns: list of ``kind: water_body`` candidate dicts (outline, bbox, refs, provenance, …)
    """
    covered = feature_columns() if covered is None else covered
    if water_columns is None:
        try:
            from ground_sources import OSMGround  # noqa: WPS433
            ground = OSMGround((tx, tz))
            water_columns = set(ground.water)
            if water_refs is None:
                water_refs = dict(ground.water_refs)
        except (OSError, RuntimeError, ValueError, FileNotFoundError):
            # Unit fixtures / no world: no vector-map water to propose.
            return []
    x0, z0, x1, z1 = cell_bbox(tx, tz, tx, tz)
    in_cell = {(x, z) for x, z in water_columns if x0 <= x <= x1 and z0 <= z <= z1}
    refs_in_cell = None
    if water_refs is not None:
        refs_in_cell = {q: water_refs[q] for q in in_cell if q in water_refs}
    out = []
    for body in water_body_components(
        in_cell,
        cell=(tx, tz),
        min_columns=MIN_WATER_COLUMNS,
        column_refs=refs_in_cell,
        provenance_source='vector_map',
    ):
        outline = [(int(c[0]), int(c[1])) for c in body['outline']]
        if outline and sum(c in covered for c in outline) >= COVERED_FRAC * len(outline):
            continue
        out.append(body)
    return out


def missing_features(
    tx: int,
    tz: int,
    idx: OSMIndex | None = None,
    covered: set | None = None,
    *,
    water_columns: set | None = None,
) -> list:
    """Buildings and water bodies overlapping the cell that no feature build covers.

    @param tx: cell X
    @param tz: cell Z
    @param idx: optional shared OSMIndex
    @param covered: optional precomputed feature_columns()
    @param water_columns: optional water columns for tests; default loads ``OSMGround``
    @returns: [{'kind': 'building'|'houses'|'water_body', 'label': str, 'street': str|None,
                'refs': [map ref], 'bbox': (x0, z0, x1, z1), 'area': int,
                'portals': [portal],
                'pictures': [university building (scripts/buildings.py: photo + outline)]}],
        buildings first, then streets, then water bodies
    """
    covered = feature_columns() if covered is None else covered
    buildings = footprint_progress(tx, tz, idx=idx, covered=covered)['unbuilt']
    return buildings + missing_water_bodies(tx, tz, water_columns=water_columns, covered=covered)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cell', required=True, help='tx,tz')
    a = ap.parse_args(argv)
    tx, tz = (int(v) for v in a.cell.split(','))
    for f in missing_features(tx, tz):
        print(f"{f['kind']:9} {f['label']}  bbox={f['bbox']}  {','.join(f['refs'][:6])}")
        if f['kind'] == 'water_body':
            print(f"          outline {f.get('area')} columns  water_level={f.get('water_level')}")
            continue
        for q in f['portals']:
            print(f'          entrance {describe(q)}')
        for q in f['pictures']:
            print(f'          picture  {describe_building(q)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

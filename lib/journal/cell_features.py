#!/usr/bin/env python3
"""Find major features (buildings and water bodies) a cell still lacks, packaged for queueing.

Each building feature also lists registered entrances (catalogue role ``entrance_register``)
on its footprint: designer inputs for ``facade_plan`` entrances (positions, names, photos).

A feature is any build recorded in the cell journal that is not a layer pass
(ground, trees, decor, foliage, canopy, undo, markers). A vector-map building is "in a
feature" when at least COVERED_FRAC of its footprint columns are touched by one.
Mapped water bodies (#148) use the same coverage rule on their outline columns.

Packaging: non-home buildings of the same ``building=`` tag whose footprints touch
(gap of TOUCH_GAP blocks or less) become one ``kind: group`` candidate with all their
refs; an ungrouped single stays ``kind: building``. Homes that overlap the cell and
are not yet in a feature are grouped by street, then split where the gap between
neighbouring footprints exceeds JOIN_GAP, so one terrace or run of houses becomes one
feature. Outbuildings (sheds, garages) are left to their house plot. Connected
polygon water columns become ``kind: water_body`` candidates (streams stay off the
pond list; #163).

A building is one build whatever cell lines it crosses (task 288): a footprint is proposed
only in its home cell (:func:`home_cell`), whole, and never in the other cells it touches.
A water body over cell lines is the exception (#321): each cell it touches proposes and
builds its own clip (``water_body.clip_candidate``), so a river is planned cell by cell.
Touching same-tag non-house footprints group only when they share that home cell (#328).
A neighbour whose own majority cell is different stays its own feature there (task 433).

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
    osm_ref,
    property_label,
    scan_polygon,
    touched_by_build,
)
from osm_footprints import OSMIndex  # noqa: E402
from cellmath import cell_bbox  # noqa: E402
from world_provider import load_world_provider  # noqa: E402
from water_body import (  # noqa: E402
    MIN_WATER_COLUMNS,
    bank_columns_in_cell,
    clip_candidate,
    water_body_components,
)
from landmark_gate import landmark_fields  # noqa: E402
from osm_footprints import osm_tags  # noqa: E402

COVERED_FRAC = 0.5
JOIN_GAP = 12         # blocks between footprints that still count as one run of houses
TOUCH_GAP = 1         # blocks between non-house footprints that still count as one group
STREET_REACH = 40     # nearest named street for homes with no addr:street
MIN_COLUMNS = 12      # ignore slivers (kiosks, bin stores)

LAYER_BUILD = re.compile(
    r'(_ground|_trees|_decor|_foliage|_canopy|_undo|_grounds_\w+)$|^cell_marker_|^clear_floating|'
    r'_grounds$|^ghost_')



def for_footprint(rings):
    """Registered entrances on a footprint, or [] without an entrance provider."""
    provider = load_world_provider('entrance_register', 'portals.py', 'portals', optional=True)
    return provider.for_footprint(rings) if provider is not None else []


def pictures_for(rings):
    """Registered building pictures on a footprint, or [] without a provider."""
    provider = load_world_provider('building_register', 'buildings.py', 'buildings', optional=True)
    return provider.for_footprint(rings) if provider is not None else []


def describe(portal):
    """Describe a registered entrance using its configured provider."""
    provider = load_world_provider('entrance_register', 'portals.py', 'portals', optional=True)
    return provider.describe(portal) if provider is not None else str(portal.get('id', 'entrance'))


def describe_building(building):
    """Describe a registered building using its configured provider."""
    provider = load_world_provider('building_register', 'buildings.py', 'buildings', optional=True)
    return provider.describe(building) if provider is not None else str(building.get('id', 'building'))


def footprint_cells(columns) -> list[tuple[int, int]]:
    """Cells a footprint touches, its home cell first (task 288).

    Order: most columns first; a tie goes to the lowest ``(tx, tz)`` so the choice is stable.
    Each column counts once (:func:`cellmath.cells_by_column_count`).

    @param columns: ``(x, z)`` block columns of one whole footprint
    @returns: ``[(tx, tz), ...]``; empty for an empty footprint
    """
    import cellmath
    return cellmath.cells_by_column_count(columns)


def home_cell(columns) -> tuple[int, int] | None:
    """The one cell that plans a footprint crossing cell lines: most columns, ties lowest ``(tx, tz)``.

    @param columns: ``(x, z)`` block columns of one whole footprint
    @returns: ``(tx, tz)`` or None for an empty footprint
    """
    cells = footprint_cells(columns)
    return cells[0] if cells else None


def clip_coverage(cell: tuple[int, int]) -> float:
    """Fraction of this cell queried by vector-map extracts, independent of build coverage.

    @param cell: (tx, tz)
    @returns: queried fraction in [0, 1], zero when no clips exist
    """
    from osm_footprints import clip_coverage as query_coverage
    try:
        return query_coverage(cell)
    except FileNotFoundError:
        return 0.0


def is_feature_build(name: str) -> bool:
    """Whether a build name is a feature rather than a layer pass.

    Grounds-marker plans use the world's plan prefix (``<prefix>_grounds_``).

    @param name: build (plan) name
    @returns: True for features
    """
    from place_config import grounds_prefix
    if name.startswith(grounds_prefix()):
        return False
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


def _split_runs(items: list, gap: float = JOIN_GAP) -> list:
    """Single-link cluster footprints whose bbox gap is at most ``gap``.

    @param items: [{'bbox': (x0, z0, x1, z1), ...}]
    @param gap: maximum bbox gap (blocks) that still joins two footprints
    @returns: list of lists
    """
    groups: list[list] = []
    for it in items:
        hit = [g for g in groups if any(_gap(it['bbox'], o['bbox']) <= gap for o in g)]
        merged = [it] + [o for g in hit for o in g]
        groups = [g for g in groups if g not in hit] + [merged]
    return groups


_SINGLE_KEYS = frozenset({'label', 'bbox', 'area', 'portals', 'pictures'})
_INTERNAL_KEYS = frozenset({'p', 'ref', '_home'})
_WHOLE = (-10**9, -10**9, 10**9, 10**9)


def _single_building_row(s: dict) -> dict:
    """One ``kind: building`` candidate from an ungrouped non-house item.

    Keeps any extra keys on ``s`` (except internal ``p`` / ``ref`` / ``_home``) so a
    parallel change can add fields without this packaging dropping them.

    @param s: single item with ref/label/bbox/area/portals/pictures (and optional extras)
    @returns: candidate dict
    """
    row = {k: v for k, v in s.items() if k not in _INTERNAL_KEYS}
    row.update({
        'kind': 'building', 'label': s['label'], 'street': None, 'refs': [s['ref']],
        'bbox': s['bbox'], 'area': s['area'], 'portals': s['portals'], 'pictures': s['pictures'],
    })
    home = _public_home(s)
    if home is not None:
        row['home'] = home
    return row


def _group_building_row(run: list) -> dict:
    """One ``kind: group`` candidate from touching non-house items of the same tag.

    Shared extra keys (same value on every member, not internal / packed fields) are
    kept so unknown fields from a parallel change are not dropped.

    @param run: two or more single items already clustered as touching
    @returns: candidate dict with sorted ``refs`` and a union bbox
    """
    refs = sorted(o['ref'] for o in run)
    tag = run[0]['p'].get('building') or 'yes'
    shared = None
    for o in run:
        extra = {k: v for k, v in o.items()
                 if k not in _INTERNAL_KEYS and k not in _SINGLE_KEYS and k != 'street'}
        shared = extra if shared is None else {k: v for k, v in shared.items()
                                              if k in extra and extra[k] == v}
    row = dict(shared or {})
    row.update({
        'kind': 'group', 'street': None,
        'label': f'{tag} ({len(run)} buildings)',
        'refs': refs,
        'bbox': (min(o['bbox'][0] for o in run), min(o['bbox'][1] for o in run),
                 max(o['bbox'][2] for o in run), max(o['bbox'][3] for o in run)),
        'area': sum(o['area'] for o in run),
        'portals': [q for o in run for q in o['portals']],
        'pictures': [q for o in run for q in o['pictures']],
    })
    home = _public_home(run[0])
    if home is not None:
        row['home'] = home
    return row


def _public_home(item: dict) -> list[int] | None:
    """Home cell for a propose row, JSON-friendly.

    @param item: building item that may carry ``_home``
    @returns: ``[tx, tz]`` or None
    """
    home = item.get('_home')
    if not home:
        return None
    return [int(home[0]), int(home[1])]


def _run_home(run: list) -> tuple[int, int] | None:
    """Home cell of a touching non-house run.

    Members are grouped only when they already share a home, so this is that
    cell (the lowest-ref member's home when a run has one). A footprint whose
    majority cell differs is not in the run (task 433).

    @param run: one or more single items with ``ref`` and ``_home``
    @returns: ``(tx, tz)`` or None when the lead has no home
    """
    lead = min(run, key=lambda o: o['ref'])
    return lead.get('_home')


def _building_item(rings, p: dict, *, area: int, home: tuple[int, int] | None) -> dict:
    """One unbuilt non-house (or home) item for packaging.

    @param rings: OSM rings
    @param p: OSM tags (must include id for ``osm_ref``)
    @param area: column count reported on the candidate
    @param home: planning cell for this footprint (``_home``); None for house items
    @returns: item dict
    """
    xs = [x for x, _ in rings[0]]
    zs = [z for _, z in rings[0]]
    item = {
        'ref': osm_ref(p), 'label': property_label(p), 'p': p,
        'portals': for_footprint(rings), 'pictures': pictures_for(rings),
        'bbox': (min(xs), min(zs), max(xs), max(zs)), 'area': area,
    }
    item.update(landmark_fields(osm_tags(p)))
    if home is not None:
        item['_home'] = home
    return item


def _expand_touching_nonhouses(seeds: list, idx: OSMIndex, covered: set) -> list:
    """Grow ``seeds`` with unbuilt same-tag non-house buildings that touch the set (any cell).

    Each member keeps its own home cell. :func:`_package_unbuilt` groups only
    members that share a home, so a shop whose majority cell is next door is
    not pulled into this cell (#288 / #433).

    @param seeds: unbuilt non-house items overlapping the cell being planned
    @param idx: OSM index
    @param covered: feature-build columns
    @returns: seeds plus touching same-tag members from other cells
    """
    if not seeds:
        return seeds
    by_ref = {s['ref']: s for s in seeds}
    pool: list = []
    for rings, p in idx.buildings:
        if p.get('building') in OUTBUILDINGS or p.get('building') in HOMES:
            continue
        ref = osm_ref(p)
        if ref in by_ref:
            continue
        full = list(scan_polygon(rings, _WHOLE))
        if len(full) < MIN_COLUMNS:
            continue
        if sum(c in covered for c in full) >= COVERED_FRAC * len(full):
            continue
        pool.append(_building_item(rings, p, area=len(full), home=home_cell(full)))
    active = list(seeds)
    changed = True
    while changed:
        changed = False
        for cand in pool:
            if cand['ref'] in by_ref:
                continue
            tag = cand['p'].get('building')
            if any(o['p'].get('building') == tag and _gap(cand['bbox'], o['bbox']) <= TOUCH_GAP
                   for o in active):
                by_ref[cand['ref']] = cand
                active.append(cand)
                changed = True
    return list(by_ref.values())


def _package_unbuilt(singles: list, homes: list, streets,
                     cell: tuple[int, int] | None = None) -> list:
    """Package uncovered building items into missing-feature / footprint rows.

    Non-house items of the same ``building=`` tag whose footprints touch (gap at most
    TOUCH_GAP) and share a home cell become one ``kind: group``; an ungrouped single
    stays ``kind: building``. A footprint that crosses a cell line keeps the home
    with most of its columns, even when it touches a same-tag neighbour homed next
    door (tasks 288, 433). When ``cell`` is set, a run is kept only for that home.

    @param singles: non-home items with ref/label/bbox/area/portals/pictures
    @param homes: home items (same shape) still needing a street
    @param streets: OSMIndex.streets for nearest-street lookup
    @param cell: optional ``(tx, tz)`` home filter for non-house runs
    @returns: buildings and groups first, then house runs (each with ``area`` column count)
    """
    out: list = []
    by_tag: dict = {}
    for s in singles:
        by_tag.setdefault(s['p'].get('building'), []).append(s)
    for tag in sorted(by_tag, key=lambda t: t or ''):
        members = by_tag[tag]
        home_keys = sorted({m.get('_home') for m in members},
                           key=lambda h: (h is None, h or ()))
        for home in home_keys:
            same = [m for m in members if m.get('_home') == home]
            for run in sorted(_split_runs(same, gap=TOUCH_GAP),
                              key=lambda g: min(o['bbox'][:2] for o in g)):
                if cell is not None and _run_home(run) != cell:
                    continue
                out.append(_single_building_row(run[0]) if len(run) == 1 else _group_building_row(run))
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


def _big_home(rings, cell: tuple[int, int], in_cell: int) -> tuple[int, int]:
    """Home cell of a huge footprint without listing its whole column set.

    Counts columns per cell over the cells the outer ring's bbox touches; a footprint inside
    one cell (the common case) costs nothing extra. Same rule as :func:`home_cell`.

    @param rings: OSM rings of the footprint
    @param cell: the cell being planned, whose count is ``in_cell``
    @param in_cell: the footprint's columns in ``cell``
    @returns: ``(tx, tz)`` home cell
    """
    import cellmath
    xs = [x for x, _ in rings[0]]
    zs = [z for _, z in rings[0]]
    lo = cellmath.cell_of(int(math.floor(min(xs))), int(math.floor(min(zs))))
    hi = cellmath.cell_of(int(math.ceil(max(xs))), int(math.ceil(max(zs))))
    counts = {cell: in_cell}
    for qx in range(lo[0], hi[0] + 1):
        for qz in range(lo[1], hi[1] + 1):
            if (qx, qz) != cell:
                n = sum(1 for _ in scan_polygon(rings, cell_bbox(qx, qz, qx, qz)))
                if n:
                    counts[(qx, qz)] = n
    return sorted(counts, key=lambda c: (-counts[c], c))[0]


ORPHAN_PORTAL_PAD = 2  # columns held around an uncovered portal in a built outline (#154 house box)


def settled_footprint_columns(bbox, idx: OSMIndex | None = None, covered: set | None = None,
                              dismissed=None) -> set:
    """Building outline columns in ``bbox`` whose feature is settled, so the ground may have them (task 295).

    An outline column is held for its building's feature while that feature is unsettled. A building
    is settled once it is built (at least COVERED_FRAC of its whole footprint touched by feature
    builds, the :func:`footprint_progress` rule) or its map ref was dismissed from a cell plan. Its
    columns are then the ground's wherever no build owns them (the caller checks ownership).
    Still held: columns of unbuilt, undismissed buildings (also where outlines overlap), outbuildings
    and slivers (never proposed as features), and a box round each portal left uncovered inside a
    built outline (#154: those houses are still to be built).

    @param bbox: ``(x0, z0, x1, z1)`` inclusive
    @param idx: optional shared OSMIndex
    @param covered: optional precomputed :func:`feature_columns`
    @param dismissed: feature ids dismissed from plans (map refs such as ``w123``); default reads
        every cell in the registry
    @returns: set of ``(x, z)``
    """
    idx = idx or OSMIndex()
    covered = feature_columns() if covered is None else covered
    dismissed = dismissed_feature_ids() if dismissed is None else set(dismissed)
    settled, held = set(), set()
    for rings, p in idx.buildings:
        cols = set(scan_polygon(rings, bbox))
        if not cols:
            continue
        ref = osm_ref(p)
        is_dismissed = any(f == ref or f.endswith('_' + ref) for f in dismissed)
        if p.get('building') in OUTBUILDINGS or (len(cols) < MIN_COLUMNS and not is_dismissed):
            held |= cols
            continue
        big = len(cols) >= 4000                       # umbrella polygons: clipped judgement, as footprint_progress
        full = cols if big else set(scan_polygon(rings, (-10**9, -10**9, 10**9, 10**9)))
        built = sum(c in covered for c in full) >= COVERED_FRAC * len(full)
        if not (built or is_dismissed):
            held |= cols
            continue
        settled |= cols
        if built:
            for portal in _uncovered_portals_in_cell(rings, covered, bbox):
                px, pz = _portal_column(portal)
                held |= {(px + a, pz + b) for a in range(-ORPHAN_PORTAL_PAD, ORPHAN_PORTAL_PAD + 1)
                         for b in range(-ORPHAN_PORTAL_PAD, ORPHAN_PORTAL_PAD + 1)}
    return settled - held


def dismissed_feature_ids() -> set:
    """Feature ids dismissed from the plan of any cell in the registry (#128).

    @returns: set of plan ids (map refs, or ``houses_...`` group ids)
    """
    from cell_registry import view_registry
    reg = view_registry()
    return {fid for c in reg.view() for fid in reg.plan_dismissed_ids(c)}


def footprint_progress(tx: int, tz: int, idx: OSMIndex | None = None, covered: set | None = None) -> dict:
    """Built vs unbuilt vector-map building footprint area overlapping a cell (#142).

    Area is the count of building columns that fall inside the cell. An unbuilt building
    whose whole footprint has its home in another cell (:func:`home_cell`) is not listed or
    counted here. Touching same-tag neighbours group only when they share that home
    (task 288 / #328 / #433). A building is unbuilt when fewer than
    COVERED_FRAC of its full footprint columns are touched by a feature build — the
    same rule as :func:`missing_features`.

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
    cell = (tx, tz)
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
        big = len(cols) >= 4000                       # umbrella polygons (campus): keep the clipped judgement
        full = cols if big else list(scan_polygon(rings, _WHOLE))
        area = len(cols)
        built = sum(c in covered for c in full) >= COVERED_FRAC * len(full)
        hc = _big_home(rings, cell, area) if big else home_cell(full)
        if built:
            built_area += area
            orphans = _uncovered_portals_in_cell(rings, covered, cb)
            if orphans:
                orphan_houses.append(_houses_from_uncovered_portals(orphans, idx.streets))
            continue
        if p.get('building') in HOMES:
            if hc != cell:
                continue                              # houses: proposed whole in home cell (task 288)
            homes.append(_building_item(rings, p, area=area, home=None))
            continue
        # Non-houses: keep every overlap for grouping; home filter runs after the cluster.
        singles.append(_building_item(rings, p, area=area, home=hc))
    singles = _expand_touching_nonhouses(singles, idx, covered)
    unbuilt = _package_unbuilt(singles, homes, idx.streets, cell=cell) + orphan_houses
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
    carry OSM way refs and provenance ``source: vector_map``. A body inside the cell is
    proposed whole. A body over cell lines (as far as ``water_columns`` reaches) is proposed
    as this cell's clip (#321, ``water_body.clip_candidate``): a per-cell ``id`` and ``clip``,
    the in-cell ``area``, and coverage judged on the in-cell columns only. Every cell it
    touches proposes its own clip, so each cell records and builds its own plan. A cell with
    none of the body's water but some within the default bank width gets a bank-only clip
    (``area`` 0), so a shore that spills over a cell line is never left unbuilt. Built
    bank-only clips drop out once their in-cell bank columns are covered (#353).

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
    out = []
    for body in water_body_components(
        water_columns,
        cell=(tx, tz),
        min_columns=MIN_WATER_COLUMNS,
        column_refs=water_refs,
        provenance_source='vector_map',
    ):
        outline = [(int(c[0]), int(c[1])) for c in body['outline']]
        if len(footprint_cells(outline)) > 1:
            # per-cell clip of a body over cell lines, bank-only where only its shore reaches (#321)
            body = clip_candidate(body, (tx, tz))
            if body is None:
                continue
            outline = [q for q in outline if x0 <= q[0] <= x1 and z0 <= q[1] <= z1]
            if not outline:
                # bank only: no in-cell water; coverage is the bank columns this clip would write
                bank_cols = bank_columns_in_cell(body['outline'], (tx, tz))
                if bank_cols and sum(c in covered for c in bank_cols) >= COVERED_FRAC * len(bank_cols):
                    continue
                out.append(body)
                continue
        elif not any(x0 <= x <= x1 and z0 <= z <= z1 for x, z in outline):
            continue
        if sum(c in covered for c in outline) >= COVERED_FRAC * len(outline):
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
    @returns: [{'kind': 'building'|'group'|'houses'|'water_body', 'label': str,
                'street': str|None, 'refs': [map ref], 'bbox': (x0, z0, x1, z1), 'area': int,
                'portals': [portal],
                'pictures': [registered building (building_register provider: photo + outline)]}],
        buildings and groups first, then streets, then water bodies
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

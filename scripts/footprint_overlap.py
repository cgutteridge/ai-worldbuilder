#!/usr/bin/env python3
"""Footprint overlap check: paths / open lidar / aerial ground inside a building mass.

Coverage highway parcels stop at vector-map footprints, so a footway that the map draws
*through* a building polygon never becomes a path parcel. The outside-footprint guard
therefore misses pavement wrongly included in the mass (Q-189 Students' Union; Q-201 / task #16).

Dry run lists footprint cells that sit on map ``footway`` / ``steps`` / ``cycleway``
bands (import half-widths), optional open lidar columns (DSM−DTM below a threshold),
and the share of the footprint classified as ground on the aerial.

  python3 scripts/footprint_overlap.py --way 33830305
  python3 scripts/footprint_overlap.py --cells-json '[[10,20],[11,20]]' \\
      --street footway,10.0,22.5,20.0,22.5

Pure helpers accept synthetic footprint + street polylines so tests need no map clips.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from script_env import make_parser  # noqa: E402
from ground_sources import PATH_WIDTH, line_cells  # noqa: E402

# Highways that count as "path through the footprint" (Q-201).
PATH_HIGHWAYS = frozenset({'footway', 'steps', 'cycleway'})
# Aerial classes that read as open ground / paving (not shade/canopy).
AERIAL_GROUND = frozenset({'lawn', 'brick', 'light', 'dark'})
# DSM−DTM (metres) at or below this → column is open / not a roof mass.
OPEN_LIDAR_M = 1.0


def path_half_width(highway: str) -> float:
    """Band half-width in blocks for a path highway tag.

    @param highway: vector-map ``highway=*`` value
    @returns: half-width used for centreline→column raster
    """
    if highway == 'steps':
        return 1.2
    return PATH_WIDTH.get(highway, 2.0) / 2.0


def path_cells_in_footprint(
    footprint: set[tuple[int, int]],
    streets: Iterable[tuple[str, list[tuple[float, float]], dict | None]],
) -> dict[tuple[int, int], list[dict]]:
    """Footprint columns that fall inside a path highway band.

    @param footprint: building mass columns ``{(x, z)}``
    @param streets: ``(highway, [(x, z), ...], props_or_None)`` polylines in MC floats
    @returns: ``{(x, z): [{highway, osm_id, name, dist}, ...]}`` for overlapping columns
    """
    if not footprint:
        return {}
    hits: dict[tuple[int, int], list[dict]] = {}
    for hw, pts, props in streets:
        if hw not in PATH_HIGHWAYS or len(pts) < 2:
            continue
        half = path_half_width(hw)
        band = line_cells(pts, half)
        props = props or {}
        oid = props.get('osm_id') or props.get('osm_way_id')
        name = props.get('name')
        for q, (dist, _along) in band.items():
            if q not in footprint:
                continue
            hits.setdefault(q, []).append({
                'highway': hw,
                'osm_id': oid,
                'name': name,
                'dist': round(float(dist), 3),
            })
    return hits


def open_lidar_cells_in_footprint(
    footprint: set[tuple[int, int]],
    feature_m: Callable[[int, int], float | None] | dict[tuple[int, int], float],
    *,
    open_max_m: float = OPEN_LIDAR_M,
) -> set[tuple[int, int]]:
    """Footprint columns whose lidar standing height is at or below ``open_max_m``.

    @param footprint: building mass columns
    @param feature_m: ``(x, z) -> DSM−DTM metres`` or a precomputed dict (``None`` = no data)
    @param open_max_m: inclusive open threshold in metres
    @returns: columns that look open (path / plaza / mis-mapped fringe)
    """
    get = feature_m.get if isinstance(feature_m, dict) else feature_m
    out: set[tuple[int, int]] = set()
    for x, z in footprint:
        v = get((x, z)) if isinstance(feature_m, dict) else get(x, z)
        if v is None:
            continue
        if float(v) <= open_max_m:
            out.add((x, z))
    return out


def aerial_ground_share(
    footprint: set[tuple[int, int]],
    aerial: Callable[[int, int], str | None] | dict[tuple[int, int], str],
) -> dict:
    """Share of footprint columns classified as ground / paving on the aerial.

    @param footprint: building mass columns
    @param aerial: ``(x, z) -> aerial_class`` or dict (``None`` / missing = unclassified)
    @returns: ``{classified, ground, share, by_class}``; ``share`` is ``None`` when nothing classified
    """
    get = aerial.get if isinstance(aerial, dict) else aerial
    by_class: dict[str, int] = {}
    classified = 0
    ground = 0
    for x, z in footprint:
        c = get((x, z)) if isinstance(aerial, dict) else get(x, z)
        if not c:
            continue
        classified += 1
        by_class[c] = by_class.get(c, 0) + 1
        if c in AERIAL_GROUND:
            ground += 1
    share = (ground / classified) if classified else None
    return {
        'classified': classified,
        'ground': ground,
        'share': share,
        'by_class': by_class,
    }


@dataclass
class OverlapReport:
    """Result of a footprint overlap dry run."""

    footprint_n: int
    path_hits: dict[tuple[int, int], list[dict]] = field(default_factory=dict)
    open_lidar: set[tuple[int, int]] = field(default_factory=set)
    aerial: dict = field(default_factory=dict)

    @property
    def path_cells(self) -> set[tuple[int, int]]:
        """Columns that sit on at least one path highway band."""
        return set(self.path_hits)

    def lines(self, *, label: str | None = None) -> list[str]:
        """Human-readable dry-run lines.

        @param label: optional footprint label prefix
        @returns: printable report lines
        """
        pref = f'{label} ' if label else ''
        out = [
            f'{pref}footprint overlap: {self.footprint_n} cells; '
            f'path {len(self.path_cells)}; open lidar {len(self.open_lidar)}',
        ]
        by_hw: dict[str, int] = {}
        for hits in self.path_hits.values():
            for h in hits:
                by_hw[h['highway']] = by_hw.get(h['highway'], 0) + 1
        if by_hw:
            parts = ', '.join(f'{hw}×{n}' for hw, n in sorted(by_hw.items()))
            out.append(f'{pref}path highways: {parts}')
        if self.path_cells:
            sample = sorted(self.path_cells)[:8]
            out.append(f'{pref}path cells e.g. {sample}')
        if self.open_lidar:
            sample = sorted(self.open_lidar)[:8]
            out.append(f'{pref}open lidar e.g. {sample}')
        aer = self.aerial or {}
        if 'classified' in aer:
            share = aer.get('share')
            if share is None:
                out.append(f'{pref}aerial ground share: n/a (no classified columns)')
            else:
                out.append(
                    f'{pref}aerial ground share: {share:.0%} '
                    f'({aer.get("ground", 0)}/{aer.get("classified", 0)} classified)'
                )
        return out


def check_footprint(
    footprint: set[tuple[int, int]],
    streets: Iterable[tuple[str, list[tuple[float, float]], dict | None]] = (),
    *,
    feature_m: Callable[[int, int], float | None] | dict[tuple[int, int], float] | None = None,
    aerial: Callable[[int, int], str | None] | dict[tuple[int, int], str] | None = None,
    open_max_m: float = OPEN_LIDAR_M,
) -> OverlapReport:
    """Run path / open-lidar / aerial checks over one footprint.

    @param footprint: building mass columns
    @param streets: path highway polylines (see ``path_cells_in_footprint``)
    @param feature_m: optional lidar standing-height lookup
    @param aerial: optional aerial class lookup
    @param open_max_m: open-lidar threshold
    @returns: ``OverlapReport``
    """
    fp = set(footprint)
    path = path_cells_in_footprint(fp, streets)
    open_cells: set[tuple[int, int]] = set()
    if feature_m is not None:
        open_cells = open_lidar_cells_in_footprint(fp, feature_m, open_max_m=open_max_m)
    aer: dict = {}
    if aerial is not None:
        aer = aerial_ground_share(fp, aerial)
    return OverlapReport(
        footprint_n=len(fp),
        path_hits=path,
        open_lidar=open_cells,
        aerial=aer,
    )


def streets_from_index(idx) -> list[tuple[str, list[tuple[float, float]], dict]]:
    """Flatten an ``OSMIndex`` into ``(highway, pts, props)`` rows for path checks.

    @param idx: ``osm_footprints.OSMIndex``
    @returns: street list (all highways; path filter applied later)
    """
    out = []
    for (name, hw, pts), props in zip(idx.streets, idx.street_props):
        p = dict(props)
        if name and not p.get('name'):
            p['name'] = name
        out.append((hw, pts, p))
    return out


def footprint_for_way(idx, way_id: str | int) -> set[tuple[int, int]]:
    """Rasterised vector-map building columns for one way id.

    @param idx: ``OSMIndex``
    @param way_id: map way id (string or int; leading ``w`` stripped)
    @returns: ``{(x, z)}``
    @raises SystemExit: when the way is missing or has no cells
    """
    want = str(way_id).lstrip('w')
    cells: set[tuple[int, int]] = set()
    for (x, z), i in idx.cells().items():
        bp = idx.buildings[i][1]
        wid = str(bp.get('osm_way_id') or bp.get('osm_id') or '')
        if wid == want:
            cells.add((x, z))
    if not cells:
        raise SystemExit(f'no footprint cells for map way {want}')
    return cells


def report_for_cells(
    footprint: set[tuple[int, int]],
    *,
    idx=None,
    streets: Iterable[tuple[str, list[tuple[float, float]], dict | None]] | None = None,
    feature_m=None,
    aerial=None,
    label: str | None = None,
) -> list[str]:
    """Dry-run lines for a footprint (vector-map index and/or synthetic streets).

    @param footprint: building mass
    @param idx: optional ``OSMIndex`` (streets taken from it when ``streets`` omitted)
    @param streets: optional synthetic / override street list
    @param feature_m: optional open-lidar lookup
    @param aerial: optional aerial class lookup
    @param label: optional line prefix
    @returns: printable lines
    """
    if streets is None:
        streets = streets_from_index(idx) if idx is not None else ()
    return check_footprint(footprint, streets, feature_m=feature_m, aerial=aerial).lines(label=label)


def lidar_feature_lookup(footprint: set[tuple[int, int]]) -> dict[tuple[int, int], float] | None:
    """Standing height (DSM−DTM metres) per footprint column from cached composite lidar tiles.

    Does not download tiles. Returns ``None`` when the DTM/DSM cache does not cover the
    footprint (pristine sampling would otherwise report 0 and look falsely open).

    @param footprint: building mass columns
    @returns: ``{(x, z): feature_m}`` or ``None`` when tiles / frame are unavailable
    """
    if not footprint:
        return None
    try:
        import defra_lidar as dl
    except ImportError:
        return None
    x0 = min(x for x, _ in footprint)
    z0 = min(z for _, z in footprint)
    x1 = max(x for x, _ in footprint)
    z1 = max(z for _, z in footprint)
    try:
        lidar = dl.DefraLidar(download=False)
        e0, n0, e1, n1 = dl.mc_box_en(x0, z0, x1, z1)
        if lidar.missing('DTM', e0, n0, e1, n1) or lidar.missing('DSM', e0, n0, e1, n1):
            return None
        h = dl.mc_heights(lidar, x0, z0, x1, z1)
    except Exception:
        return None
    feat = h['feature']
    out: dict[tuple[int, int], float] = {}
    for x, z in footprint:
        out[(x, z)] = float(feat[x - x0, z - z0])
    return out


def aerial_class_lookup(footprint: set[tuple[int, int]]) -> dict[tuple[int, int], str] | None:
    """Aerial class per footprint column from the place mosaic / registered sources.

    @param footprint: building mass columns
    @returns: ``{(x, z): class}`` or ``None`` when aerial sampling fails
    """
    if not footprint:
        return None
    try:
        from ground_sources import aerial_class, aerial_rgb
    except ImportError:
        return None
    try:
        rgb = aerial_rgb(sorted(footprint))
    except Exception:
        return None
    return {q: aerial_class(c) for q, c in rgb.items() if c is not None}


def _parse_street(spec: str) -> tuple[str, list[tuple[float, float]], dict]:
    """Parse ``highway,x1,z1,x2,z2[,x3,z3...]`` into a street triple."""
    parts = spec.split(',')
    if len(parts) < 5 or (len(parts) - 1) % 2:
        raise SystemExit(f'--street wants highway,x1,z1,x2,z2,... got {spec!r}')
    hw = parts[0]
    nums = [float(v) for v in parts[1:]]
    pts = [(nums[i], nums[i + 1]) for i in range(0, len(nums), 2)]
    return hw, pts, {'name': 'synthetic'}


def main(argv: list[str] | None = None) -> int:
    """CLI entry: report path / lidar / aerial overlap for a way or cell list.

    @param argv: optional argv override (tests)
    @returns: process exit code
    """
    ap = make_parser(__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--way', help='vector-map building way id (e.g. 33830305)')
    g.add_argument('--cells-json', help='JSON list of [x,z] footprint columns')
    ap.add_argument('--street', action='append', default=[],
                    help='synthetic highway,x1,z1,x2,z2,... (repeatable; skips live map streets)')
    ap.add_argument('--osm-root', type=Path, help='vector-map clip root (default: world VECTOR_MAP_CLIPS)')
    ap.add_argument('--with-lidar', action='store_true',
                    help='also list open lidar columns (cached composite DSM−DTM)')
    ap.add_argument('--with-aerial', action='store_true',
                    help='also report aerial ground share (mosaic / registered)')
    ap.add_argument('--json', action='store_true', help='machine-readable summary on stdout')
    a = ap.parse_args(argv)

    streets = None
    idx = None
    if a.street:
        streets = [_parse_street(s) for s in a.street]
    elif a.way or not a.cells_json:
        import osm_footprints
        idx = osm_footprints.OSMIndex(root=a.osm_root) if a.osm_root else osm_footprints.OSMIndex()
        streets = streets_from_index(idx)

    if a.way:
        if idx is None:
            import osm_footprints
            idx = osm_footprints.OSMIndex(root=a.osm_root) if a.osm_root else osm_footprints.OSMIndex()
        footprint = footprint_for_way(idx, a.way)
        label = f'w{str(a.way).lstrip("w")}'
    else:
        raw = json.loads(a.cells_json)
        footprint = {(int(x), int(z)) for x, z in raw}
        label = 'cells'

    feature_m = lidar_feature_lookup(footprint) if a.with_lidar else None
    aerial = aerial_class_lookup(footprint) if a.with_aerial else None
    rep = check_footprint(footprint, streets or (), feature_m=feature_m, aerial=aerial)
    if a.json:
        print(json.dumps({
            'label': label,
            'footprint_n': rep.footprint_n,
            'path_n': len(rep.path_cells),
            'path_cells': sorted(rep.path_cells),
            'open_lidar_n': len(rep.open_lidar),
            'open_lidar': sorted(rep.open_lidar),
            'aerial': rep.aerial,
        }))
    else:
        for line in rep.lines(label=label):
            print(line)
        if a.with_lidar and feature_m is None:
            print(f'{label} open lidar: unavailable (no composite lidar cache / frame)')
        if a.with_aerial and aerial is None:
            print(f'{label} aerial ground share: unavailable')
    return 0


if __name__ == '__main__':
    sys.exit(main())

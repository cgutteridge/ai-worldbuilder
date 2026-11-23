"""Railway evidence for the line planner (a later ticket).

Turns an OSM railway extract into through-lines and decor, chains and pairs
the through lines, and records level crossings, rail bridges and tunnels, and
road bridges that cross over a through line. Nothing here places blocks.

**Per-cell skip.** ``railway`` is on ``CELL_EXTRACT_LAYERS``. The preset
already in ``osm_extraction.LAYER_PRESETS`` is one ``lines`` query
(``railway IS NOT NULL``), the same shape as waterway and barrier, not a
multipolygon scan. A 128-block cell usually has no railway ways, so the extra
GeoJSON is small. That cell clip is the index: a second file would only
repeat it. :func:`cell_has_railway` reads the one cell-evidence path it is
given. A missing file, or a clip with no railway line, is false and does not
open the planner-box extract.

**Box extract.** The planner's input is not cell-clipped. The live fetch is
``shims/osm_extract.py --layers railway`` over a 512 by 512 MC box (:data:`PLANNER_BOX`,
decision 17 in docs/plans/railway.md; a longer line is a chain of boxes). That command uses the railway lines preset with
``-spat`` and without ``-clipsrc``, so a way that leaves the box comes back
whole. :func:`extract_railway_box` reads that GeoJSON.

**Through and decor.** A through line is ``railway=rail`` with no ``service``
tag. ``usage`` does not matter. Sidings, crossovers, yards, depots, miniature,
disused, turntables and the rest are decor and are not chained.

**Chains.** Through ways join end to end when the endpoints are within 1.5
blocks. A chain stops at a junction: a node where more than two through ways
meet, or where a ``railway=switch`` node sits within 1.5 blocks. Junctions
are left for a person.

**Pairs.** Parallel chains whose centrelines sit about 4 blocks apart, and no
more than 8, become one :class:`Line` with one polyline per track.

**Crossings and spans.** A level crossing keeps ``crossing:barrier``,
``crossing:light``, ``crossing:bell``, ``crossing:activation`` and
``description``. A bridge or tunnel tagged on the rail is a gap span. A
highway or path with ``bridge`` / ``viaduct`` whose centre line crosses a
through track is an overhead span (deck columns, layer, position along each
track), kept apart from the rail's own spans.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import cellmath
import osm_extraction
import world_frame as wf
from osm_footprints import osm_tags

# Endpoint join, and how near a switch must be to mark that node a junction.
ENDPOINT_JOIN = 1.5
# Double track is about 4 blocks between centrelines; 8 is the widest we pair.
PAIR_MIN = 1.0
PAIR_MAX = 8.0
# Share of the shorter chain that must sit beside the other, clear of the ends.
PAIR_OVERLAP = 0.5
PAIR_END_CAP = 2.0
# A level-crossing node is on its own rail; the other rail is about 4 away.
CROSSING_SNAP = 2.0
# Two rail-bridge pieces are the same span when their middles are this close.
SPAN_GROUP = 20.0
# The line planner asks for a square of this edge. The box function takes any box.
PLANNER_BOX = 512

THROUGH = 'through'
DECOR = 'decor'


@dataclass(frozen=True)
class Way:
    """One OSM way in Minecraft x, z.

    @param osm_id: way id
    @param kind: ``through`` or ``decor``
    @param points: polyline, whole, not clipped to a cell
    @param tags: flattened OSM tags
    """

    osm_id: str
    kind: str
    points: tuple[tuple[int, int], ...]
    tags: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class MapPoint:
    """One OSM point in Minecraft x, z.

    @param osm_id: node id
    @param at: block column
    @param tags: flattened OSM tags
    """

    osm_id: str
    at: tuple[int, int]
    tags: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class LevelCrossing:
    """A level crossing on one track.

    Gates, lights and the bell are recorded only when the map says so.

    @param osm_id: node id
    @param line_id: line this track belongs to
    @param track_index: index into :attr:`Line.tracks`
    @param at: node column
    @param along: distance along the track polyline
    @param barrier: ``crossing:barrier``, or None
    @param light: ``crossing:light``, or None
    @param bell: ``crossing:bell``, or None
    @param activation: ``crossing:activation``, or None
    @param description: ``description``, or None
    """

    osm_id: str
    line_id: str
    track_index: int
    at: tuple[int, int]
    along: float
    barrier: str | None
    light: str | None
    bell: str | None
    activation: str | None
    description: str | None


@dataclass(frozen=True)
class TrackSpan:
    """Where one track runs through a rail bridge or tunnel.

    @param track_index: index into :attr:`Line.tracks`
    @param along0: distance at the start of the span
    @param along1: distance at the end of the span
    @param start: first column of the span
    @param end: last column of the span
    """

    track_index: int
    along0: float
    along1: float
    start: tuple[int, int]
    end: tuple[int, int]


@dataclass(frozen=True)
class RailSpan:
    """A bridge or tunnel that carries the railway (a gap in the line).

    @param kind: ``bridge`` or ``tunnel``
    @param line_id: line the span belongs to
    @param bridge: ``bridge`` tag, or None
    @param tunnel: ``tunnel`` tag, or None
    @param layer: ``layer`` tag, or None
    @param bridge_ref: ``bridge:ref``, or None
    @param tracks: the span on each track it covers
    """

    kind: str
    line_id: str
    bridge: str | None
    tunnel: str | None
    layer: str | None
    bridge_ref: str | None
    tracks: tuple[TrackSpan, ...]


@dataclass(frozen=True)
class OverheadCrossing:
    """Where an overhead deck crosses one track.

    @param track_index: index into :attr:`Line.tracks`
    @param along: distance along that track
    @param at: crossing column (x, z), which may sit between blocks
    """

    track_index: int
    along: float
    at: tuple[float, float]


@dataclass(frozen=True)
class OverheadSpan:
    """A road or path bridge over the railway. Not a gap in the rail.

    @param line_id: line the deck crosses
    @param osm_id: way id, or ``''``
    @param highway: ``highway`` tag
    @param bridge: ``bridge`` tag (``yes``, ``viaduct``, …)
    @param layer: ``layer`` tag, or None
    @param deck: columns of the deck, as :func:`ground_sources.line_cells` builds
    @param crossings: one hit per track the centre line actually crosses
    """

    line_id: str
    osm_id: str
    highway: str | None
    bridge: str | None
    layer: str | None
    deck: frozenset[tuple[int, int]]
    crossings: tuple[OverheadCrossing, ...]


@dataclass(frozen=True)
class Line:
    """A paired (or single) through line.

    @param id: stable id from the member way ids
    @param tracks: one polyline per track, Minecraft x, z
    @param track_way_ids: OSM way ids of each track, in polyline order
    @param crossings: level crossings on those tracks
    @param spans: bridges and tunnels that carry the rail
    @param overhead: road or path bridges over the rail
    """

    id: str
    tracks: tuple[tuple[tuple[int, int], ...], ...]
    track_way_ids: tuple[tuple[str, ...], ...]
    crossings: tuple[LevelCrossing, ...]
    spans: tuple[RailSpan, ...]
    overhead: tuple[OverheadSpan, ...]


@dataclass(frozen=True)
class _Chain:
    """One maximal through-chain, already pointed in a stable direction."""

    points: tuple[tuple[int, int], ...]
    way_ids: tuple[str, ...]
    parts: tuple[tuple[Way, bool], ...]


def classify(way: dict) -> str:
    """Classify one way as ``through`` or ``decor``.

    Through means ``railway=rail`` and no ``service`` tag. ``usage`` is
    ignored. Anything else is decor, including a rail way that has a service
    tag and a way whose ``railway`` is miniature, disused, turntable or switch.

    @param way: flattened OSM tags for one way
    @returns: ``through`` or ``decor``
    """
    railway = str(way.get('railway') or '')
    service = way.get('service')
    if railway == 'rail' and service in (None, ''):
        return THROUGH
    return DECOR


def cell_has_railway(
    cell: tuple[int, int],
    evidence: Path | None = None,
    *,
    frame: wf.WorldFrame | None = None,
) -> bool:
    """True when any railway line of any class touches the cell.

    Reads only ``evidence`` (the cell clip). A missing path, or a clip with
    no railway line, is false and does not open a box extract. When the clip
    does contain a railway line, ``frame`` is required to convert it.

    @param cell: ``(tx, tz)`` coverage cell
    @param evidence: cell GeoJSON, or None when the cell has no clip yet
    @param frame: world frame for a clip that contains a railway line
    @returns: whether a railway line touches the cell
    @raises ValueError: when the clip has a railway line and ``frame`` is omitted
    @raises ValueError: when ``evidence`` exists but is not a FeatureCollection
    """
    if evidence is None:
        return False
    path = Path(evidence)
    if not path.is_file():
        return False
    for feat in _features(path):
        if not _is_railway_line(feat):
            continue
        if frame is None:
            raise ValueError(
                'frame is required to test a railway line against a cell'
            )
        for line in _geom_lines(feat.get('geometry') or {}):
            points = _mc_line(line, frame)
            if _polyline_hits(points, cellmath.bounds(cell)):
                return True
    return False


def railway_box_plan(
    box_mc: tuple[int, int, int, int],
    frame: wf.WorldFrame,
) -> dict:
    """The unclipped railway-lines query for one Minecraft box.

    Same preset the ``railway`` layer uses. ``clip`` is false: ``-spat``
    selects ways, and the geometries are not cut to the box. The planner's
    box is :data:`PLANNER_BOX` on a side; any box is accepted.

    @param box_mc: inclusive ``(x0, z0, x1, z1)``
    @param frame: world frame for the spat conversion
    @returns: gdal layer, where, spat ``(lon_min, lat_min, lon_max, lat_max)``,
        ``clip`` false, and the normalised box
    @raises ValueError: when ``box_mc`` is not four numbers, or the preset is not one lines job
    """
    box = _norm_box(box_mc)
    jobs = osm_extraction.LAYER_PRESETS['railway']
    if len(jobs) != 1 or jobs[0][0] != 'lines':
        raise ValueError('railway preset must be one lines job')
    gdal_layer, where, models = jobs[0]
    x0, z0, x1, z1 = box
    lons: list[float] = []
    lats: list[float] = []
    for x, z in ((x0, z0), (x1, z0), (x0, z1), (x1, z1)):
        east, north = wf.mc_to_osgb(x, z, frame)
        lat, lon = wf.osgb_to_wgs84(east, north)
        lons.append(lon)
        lats.append(lat)
    return {
        'preset': 'railway',
        'gdal_layer': gdal_layer,
        'where': where,
        'models': list(models),
        'spat': (min(lons), min(lats), max(lons), max(lats)),
        'clip': False,
        'box_mc': box,
    }


def extract_railway_box(
    box_mc: tuple[int, int, int, int],
    *,
    source: Path,
    frame: wf.WorldFrame,
) -> tuple[Way, ...]:
    """Whole railway ways for ``box_mc``, from a railway-lines GeoJSON.

    Ways that touch the box are returned in full, including vertices outside
    the box. The file is the one ``shims/osm_extract.py --layers railway``
    writes for that box (see :func:`railway_box_plan`).

    @param box_mc: inclusive ``(x0, z0, x1, z1)``
    @param source: GeoJSON from that unclipped railway query
    @param frame: world frame for lat/lon to Minecraft
    @returns: ways of every class that touch the box
    @raises ValueError: when ``source`` is missing or not a FeatureCollection
    @raises ValueError: when ``box_mc`` is not four numbers
    """
    plan = railway_box_plan(box_mc, frame)
    path = Path(source)
    if not path.is_file():
        raise ValueError(f'missing railway extract: {path}')
    box = plan['box_mc']
    ways = []
    for way in _ways_from_geojson(path, frame):
        if _polyline_hits(way.points, box):
            ways.append(way)
    return tuple(ways)


def read_map_points(path: Path, frame: wf.WorldFrame) -> tuple[MapPoint, ...]:
    """Read OSM points (crossings, switches, and the rest) from a GeoJSON.

    The trial query was ``--gdal-layer points --where "other_tags LIKE
    '%level_crossing%'"``.

    @param path: points GeoJSON
    @param frame: world frame
    @returns: points in Minecraft coordinates
    @raises ValueError: when ``path`` is not a FeatureCollection
    """
    out = []
    for feat in _features(path):
        geom = feat.get('geometry') or {}
        if geom.get('type') != 'Point':
            continue
        coords = geom.get('coordinates') or []
        if len(coords) < 2:
            continue
        tags = _tag_pairs(osm_tags(feat.get('properties') or {}))
        osm_id = dict(tags).get('osm_id', '')
        out.append(MapPoint(osm_id, _to_mc(coords[0], coords[1], frame), tags))
    return tuple(out)


def build_lines(
    ways: tuple[Way, ...] | list[Way],
    points: tuple[MapPoint, ...] | list[MapPoint] = (),
    *,
    overhead: tuple[dict, ...] | list[dict] = (),
) -> tuple[Line, ...]:
    """Chain through ways, pair parallel chains, and attach points and spans.

    Decor ways are ignored for chaining. A chain stops where more than two
    through ways meet, and where a switch node marks the joint. Parallel
    chains within :data:`PAIR_MAX` blocks become one line. Overhead entries
    are highway or path ways already in Minecraft coordinates:
    ``{'osm_id', 'tags', 'points'}``.

    @param ways: railway ways, already in Minecraft coordinates
    @param points: nodes (switches and level crossings)
    @param overhead: road and path ways that may cross over the rail
    @returns: lines, sorted by id
    """
    through = [w for w in ways if w.kind == THROUGH and len(w.points) >= 2]
    switches = [
        p.at for p in points if _pairs_get(p.tags, 'railway') == 'switch'
    ]
    chains = _chain_through(through, switches)
    groups = _pair_chains(chains)
    built = [_assemble(group, points, overhead) for group in groups]
    built.sort(key=lambda line: line.id)
    return tuple(built)


def _assemble(chains: list[_Chain], points, overhead) -> Line:
    """One line from one or two chains, with crossings, spans and overhead."""
    ordered = sorted(chains, key=lambda c: (_mean(c.points), c.way_ids))
    way_ids = [i for chain in ordered for i in chain.way_ids]
    line_id = _line_id(way_ids)
    tracks = tuple(c.points for c in ordered)
    track_ids = tuple(c.way_ids for c in ordered)
    spans = _rail_spans(line_id, ordered)
    crossings = _crossings(line_id, tracks, points)
    over = _overhead(line_id, tracks, overhead)
    return Line(line_id, tracks, track_ids, crossings, spans, over)


def _chain_through(ways: list[Way], switches: list[tuple[int, int]]) -> list[_Chain]:
    """Maximal through-chains that stop at junctions and switch nodes."""
    n = len(ways)
    if n == 0:
        return []
    coords: list[tuple[int, int]] = []
    meta: list[tuple[int, int]] = []
    for i, way in enumerate(ways):
        coords.append(way.points[0])
        meta.append((i, 0))
        coords.append(way.points[-1])
        meta.append((i, 1))
    parent = list(range(len(coords)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a in range(len(coords)):
        for b in range(a + 1, len(coords)):
            if math.dist(coords[a], coords[b]) <= ENDPOINT_JOIN:
                union(a, b)

    clusters: dict[int, list[int]] = {}
    for i in range(len(coords)):
        clusters.setdefault(find(i), []).append(i)

    adj: list[list[tuple[int, int, int]]] = [[] for _ in range(n)]
    for members in clusters.values():
        ends: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        pts: list[tuple[int, int]] = []
        wset: set[int] = set()
        for mi in members:
            key = meta[mi]
            pts.append(coords[mi])
            wset.add(key[0])
            if key not in seen:
                seen.add(key)
                ends.append(key)
        marked = any(
            math.dist(p, s) <= ENDPOINT_JOIN for p in pts for s in switches
        )
        if len(wset) > 2 or marked or len(ends) != 2 or ends[0][0] == ends[1][0]:
            continue
        (w1, e1), (w2, e2) = ends
        adj[w1].append((w2, e1, e2))
        adj[w2].append((w1, e2, e1))

    visited: set[int] = set()
    chains: list[_Chain] = []
    for start in sorted(range(n), key=lambda i: (len(adj[i]), i)):
        if start in visited:
            continue
        links = adj[start]
        begin = (1 - links[0][1]) if len(links) == 1 else 0
        parts: list[tuple[Way, bool]] = []
        points: list[tuple[int, int]] = []
        cur: int | None = start
        cur_begin = begin
        while cur is not None and cur not in visited:
            visited.add(cur)
            forward = cur_begin == 0
            way = ways[cur]
            seq = list(way.points if forward else reversed(way.points))
            if points and seq and math.dist(seq[0], points[-1]) <= ENDPOINT_JOIN:
                seq = seq[1:]
            points.extend(seq)
            parts.append((way, forward))
            leave = 1 - cur_begin
            nxt = None
            for other, my_end, their_end in adj[cur]:
                if my_end == leave and other not in visited:
                    nxt = (other, their_end)
                    break
            if nxt is None:
                break
            cur, cur_begin = nxt
        if len(points) >= 2:
            chains.append(_finish_chain(parts, points))
    return chains


def _finish_chain(
    parts: list[tuple[Way, bool]],
    points: list[tuple[int, int]],
) -> _Chain:
    """Point the chain so it starts at the lexicographically smaller end."""
    pts = tuple(points)
    if len(pts) >= 2 and pts[-1] < pts[0]:
        pts = tuple(reversed(pts))
        parts = [(way, not fwd) for way, fwd in reversed(parts)]
    return _Chain(pts, tuple(way.osm_id for way, _fwd in parts), tuple(parts))


def _pair_chains(chains: list[_Chain]) -> list[list[_Chain]]:
    """Greedy one-to-one pairing. Each chain is in at most one pair."""
    scores: list[tuple[float, int, int]] = []
    for i in range(len(chains)):
        for j in range(i + 1, len(chains)):
            score = _pair_score(chains[i].points, chains[j].points)
            if score is not None:
                scores.append((score, i, j))
    scores.sort(reverse=True)
    used: set[int] = set()
    groups: list[list[_Chain]] = []
    for _score, i, j in scores:
        if i in used or j in used:
            continue
        used.add(i)
        used.add(j)
        groups.append([chains[i], chains[j]])
    for i, chain in enumerate(chains):
        if i not in used:
            groups.append([chain])
    return groups


def _pair_score(
    a: tuple[tuple[int, int], ...],
    b: tuple[tuple[int, int], ...],
) -> float | None:
    """Overlap length when two centrelines run about 4 blocks apart, else None."""
    if _length(a) > _length(b):
        a, b = b, a
    shorter = _length(a)
    longer = _length(b)
    if shorter < 1 or longer < 1:
        return None
    samples = _samples(a, 8)
    if len(samples) < 2:
        return None
    dists: list[float] = []
    for point in samples:
        dist, along = _project(point, b)
        if dist <= PAIR_MAX and PAIR_END_CAP < along < longer - PAIR_END_CAP:
            dists.append(dist)
    frac = len(dists) / len(samples)
    if frac < PAIR_OVERLAP or not dists:
        return None
    dists.sort()
    median = dists[len(dists) // 2]
    if median < PAIR_MIN or median > PAIR_MAX:
        return None
    return frac * shorter


def _rail_spans(line_id: str, chains: list[_Chain]) -> tuple[RailSpan, ...]:
    """Bridge and tunnel pieces on the rail, grouped into one span per structure."""
    pieces: list[dict] = []
    for ti, chain in enumerate(chains):
        for way, forward in chain.parts:
            bridge = _pairs_get(way.tags, 'bridge')
            tunnel = _pairs_get(way.tags, 'tunnel')
            has_bridge = bridge not in (None, '', 'no')
            has_tunnel = tunnel not in (None, '', 'no')
            if not has_bridge and not has_tunnel:
                continue
            seq = way.points if forward else tuple(reversed(way.points))
            _d0, a0 = _project(seq[0], chain.points)
            _d1, a1 = _project(seq[-1], chain.points)
            if a1 < a0:
                a0, a1 = a1, a0
                p0, p1 = seq[-1], seq[0]
            else:
                p0, p1 = seq[0], seq[-1]
            pieces.append({
                'kind': 'bridge' if has_bridge else 'tunnel',
                'bridge': bridge if has_bridge else None,
                'tunnel': tunnel if has_tunnel else None,
                'layer': _pairs_get(way.tags, 'layer'),
                'bridge_ref': _pairs_get(way.tags, 'bridge:ref'),
                'track_index': ti,
                'along0': a0,
                'along1': a1,
                'start': p0,
                'end': p1,
                'mid': ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2),
            })
    return tuple(_group_spans(line_id, pieces))


def _group_spans(line_id: str, pieces: list[dict]) -> list[RailSpan]:
    """Union pieces that share a bridge:ref, or sit on the same unnumbered bridge."""
    parent = list(range(len(pieces)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(len(pieces)):
        for j in range(i + 1, len(pieces)):
            a, b = pieces[i], pieces[j]
            if a['kind'] != b['kind']:
                continue
            same_ref = a['bridge_ref'] and a['bridge_ref'] == b['bridge_ref']
            both_open = not a['bridge_ref'] and not b['bridge_ref']
            close = math.dist(a['mid'], b['mid']) <= SPAN_GROUP
            if same_ref or (both_open and close):
                union(i, j)
    groups: dict[int, list[dict]] = {}
    for i, piece in enumerate(pieces):
        groups.setdefault(find(i), []).append(piece)
    spans = []
    for group in groups.values():
        group.sort(key=lambda p: p['track_index'])
        first = group[0]
        tracks = tuple(
            TrackSpan(p['track_index'], p['along0'], p['along1'], p['start'], p['end'])
            for p in group
        )
        spans.append(RailSpan(
            first['kind'], line_id, first['bridge'], first['tunnel'],
            first['layer'], first['bridge_ref'], tracks,
        ))
    spans.sort(key=lambda s: (s.tracks[0].start, s.bridge_ref or ''))
    return spans


def _crossings(line_id, tracks, points) -> tuple[LevelCrossing, ...]:
    """Snap each level-crossing node to its nearest track, once."""
    found = []
    for point in points:
        if _pairs_get(point.tags, 'railway') != 'level_crossing':
            continue
        best = None
        for ti, poly in enumerate(tracks):
            dist, along = _project(point.at, poly)
            if dist <= CROSSING_SNAP and (best is None or dist < best[0]):
                best = (dist, ti, along)
        if best is None:
            continue
        _dist, ti, along = best
        found.append(LevelCrossing(
            point.osm_id, line_id, ti, point.at, along,
            _pairs_get(point.tags, 'crossing:barrier'),
            _pairs_get(point.tags, 'crossing:light'),
            _pairs_get(point.tags, 'crossing:bell'),
            _pairs_get(point.tags, 'crossing:activation'),
            _pairs_get(point.tags, 'description'),
        ))
    found.sort(key=lambda c: (c.track_index, c.osm_id))
    return tuple(found)


def _overhead(line_id, tracks, overhead) -> tuple[OverheadSpan, ...]:
    """Road and path bridges whose centre line crosses a through track."""
    spans = []
    for road in overhead:
        tags = road.get('tags') or {}
        bridge = tags.get('bridge')
        if tags.get('highway') in (None, '') or bridge in (None, '', 'no'):
            continue
        pts = [tuple(p) for p in road.get('points') or ()]
        if len(pts) < 2:
            continue
        hits: list[OverheadCrossing] = []
        for ti, poly in enumerate(tracks):
            for along, at in _track_crossings(pts, poly):
                hits.append(OverheadCrossing(ti, along, at))
        if not hits:
            continue
        hits.sort(key=lambda h: h.track_index)
        spans.append(OverheadSpan(
            line_id,
            str(road.get('osm_id') or ''),
            tags.get('highway'),
            bridge,
            tags.get('layer'),
            _deck_columns(tags, pts),
            tuple(hits),
        ))
    return tuple(spans)


def _track_crossings(road, poly) -> list[tuple[float, tuple[float, float]]]:
    """Intersections of a road centre line with one track polyline."""
    hits = []
    acc = 0.0
    for c, d in zip(poly, poly[1:]):
        seg = math.dist(c, d)
        for a, b in zip(road, road[1:]):
            got = _seg_intersect(a, b, c, d)
            if got is None:
                continue
            (x, z), u = got
            if hits and math.dist((x, z), hits[-1][1]) < 0.5:
                continue
            hits.append((acc + u * seg, (x, z)))
        acc += seg
    return hits


def _deck_columns(tags: dict, points: list[tuple]) -> frozenset[tuple[int, int]]:
    """Deck columns for one overhead way, using the ground layer's own rule."""
    from ground_sources import ROAD_CLASSES, line_cells, road_width

    if tags.get('highway') in ROAD_CLASSES:
        half = max(1.5, road_width(tags) / 2)
    else:
        half = 1.5
    return frozenset(line_cells(points, half))


def _ways_from_geojson(path: Path, frame: wf.WorldFrame) -> list[Way]:
    """Every railway line in a GeoJSON, whole, in Minecraft coordinates."""
    ways = []
    for feat in _features(path):
        if not _is_railway_line(feat):
            continue
        tags = osm_tags(feat.get('properties') or {})
        osm_id = str(tags.get('osm_id') or '')
        lines = _geom_lines(feat.get('geometry') or {})
        for i, line in enumerate(lines):
            points = _mc_line(line, frame)
            if len(points) < 2:
                continue
            part = osm_id if len(lines) == 1 else f'{osm_id}#{i}'
            ways.append(Way(part, classify(tags), points, _tag_pairs(tags)))
    return ways


def _features(path: Path) -> list:
    """Feature list of a GeoJSON FeatureCollection.

    @param path: existing file
    @returns: features (possibly empty)
    @raises ValueError: when the JSON is not a FeatureCollection
    """
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get('type') != 'FeatureCollection':
        raise ValueError(f'{path} is not a GeoJSON FeatureCollection')
    return list(data.get('features') or [])


def _is_railway_line(feat: dict) -> bool:
    """True for a line feature that carries a ``railway`` tag."""
    geom = feat.get('geometry') or {}
    if geom.get('type') not in ('LineString', 'MultiLineString'):
        return False
    return bool(osm_tags(feat.get('properties') or {}).get('railway'))


def _geom_lines(geom: dict) -> list:
    """LineString coordinate lists inside one geometry."""
    gtype = geom.get('type')
    coords = geom.get('coordinates') or []
    if gtype == 'LineString':
        return [coords]
    if gtype == 'MultiLineString':
        return list(coords)
    return []


def _mc_line(coords, frame: wf.WorldFrame) -> tuple[tuple[int, int], ...]:
    """One lon/lat line to Minecraft x, z."""
    return tuple(_to_mc(c[0], c[1], frame) for c in coords if len(c) >= 2)


def _to_mc(lon: float, lat: float, frame: wf.WorldFrame) -> tuple[int, int]:
    """WGS84 lon/lat to Minecraft x, z via the world frame."""
    east, north = wf.wgs84_to_osgb(lat, lon)
    return wf.osgb_to_mc(east, north, frame)


def _tag_pairs(tags: dict) -> tuple[tuple[str, str], ...]:
    """Drop empty tags and freeze the rest."""
    return tuple(
        (str(k), str(v)) for k, v in tags.items() if v not in (None, '')
    )


def _pairs_get(tags: tuple[tuple[str, str], ...], key: str) -> str | None:
    """One tag value, or None when it is absent."""
    for k, v in tags:
        if k == key and v != '':
            return v
    return None


def _id_key(osm_id: str) -> tuple:
    """Sort numeric way ids in number order, and anything else after them."""
    text = str(osm_id)
    if text.isdigit():
        return (False, int(text))
    return (True, text)


def _line_id(way_ids: list[str]) -> str:
    """Stable id: member way ids, numeric order, joined."""
    ordered = sorted(way_ids, key=_id_key)
    return 'line-' + '-'.join(ordered)


def _norm_box(box_mc) -> tuple[int, int, int, int]:
    """Inclusive box with the corners ordered.

    @param box_mc: four numbers
    @returns: ``(x0, z0, x1, z1)`` with x0 <= x1 and z0 <= z1
    @raises ValueError: when there are not four numbers
    """
    try:
        x0, z0, x1, z1 = (int(v) for v in box_mc)
    except (TypeError, ValueError) as exc:
        raise ValueError('box_mc needs x0,z0,x1,z1') from exc
    if x0 > x1:
        x0, x1 = x1, x0
    if z0 > z1:
        z0, z1 = z1, z0
    return x0, z0, x1, z1


def _mean(points: tuple[tuple[int, int], ...]) -> tuple[float, float]:
    """Mean x, then mean z, for a stable track order."""
    n = len(points) or 1
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def _length(points) -> float:
    """Polyline length in blocks."""
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def _samples(points, step: float) -> list[tuple[float, float]]:
    """Points about ``step`` blocks apart along a polyline, including vertices."""
    if not points:
        return []
    out = [tuple(points[0])]
    prev = points[0]
    for p in points[1:]:
        seg = math.dist(prev, p)
        if seg:
            dist = step
            while dist < seg:
                t = dist / seg
                out.append((prev[0] + t * (p[0] - prev[0]), prev[1] + t * (p[1] - prev[1])))
                dist += step
        out.append(tuple(p))
        prev = p
    return out


def _project(point, poly) -> tuple[float, float]:
    """Distance from a point to a polyline, and the distance along it.

    @param point: ``(x, z)``
    @param poly: polyline
    @returns: ``(distance, along)``
    """
    px, pz = point
    if not poly:
        return 1e18, 0.0
    if len(poly) == 1:
        return math.dist(point, poly[0]), 0.0
    best = 1e18
    along = 0.0
    acc = 0.0
    for a, b in zip(poly, poly[1:]):
        ax, az = a
        bx, bz = b
        dx, dz = bx - ax, bz - az
        seg2 = dx * dx + dz * dz
        seg = math.sqrt(seg2) if seg2 else 0.0
        if seg2 == 0:
            t = 0.0
            dist = math.hypot(px - ax, pz - az)
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (pz - az) * dz) / seg2))
            dist = math.hypot(px - (ax + t * dx), pz - (az + t * dz))
        if dist < best:
            best = dist
            along = acc + t * seg
        acc += seg
    return best, along


def _polyline_hits(points, box: tuple[int, int, int, int]) -> bool:
    """True when a polyline touches the closed rectangle ``(x0, z0, x1, z1)``."""
    if len(points) < 1:
        return False
    x0, z0, x1, z1 = box
    for x, z in points:
        if x0 <= x <= x1 and z0 <= z <= z1:
            return True
    edges = (
        ((x0, z0), (x1, z0)),
        ((x1, z0), (x1, z1)),
        ((x1, z1), (x0, z1)),
        ((x0, z1), (x0, z0)),
    )
    for a, b in zip(points, points[1:]):
        for c, d in edges:
            if _segments_touch(a, b, c, d):
                return True
    return False


def _segments_touch(a, b, c, d) -> bool:
    """True when two segments cross or overlap, including at an endpoint."""
    if _seg_intersect(a, b, c, d) is not None:
        return True
    if abs(_orient(a, b, c)) > 1e-6 or abs(_orient(a, b, d)) > 1e-6:
        return False
    return (
        _on_seg(a, c, d) or _on_seg(b, c, d)
        or _on_seg(c, a, b) or _on_seg(d, a, b)
    )


def _seg_intersect(a, b, c, d):
    """Proper or touching intersection of AB with CD.

    @returns: ``((x, z), u)`` where ``u`` is the fraction along CD, or None
    """
    ax, az = a
    bx, bz = b
    cx, cz = c
    dx, dz = d
    rx, rz = bx - ax, bz - az
    sx, sz = dx - cx, dz - cz
    den = rx * sz - rz * sx
    if abs(den) < 1e-9:
        return None
    qpx, qpz = cx - ax, cz - az
    t = (qpx * sz - qpz * sx) / den
    u = (qpx * rz - qpz * rx) / den
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        u = min(1.0, max(0.0, u))
        return (cx + u * sx, cz + u * sz), u
    return None


def _orient(a, b, c) -> float:
    """Twice the signed area of triangle abc."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_seg(p, a, b) -> bool:
    """True when ``p`` is on segment ab (they are already collinear)."""
    return (
        min(a[0], b[0]) - 1e-6 <= p[0] <= max(a[0], b[0]) + 1e-6
        and min(a[1], b[1]) - 1e-6 <= p[1] <= max(a[1], b[1]) + 1e-6
    )

#!/usr/bin/env python3
"""Evidence for the ground layer: what is on the ground in a 128² cell.

Two sources, both read-only, neither tied to any one place:

  VECTOR_MAP  every ``*.geojson`` in the OSGB cache ``osm_footprints`` builds
         (``verification/site/osm27700``, including ``dir__name.geojson`` and legacy
         ``*-mp`` / ``*-ln`` / ``*-pt`` names), projected to Minecraft columns.
         Roads, paths and areas become surface kinds.
  AERIAL the z19 mosaic, sampled per column. Colour says lawn / brick paving / light
         paving / shade-or-canopy. It only fills columns the map does not describe.

Nothing here reads the Minecraft world, an earlier ground pass, a zone, or a campus polygon.
An optional ``reference/supplement-roads.json`` in the active world adds carriageway
centrelines the clips omit. No selected world, or no such file, adds nothing.

Kinds (the vocabulary the ground layer works in):
  grass  tarmac  paving  brick  gravel  dirt  water
"""
from __future__ import annotations

import json
import math
from collections import Counter

import numpy as np

from ref_manifest import load_manifest
from script_env import ROOT, world_bound
import cellmath  # noqa: E402

# vector-map surface=* → ground kind. 'paving' is split brick / light by the aerial.
SURFACE_KIND = {
    'asphalt': 'tarmac', 'paved': 'paving', 'paving_stones': 'paving', 'concrete': 'paving',
    'concrete:plates': 'paving', 'sett': 'brick', 'bricks': 'brick', 'cobblestone': 'brick',
    'gravel': 'gravel', 'fine_gravel': 'gravel', 'compacted': 'gravel', 'unpaved': 'gravel',
    'dirt': 'dirt', 'ground': 'dirt', 'earth': 'dirt', 'grass': 'grass', 'pebblestone': 'gravel',
    'tartan': 'tarmac', 'artificial_turf': 'grass',
}
# Priority: a higher number wins where two features cover one column.
# ``pitch`` is leisure pitch/playground/track and landuse=construction (#158): hard enough
# that classify() treats them as mapped surface (pri >= 40), above soft grass/wood.
PRI = {
    'wood': 20, 'grass': 30, 'pedestrian': 40, 'pitch': 45,
    'parking': 50, 'path': 60, 'service': 70, 'road': 90,
}
# sport=* values that imply a hard court when surface=* is absent (#158).
_HARD_SPORTS = frozenset({
    'tennis', 'basketball', 'multi', 'netball', 'handball', 'futsal',
    'skateboard', 'roller_skating', 'athletics', 'running',
})
ROAD_CLASSES = ('motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'unclassified', 'residential')
LANE_WIDTH = 3.0                      # metres ≈ blocks, one traffic lane
DEFAULT_LANES = 2
SERVICE_WIDTH = {'driveway': 3.0, 'parking_aisle': 5.0, 'alley': 3.5}
PATH_WIDTH = {'footway': 2.0, 'cycleway': 2.5, 'path': 1.6, 'pedestrian': 3.0, 'bridleway': 2.0, 'track': 3.0}
MARGIN = 4                            # columns of evidence kept outside the cell, for filters


def tags_of(props: dict) -> dict:
    """Merge ogr's ``other_tags`` hstore string into the plain property dict."""
    t = dict(props)
    for kv in (props.get('other_tags') or '').split('","'):
        if '=>' in kv:
            k, v = kv.split('=>', 1)
            t.setdefault(k.strip('"'), v.strip('"'))
    return t


def _coords(c):
    """Flatten any GeoJSON coordinate nesting to (E, N) pairs."""
    if isinstance(c[0], (int, float)):
        yield c[0], c[1]
    else:
        for s in c:
            yield from _coords(s)


def seg_dist(px: float, pz: float, a, b) -> tuple[float, float]:
    """Distance from a point to segment a-b, and the distance along it."""
    (ax, az), (bx, bz) = a, b
    l2 = (bx - ax) ** 2 + (bz - az) ** 2 or 1e-9
    t = max(0.0, min(1.0, ((px - ax) * (bx - ax) + (pz - az) * (bz - az)) / l2))
    return math.hypot(px - ax - t * (bx - ax), pz - az - t * (bz - az)), t * math.sqrt(l2)


def line_cells(pts, half: float) -> dict:
    """{(x, z): (distance, along)} for columns whose centre is within ``half`` of a polyline."""
    out, acc = {}, 0.0
    for a, b in zip(pts, pts[1:]):
        for x in range(math.floor(min(a[0], b[0]) - half) - 1, math.ceil(max(a[0], b[0]) + half) + 1):
            for z in range(math.floor(min(a[1], b[1]) - half) - 1, math.ceil(max(a[1], b[1]) + half) + 1):
                d, t = seg_dist(x + .5, z + .5, a, b)
                if d <= half and ((x, z) not in out or d < out[(x, z)][0]):
                    out[(x, z)] = (d, acc + t)
        acc += math.dist(a, b)
    return out


def centre_mark_cells(cells: dict) -> set:
    """Dashed road centre-line columns, always one cell wide.

    Keeps the 3-on / 3-off dash (``int(along) % 6 < 3``). Distance strictly
    under half a block wins; when the line falls on a block edge and two
    neighbours both have ``d == 0.5``, the lexicographically smaller column
    wins for that along-bucket so the dash is never two blocks wide (#147).

    @param cells: ``line_cells`` map ``{(x, z): (distance, along)}``
    @returns: set of ``(x, z)`` mark columns
    """
    best: dict[int, tuple[float, tuple[int, int]]] = {}
    for q, (d, a) in cells.items():
        if d > 0.5 or int(a) % 6 >= 3:
            continue
        key = int(a)
        cur = best.get(key)
        if cur is None or (d, q) < cur:
            best[key] = (d, q)
    return {q for _, q in best.values()}


def inside(px: float, pz: float, ring) -> bool:
    """Even-odd point-in-polygon."""
    c = False
    for (ax, az), (bx, bz) in zip(ring, ring[1:] + ring[:1]):
        if (az > pz) != (bz > pz) and px < ax + (pz - az) * (bx - ax) / (bz - az):
            c = not c
    return c


def _inside_grid(px, pz, ring):
    """``inside`` over numpy arrays of centres: same even-odd test, same float expression, so same answer."""
    c = np.zeros(px.shape, dtype=bool)
    with np.errstate(divide='ignore', invalid='ignore'):
        for (ax, az), (bx, bz) in zip(ring, ring[1:] + ring[:1]):
            if az == bz:
                continue                                   # horizontal edge never crosses: (az > pz) == (bz > pz)
            c ^= ((az > pz) != (bz > pz)) & (px < ax + (pz - az) * (bx - ax) / (bz - az))
    return c


def poly_cells(rings_list) -> set:
    """Columns inside polygons given as [[outer, hole, ...], ...] (MC coordinates)."""
    out = set()
    for rings in rings_list:
        o = rings[0]
        x0, x1 = math.floor(min(p[0] for p in o)), math.ceil(max(p[0] for p in o))
        z0, z1 = math.floor(min(p[1] for p in o)), math.ceil(max(p[1] for p in o))
        xs, zs = np.meshgrid(np.arange(x0, x1 + 1), np.arange(z0, z1 + 1), indexing='ij')
        hit = _inside_grid(xs + .5, zs + .5, o)
        for h in rings[1:]:
            hit &= ~_inside_grid(xs + .5, zs + .5, h)
        ix, iz = np.nonzero(hit)
        out.update(zip((ix + x0).tolist(), (iz + z0).tolist()))
    return out


# trees_layer (edited by another task) still imports this name; delete the alias once it uses cellmath.bounds.
cell_box = cellmath.bounds


# --- vector_map ---------------------------------------------------------------------------
# Optional world file: {"tx,tz": [{"points": [[x, z], ...], "tags": {...}}, ...]}.
_ROAD_SUPPLEMENT = ROOT / 'reference' / 'supplement-roads.json'


def _supplement_road_lines(cell: tuple[int, int]) -> list[tuple[dict, str, list]]:
    """Carriageway lines for ``cell`` from ``reference/supplement-roads.json``, or none.

    No selected world, a missing file, or no entry for this cell yields ``[]``.
    Each present row becomes ``(tags, 'line', [(x, z), ...])`` in file order.

    @param cell: ``(tx, tz)``
    @returns: line features to append after the vector-map cache
    @raises ValueError: when the file is not an object, or this cell's entry is not a list
    """
    if not world_bound():
        return []
    spec = _ROAD_SUPPLEMENT
    if not spec.is_file():
        return []
    data = json.loads(spec.read_text())
    if not isinstance(data, dict):
        raise ValueError(f'{spec} must be a JSON object keyed by cell')
    key = f'{cell[0]},{cell[1]}'
    rows = data.get(key, [])
    if not rows:
        return []
    if not isinstance(rows, list):
        raise ValueError(f'{spec} entry {key} must be a list')
    out = []
    for row in rows:
        pts = [(p[0], p[1]) for p in row['points']]
        out.append((dict(row['tags']), 'line', pts))
    return out


def load_osm(cell: tuple[int, int], margin: int = MARGIN + 8) -> list[tuple[dict, str, list]]:
    """Vector-map features touching the cell: [(tags, 'poly'|'line'|'point', geometry in MC)].

    Reads the OSGB cache via ``osm_footprints.cached_geojson_paths`` (reprojects
    clips not yet cached). poly geometry is [[outer, hole, ...], ...]; line is
    [(x, z), ...]; point is (x, z). Duplicates across extracts (same map id and
    tags) are dropped. Lines for this cell from ``reference/supplement-roads.json``
    are appended when that file is present.
    """
    from osm_footprints import cached_geojson_paths
    from world_frame import osgb_to_mc
    mc = lambda c: osgb_to_mc(c[0], c[1])
    x0, z0, x1, z1 = cellmath.bounds(cell, margin)
    seen, out = set(), []
    for path in cached_geojson_paths():
        for f in json.loads(path.read_text())['features']:
            g = f.get('geometry')
            if not g:
                continue
            pts = [mc(c) for c in _coords(g['coordinates'])]
            if not any(x0 <= x <= x1 and z0 <= z <= z1 for x, z in pts):
                continue
            t = tags_of(f['properties'])
            key = (t.get('osm_id') or t.get('osm_way_id'), json.dumps(g['coordinates'])[:80])
            if key in seen:
                continue
            seen.add(key)
            if g['type'] in ('Polygon', 'MultiPolygon'):
                polys = [g['coordinates']] if g['type'] == 'Polygon' else g['coordinates']
                out.append((t, 'poly', [[[mc(c[:2]) for c in r] for r in poly] for poly in polys]))
            elif g['type'] in ('LineString', 'MultiLineString'):
                lines = [g['coordinates']] if g['type'] == 'LineString' else g['coordinates']
                for ln in lines:
                    out.append((t, 'line', [mc(c[:2]) for c in ln]))
            elif g['type'] == 'Point':
                out.append((t, 'point', mc(g['coordinates'][:2])))
    out.extend(_supplement_road_lines(tuple(cell)))
    return out


def _tag_negative(raw: str | None) -> bool:
    """True when an OSM layer/level tag encodes a value below ground (starts with ``-``)."""
    return bool(raw) and str(raw).startswith('-')


def waterway_is_underground(t: dict) -> bool:
    """True when a waterway line must not become open-water surface columns (#162).

    Skips: ``tunnel`` with any value except ``building_passage``, ``covered=yes``,
    ``layer<0``, ``level<0``, and any ``indoor=*``. ``building_passage`` is not a
    waterway case and is left alone. Drain lines are not accepted waterways.

    @param t: OSM tags for one line feature.
    @returns: Whether the line is underground / covered / indoor.
    """
    tunnel = t.get('tunnel')
    if tunnel and tunnel != 'building_passage':
        return True
    if t.get('covered') == 'yes':
        return True
    if t.get('indoor') not in (None, ''):
        return True
    if _tag_negative(t.get('layer')) or _tag_negative(t.get('level')):
        return True
    return False


def water_way_ref(t: dict) -> str | None:
    """OSM way ref for a water polygon (``w<id>``), or ``None`` when untagged (#163).

    Prefers ``osm_way_id`` (ogr multipolygon members), else ``osm_id`` (standalone
    water ways). Always uses the ``w`` prefix so pond plan ids match building refs.

    @param t: OSM tags / properties for one polygon feature.
    @returns: e.g. ``'w141657072'``, or ``None`` when no id is present.
    """
    wid = t.get('osm_way_id') or t.get('osm_id')
    if wid is None or str(wid).strip() == '':
        return None
    return 'w' + str(wid).lstrip('w')


def _painted_area_tags(t: dict) -> bool:
    """True when polygon tags are a leisure/landuse/natural area OSMGround paints (#158)."""
    return (
        t.get('leisure') in ('pitch', 'playground', 'track')
        or t.get('landuse') in ('construction', 'allotments')
        or t.get('natural') in ('wetland', 'grassland', 'shrubbery')
    )


def area_floor_kind(t: dict) -> str | None:
    """Ground kind for a leisure / construction / allotment / soft-natural polygon (#158).

    Prefers ``surface=*`` via ``SURFACE_KIND``. When surface is absent, leisure=pitch
    uses ``sport=*`` (hard courts → tarmac, else grass); playground defaults to paving;
    track defaults to tarmac (tartan); construction and allotments to dirt; wetland /
    grassland / shrubbery to grass. Returns ``None`` when the tags are not an area
    this reader paints (including landuse=residential/retail/commercial/industrial).

    @param t: OSM tags for one polygon feature.
    @returns: Ground kind string, or ``None`` if unread.
    """
    if not _painted_area_tags(t):
        return None
    if surf := SURFACE_KIND.get(t.get('surface') or ''):
        return surf
    leisure = t.get('leisure')
    if leisure == 'pitch':
        sports = {s.strip() for s in (t.get('sport') or '').split(';') if s.strip()}
        if sports & _HARD_SPORTS:
            return 'tarmac'
        return 'grass'
    if leisure == 'playground':
        return 'paving'
    if leisure == 'track':
        return 'tarmac'
    if t.get('landuse') in ('construction', 'allotments'):
        return 'dirt'
    return 'grass'  # wetland / grassland / shrubbery


def area_floor_pri(t: dict) -> int | None:
    """Priority for an area painted by ``area_floor_kind`` (#158).

    Pitch / playground / track / construction use ``PRI['pitch']`` so classify() keeps
    them as mapped surface. Allotments, wetland and grassland use ``PRI['grass']``;
    shrubbery uses ``PRI['wood']`` (same band as scrub).

    @param t: OSM tags for one polygon feature.
    @returns: Priority int, or ``None`` when the area is unread.
    """
    if not _painted_area_tags(t):
        return None
    leisure = t.get('leisure')
    if leisure in ('pitch', 'playground', 'track') or t.get('landuse') == 'construction':
        return PRI['pitch']
    if t.get('natural') == 'shrubbery':
        return PRI['wood']
    return PRI['grass']


def road_width(t: dict) -> float:
    """Carriageway width in blocks from tags: width=*, else lanes (+ painted cycle lanes)."""
    if t.get('width'):
        try:
            return float(t['width'].split()[0])
        except ValueError:
            pass
    hw = t.get('highway')
    if hw == 'service':
        return SERVICE_WIDTH.get(t.get('service'), 4.0)
    if hw == 'track':
        return 3.0
    try:
        lanes = int(t.get('lanes', DEFAULT_LANES))
    except ValueError:
        lanes = DEFAULT_LANES
    w = LANE_WIDTH * lanes
    if t.get('cycleway:both') == 'lane' or t.get('cycleway') == 'lane':
        try:
            w += 2 * float(t.get('cycleway:width', 1.5))
        except ValueError:
            w += 3.0
    return w


STEPS_HALF_WIDTH = 1.2     # highway=steps half width in blocks


def _way_line(t: dict, pts, half: float) -> dict:
    """One mapped way as the furniture inference reads it: class, centreline, half width, lit and footway tags."""
    return {'highway': t.get('highway'), 'points': [(float(p[0]), float(p[1])) for p in pts],
            'half': float(half), 'lit': t.get('lit'), 'footway': t.get('footway')}


class OSMGround:
    """Vector map rasterised to one kind + priority per column, plus road/barrier extras."""

    def __init__(self, cell: tuple[int, int]):
        self.cell = cell
        self.kind: dict = {}          # (x, z) -> kind from vector map
        self.pri: dict = {}
        self.feat: dict = {}          # (x, z) -> id of the mapped line (path/road) that put the kind there
        self._fid = 0
        self.road: set = set()        # public carriageway columns (kerb candidates)
        self.marks: set = set()       # centre-line dash columns
        self.water: set = set()       # polygon natural=water only (pond candidates; #163)
        self.water_line: set = set()  # waterway line columns (streams); not pond candidates
        self.water_refs: dict = {}    # (x, z) -> 'w<way id>' for polygon water (#163)
        self.bridge: set = set()      # columns of every mapped bridge, tunnel and layer!=0 way (deck | tunnel)
        self.deck: set = set()        # columns under a mapped bridge or raised way: the ground below is ground's (#298)
        self.tunnel: set = set()      # columns over a mapped tunnel or underground way: the surface above is ground's
        self.steps: dict = {}         # (x, z) -> 'x' | 'z': ground-level highway=steps, the way's run axis (#294)
        self.hedge: set = set()
        self.wall: set = set()
        self.fence: set = set()
        self.soft: set = set()        # mapped surface car park columns: only a sharp registered aerial lawn overrides tarmac (#338)
        self.parking_areas: list = []  # one column set per surface car park polygon (#336, bay lines)
        self.parking_aisles: list = []  # {'points', 'half'} per service=parking_aisle centreline (#336)
        self.trees: list = []         # (x, z) natural=tree points
        self.tree_rows: list = []     # natural=tree_row polylines as [(x, z), ...] in MC (#157)
        self.furniture: list = []     # (x, z, kind) street furniture from Point features (#127)
        self.road_lines: list = []    # {'highway','points','half','lit','footway'} per mapped carriageway (#260)
        self.path_lines: list = []    # same, per mapped path (footway, path, cycleway, pedestrian, ...)
        feats = load_osm(cell)
        self.n_features = len(feats)  # reporting for dry runs (#113); 0 is legitimate
        self._load(feats)

    def _put(self, cells, kind: str, pri: int, feat: int | None = None) -> None:
        x0, z0, x1, z1 = cellmath.bounds(self.cell, MARGIN)
        for q in cells:
            if x0 <= q[0] <= x1 and z0 <= q[1] <= z1 and pri >= self.pri.get(q, -1):
                self.kind[q], self.pri[q] = kind, pri
                if feat is None:
                    self.feat.pop(q, None)
                else:
                    self.feat[q] = feat

    def _load(self, feats) -> None:
        from street_furniture import classify

        for t, geom, g in feats:
            if geom == 'poly':
                self._poly(t, g)
            elif geom == 'line':
                self._line(t, g)
            elif geom == 'point':
                kind = classify(t)
                if kind:
                    self.furniture.append((int(math.floor(g[0])), int(math.floor(g[1])), kind))
                elif t.get('natural') == 'tree':
                    self.trees.append((int(math.floor(g[0])), int(math.floor(g[1]))))
            elif t.get('natural') == 'tree':
                self.trees.append((int(math.floor(g[0])), int(math.floor(g[1]))))

    def _poly(self, t: dict, polys) -> None:
        if t.get('natural') == 'water':
            cells = poly_cells(polys)
            self.water |= cells
            ref = water_way_ref(t)
            if ref:
                for q in cells:
                    self.water_refs[q] = ref
        elif t.get('highway') == 'pedestrian' or t.get('area:highway'):
            self._put(poly_cells(polys), SURFACE_KIND.get(t.get('surface'), 'paving'), PRI['pedestrian'])
        elif t.get('amenity') == 'parking' and t.get('parking') not in ('underground', 'multi-storey', 'rooftop'):
            # surface car park: tarmac (#338; a narrow exception to #158, which keeps land use out of ground paint)
            cs = poly_cells(polys)
            x0, z0, x1, z1 = cellmath.bounds(self.cell, MARGIN)
            self.parking_areas.append({q for q in cs if x0 <= q[0] <= x1 and z0 <= q[1] <= z1})
            self._put(cs, 'tarmac', PRI['parking'])
            self.soft |= {q for q in cs if self.pri.get(q) == PRI['parking']}
        elif t.get('amenity') == 'bicycle_parking':
            self._put(poly_cells(polys), 'paving', PRI['pedestrian'])
        elif t.get('landuse') in ('grass', 'meadow', 'flowerbed', 'recreation_ground') or \
                t.get('leisure') in ('garden', 'park'):
            self._put(poly_cells(polys), 'grass', PRI['grass'])
        elif t.get('natural') in ('wood', 'scrub') or t.get('landuse') == 'forest':
            self._put(poly_cells(polys), 'grass', PRI['wood'])
        elif (floor := area_floor_kind(t)) is not None:
            # leisure pitch/playground/track; landuse construction/allotments;
            # natural wetland/grassland/shrubbery — surface=* / sport=* choose the floor (#158).
            self._put(poly_cells(polys), floor, area_floor_pri(t))
        if t.get('barrier') == 'hedge':
            for rings in polys:
                self.hedge |= set(line_cells(rings[0], 0.6))

    def _line(self, t: dict, pts) -> None:
        hw, b = t.get('highway'), t.get('barrier')
        if t.get('natural') == 'tree_row':
            # Keep the polyline in MC; trees_layer samples it into a crown sequence (#157).
            self.tree_rows.append([(float(p[0]), float(p[1])) for p in pts])
            return
        if t.get('waterway') in ('stream', 'river', 'canal', 'ditch'):
            if waterway_is_underground(t):
                return
            self.water_line |= set(line_cells(pts, 0.8))
            return
        if hw and (t.get('bridge') in ('yes', 'viaduct') or t.get('tunnel') == 'yes' or
                   t.get('layer', '0') not in ('0', '')):
            cells = set(line_cells(pts, max(1.5, road_width(t) / 2 if hw in ROAD_CLASSES else 1.5)))
            self.bridge |= cells
            if t.get('bridge') not in ('yes', 'viaduct') and (t.get('tunnel') == 'yes' or _tag_negative(t.get('layer'))):
                self.tunnel |= cells
            else:
                self.deck |= cells
            return
        if hw in ROAD_CLASSES or hw == 'service' or hw == 'track':
            half = road_width(t) / 2
            self.road_lines.append(_way_line(t, pts, half))
            if hw == 'service' and t.get('service') == 'parking_aisle':
                self.parking_aisles.append({'points': [(float(p[0]), float(p[1])) for p in pts], 'half': float(half)})
            cells = line_cells(pts, half)
            kind = SURFACE_KIND.get(t.get('surface'), 'tarmac')
            pri = PRI['service'] if hw in ('service', 'track') else PRI['road']
            self._put(cells, kind, pri)
            if hw in ROAD_CLASSES and kind == 'tarmac':
                self.road |= set(cells)
                if half >= 3 and t.get('oneway') != 'yes':
                    self.marks |= centre_mark_cells(cells)
        elif hw in PATH_WIDTH:
            self.path_lines.append(_way_line(t, pts, PATH_WIDTH[hw] / 2))
            self._fid += 1
            self._put(line_cells(pts, PATH_WIDTH[hw] / 2),
                      SURFACE_KIND.get(t.get('surface'), 'tarmac' if hw == 'cycleway' else 'paving'), PRI['path'],
                      self._fid)
        elif hw == 'steps':
            # Outdoor steps at ground level: surfaced by the ground layer at lidar height (#294).
            # Bridge, tunnel and layer!=0 steps returned above, into .bridge (.deck / .tunnel).
            for a, b in zip(pts, pts[1:]):
                axis = 'x' if abs(b[0] - a[0]) >= abs(b[1] - a[1]) else 'z'
                for q in line_cells([a, b], STEPS_HALF_WIDTH):
                    self.steps.setdefault(q, axis)
        elif b == 'hedge':
            self.hedge |= set(line_cells(pts, 0.6))
        elif b in ('wall', 'retaining_wall'):
            self.wall |= set(line_cells(pts, 0.5))
        elif b in ('fence', 'railings', 'guard_rail'):
            self.fence |= set(line_cells(pts, 0.5))


# --- aerial ------------------------------------------------------------------------------
class RegRGB(tuple):
    """A colour sampled from a registered user aerial (3D render / screenshot), not the z19 mosaic.

    Same 0..1 RGB triple, but ``aerial_class`` reads it with the registered-source thresholds.
    """
    __slots__ = ()


def aerial_rgb(cell: tuple[int, int], margin: int = MARGIN) -> dict:
    """Colour (0..1) per column: registered user aerials where one covers it, else the z19 mosaic.

    The mosaic gives a 5x5 pixel mean; registered columns are RegRGB (see ``aerial_class``).
    """
    from aerial_coverage import sample_rgb
    from aerial_registered import sample_rgb as registered_sample_rgb
    x0, z0, x1, z1 = cellmath.bounds(cell, margin)
    pts = [(x, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)]
    out = sample_rgb(cell, margin=margin)
    # sharper, truer colour wins where it exists
    out.update({p: RegRGB(rgb) for p, rgb in registered_sample_rgb(pts).items()})
    return out


def hsv(rgb) -> tuple[float, float, float]:
    """(hue°, saturation, mean luminance) of a 0..1 RGB triple."""
    r, g, b = rgb
    mx, mn = max(rgb), min(rgb)
    d = mx - mn
    h = 0.0
    if d > 0:
        h = (((g - b) / d) % 6 if mx == r else (b - r) / d + 2 if mx == g else (r - g) / d + 4) * 60
    return h, (d / mx if mx else 0.0), (r + g + b) / 3


def _registered_class(h: float, s: float, lum: float) -> str:
    """Class of a registered user aerial colour (3D render: true colour, neutral paving, blue roofs).

    Calibrated on cell 47,-45 against the strip beside Building 2 and the promenade: lawn there is
    green (hue 80-165, s 0.24-0.36, lum 0.25-0.55, dark under shade) where the sun-bleached mosaic
    shows khaki, paving is neutral or cream (hue 15-60, s <= 0.12) and block paving is pink (hue <= 12 or >= 330; salmon s 0.25-0.30, pale s 0.09-0.14).
    Blue-violet colour is a leaning roof or its shadow (the ground is unseen), so it counts as shade.
    """
    if s >= 0.15 and 190 <= h <= 300:
        return 'shade'
    if s >= 0.17 and 31 <= h <= 170:
        return 'lawn'
    if s >= 0.20 and (h < 31 or h >= 330):
        return 'brick'
    if s >= 0.085 and (h >= 330 or h <= 12):              # pale pink block paving; cream/tan (hue 15-60) stays paving
        return 'brick'
    return 'light' if lum >= 0.45 else 'dark'


def aerial_class(rgb) -> str:
    """Colour → shade | lawn | brick | light | dark. A RegRGB (registered user aerial) uses its own thresholds.

    The aerial here is low-saturation and sun-bleached, so lawn is khaki, not green:
    hue (olive-yellow vs orange-red) separates lawn from brick paving, saturation separates
    both from neutral paving, and dark + saturated is shade or canopy (ground unseen).
    """
    h, s, lum = hsv(rgb)
    if isinstance(rgb, RegRGB):
        return _registered_class(h, s, lum)
    if lum < 0.50 and s >= 0.22:
        return 'shade'
    if s >= 0.13 and 31 <= h <= 110:
        return 'lawn'
    if s >= 0.14 and h < 31:
        return 'brick'
    return 'light' if lum >= 0.45 else 'dark'


def majority(grid: dict, free: set, passes: int = 2, need: int = 5) -> dict:
    """3×3 majority filter over ``free`` columns so single-pixel speckle does not become blocks."""
    cur = dict(grid)
    for _ in range(passes):
        new = {}
        for q in free:
            c = Counter(cur.get((q[0] + a, q[1] + b)) for a in (-1, 0, 1) for b in (-1, 0, 1))
            c.pop(None, None)
            k, n = c.most_common(1)[0] if c else (cur.get(q), 0)
            new[q] = k if n >= need else cur.get(q)
        cur.update(new)
    return cur


# --- reference manifest ---------------------------------------------------------------------
def cell_coverage(row: dict, tx: int, tz: int, size: int = 128) -> float:
    """Fraction of a coverage cell's area inside a manifest row's ``bbox_mc`` (0 when it has none).

    ``mc_tiles`` lists every tile a row merely touches, so a mosaic that clips a cell edge by a few rows still
    lists the cell. This is the test for "does this aerial actually show the cell".

    @param row: manifest row
    @param tx: cell X
    @param tz: cell Z
    @param size: cell size in blocks
    @returns: 0.0 to 1.0
    """
    b = row.get('bbox_mc')
    if not b:
        return 0.0
    x0, z0 = tx * size, tz * size
    w = min(b[2], x0 + size - 1) - max(b[0], x0) + 1
    h = min(b[3], z0 + size - 1) - max(b[1], z0) + 1
    return max(0, w) * max(0, h) / (size * size)


def references_for_cell(
    tx: int,
    tz: int,
    *,
    useful_only: bool = True,
) -> list[dict]:
    """Manifest rows whose ``mc_tiles`` overlap this coverage cell.

    @param tx: cell X
    @param tz: cell Z
    @param useful_only: omit ``useful=no`` rows
    @returns: matching manifest rows (may be empty)
    """
    tile = cellmath.mc_tile_for_cell(tx, tz)
    rows = load_manifest()
    out: list[dict] = []
    for r in rows:
        if useful_only and r.get('useful') == 'no':
            continue
        if tile not in (r.get('mc_tiles') or []):
            continue
        out.append(r)
    return out

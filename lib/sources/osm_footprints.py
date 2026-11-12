#!/usr/bin/env python3
"""Vector-map building footprints + named streets in Minecraft coords — the frontage authority.

Reads every map clip under this world's ``reference/vector_map`` tree (see
``vector_map_clips``: download PBF → ``downloads/`` → extract → project reference).
Clips are written by the place's osm_extract provider (WGS84), reprojected once to
OSGB (EPSG:27700, via ogr2ogr; cached under verification/site/osm27700/) and
converted to MC X/Z with osgb.py. Optional ``root=`` / ``osm_root=`` is a test
seam for alternate trees — not a cross-world production path.

Why this exists (I-009, 11 Oct 2026): façade builds placed a brick front wall
on the pavement of Broadlands Road because the "street edge" was guessed from
surface blocks. Real houses sat ~6 blocks back behind front gardens. The
house front line comes from these footprints, not from block surveys.

  # which street am I on, where are the fronts, how deep are the gardens?
  python3 scripts/osm_footprints.py frontage --bbox 7040,-5560,7140,-5500
  python3 scripts/osm_footprints.py frontage --street "Broadlands Road" --bbox ...
  # audit cells (a build's archive, or every live kit-owned cell)
  python3 scripts/osm_footprints.py check --build <build_name>
  python3 scripts/osm_footprints.py check --all
  # first-guess property plots (house + front + back garden) for whole-property builds
  python3 scripts/osm_footprints.py plots --bbox 7040,-5560,7140,-5500

site_kit.apply() runs the same check on every plan (see guard()).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

from script_env import ROOT  # noqa: E402
from vector_map_clips import vector_map_clip_root  # noqa: E402

# Deferred until a world is selected (``ROOT`` is a deferred path). Prefer
# ``vector_map_clip_root()`` (legacy folder fallback) on first attribute access.
CACHE = ROOT / 'verification/site/osm27700'
_VECTOR_MAP_CLIPS = None


def _resolve_vector_map_clips():
    """Resolve clip root once a world is selected.

    @returns: path to ``reference/vector_map`` (or legacy equivalent)
    """
    global _VECTOR_MAP_CLIPS
    if _VECTOR_MAP_CLIPS is not None:
        return _VECTOR_MAP_CLIPS
    try:
        _VECTOR_MAP_CLIPS = vector_map_clip_root()
    except (FileNotFoundError, RuntimeError):
        _VECTOR_MAP_CLIPS = ROOT / 'reference' / 'vector_map'
    return _VECTOR_MAP_CLIPS


def __getattr__(name: str):
    """Lazy ``VECTOR_MAP_CLIPS`` after ``--world`` / ``set_world_root``."""
    if name == 'VECTOR_MAP_CLIPS':
        return _resolve_vector_map_clips()
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')

# Blocks that may legitimately stand outside a building footprint and rise above
# the ground: garden walls/fences/hedges, trees, street furniture.
FURNITURE = ('_wall', '_fence', 'fence_gate', 'leaves', '_log', '_wood', 'lantern', 'iron_chain',
             'sign', 'banner', 'iron_bars', 'end_rod', 'torch', 'flower', 'bush', 'azalea',
             'sapling', 'grass', 'fern', 'vine', 'potted', 'flower_pot', 'hopper', 'cauldron',
             'lightning_rod', 'button', 'lever', 'composter', 'barrel', 'scaffolding')
# Roof plates / canopy solids: never use the back-garden shed height exemption
# (R-008: red_concrete parasols at BACK_RISE on open terrace passed as sheds).
ROOF_LIKE = ('_concrete', '_slab')
AIRS = ('minecraft:air', 'minecraft:cave_air', 'minecraft:light', 'minecraft:void_air', 'minecraft:water')
MAX_RISE = 2      # front zone (street side of the house line): boundary walls/hedges only
BACK_RISE = 4     # back gardens: sheds / outbuildings up to ~4 blocks (cite the aerial)
MARGIN = 2        # footprint dilation (projection + raster error is ~1-2 blocks)


def _bid(state: str) -> str:
    """Namespaced block id without ``[blockstate]`` (same contract as ``site_kit.bid``).

    Kept local so ``guard()`` unit tests need no live ``site_kit`` / world.json.

    @param state: plan cell value (optional ``minecraft:``, optional blockstate)
    @returns: e.g. ``minecraft:red_concrete`` or ``minecraft:smooth_stone_slab``
    """
    head = state.split('[', 1)[0]
    return head if ':' in head else 'minecraft:' + head


def _roof_like(block_id: str) -> bool:
    """True if ``block_id`` is a roof/canopy solid that must not use BACK_RISE.

    @param block_id: namespaced id without blockstate (``_bid`` output)
    @returns: whether the block counts as building-like at any rise above MAX_RISE
    """
    return any(k in block_id for k in ROOF_LIKE)


def _projected(src: Path) -> Path:
    """Return a cached EPSG:27700 copy of one WGS84 GeoJSON clip."""
    CACHE.mkdir(parents=True, exist_ok=True)
    dst = CACHE / (src.parent.name + '__' + src.name)
    identity = dst.with_suffix('.source-sha256')
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    if not dst.exists() or not identity.exists() or identity.read_text() != digest:
        dst.unlink(missing_ok=True)
        subprocess.run(['ogr2ogr', '-f', 'GeoJSON', '-t_srs', 'EPSG:27700', str(dst), str(src)],
                       check=True, capture_output=True)
        identity.write_text(digest)
    return dst


def cached_geojson_paths(clips_root: Path | None = None) -> list[Path]:
    """Ensure every vector-map clip is projected into ``CACHE``; return all ``*.geojson`` there.

    Shared reader for ground/decor (and anything else that rasterises the OSGB
    cache). Reprojects missing or stale clips via ``_projected``. Returns every
    geojson in the cache so ``dir__name.geojson`` names from this module and
    legacy ``*-mp`` / ``*-ln`` / ``*-pt`` names both work.

    @param clips_root: optional clip tree (default: active world's vector_map root)
    @returns: sorted paths under ``CACHE``
    """
    root = clips_root if clips_root is not None else _resolve_vector_map_clips()
    if root.is_dir():
        for src in sorted(root.glob('*/*.geojson')):
            _projected(src)
    CACHE.mkdir(parents=True, exist_ok=True)
    return sorted(CACHE.glob('*.geojson'))


def _query_bbox(src: Path, layer: str = 'buildings') -> tuple[int, int, int, int] | None:
    """Read inclusive query bounds for one layer preset from filenames or legacy folder names.

    Custom GDAL-layer queries (e.g. multipolygons filtered to campus boundaries)
    do not establish that the layer was queried, even inside their bounding box.

    @param src: clip path
    @param layer: preset name that must appear in the clip name (``buildings``, ``landuse``)
    """
    if not re.search(rf'(?:^|\+){re.escape(layer)}(?:\+|-|$)', src.stem):
        return None
    for name in (src.stem, src.parent.name):
        match = re.search(r'(?:^|-)bbox-(-?\d+)-(-?\d+)-(-?\d+)-(-?\d+)$', name)
        if match:
            x0, z0, x1, z1 = map(int, match.groups())
            if x0 <= x1 and z0 <= z1:
                return x0, z0, x1, z1
        match = re.search(r'(?:^|-)r(\d+)m-at-(-?\d+)-(-?\d+)$', name)
        if match:
            radius, x, z = map(int, match.groups())
            return x - radius, z - radius, x + radius, z + radius
        match = re.search(r'(?:^|-)cell-(-?\d+)-(-?\d+)$', name)
        if match:
            # osm_extract's cell preset writes fixed 128-block clips.
            x, z = map(int, match.groups())
            return x, z, x + 127, z + 127
    return None  # Unknown provenance is blind, never inferred from returned buildings.


def clip_coverage(cell: tuple[int, int], *, root: Path | None = None,
                  extents: list | None = None) -> float:
    """Fraction of cell columns queried; read bounds without projecting feature data.

    @param cell: (tx, tz)
    @param root: optional vector-map clip tree
    @param extents: optional preloaded inclusive building-query bboxes
    @returns: queried fraction in [0, 1]
    """
    from cellmath import cell_bbox
    if extents is None:
        root = root if root is not None else _resolve_vector_map_clips()
        extents = [bbox for src in root.glob('*/*.geojson')
                   if (bbox := _query_bbox(src)) is not None]
    x0, z0, x1, z1 = cell_bbox(*cell, *cell)
    columns = set()
    for bx0, bz0, bx1, bz1 in extents:
        columns.update((x, z)
                       for x in range(max(x0, bx0), min(x1, bx1) + 1)
                       for z in range(max(z0, bz0), min(z1, bz1) + 1))
    return len(columns) / ((x1 - x0 + 1) * (z1 - z0 + 1))


class OSMIndex:
    """All buildings and named highways from the vector-map clip tree, in MC float coords."""

    def __init__(self, root: Path | None = None, *, read_only: bool = False):
        root = root or _resolve_vector_map_clips()
        from world_frame import osgb_to_mc
        mc = lambda c: osgb_to_mc(c[0], c[1])
        if read_only:
            from world_frame import wgs84_to_osgb
            mc = lambda c: osgb_to_mc(*wgs84_to_osgb(c[1], c[0]))
        self.buildings = []   # (rings[[(x,z)...]], props)
        self.streets = []     # (name, highway, [(x,z)...])
        self.street_props = []  # map properties of each self.streets entry (same order)
        self.areas = []       # (rings, props) of every non-building polygon (landuse/leisure/amenity/...)
        self.entrances = []   # (x, z, props) entrance=* Point features (#159)
        self.extents = []     # (x0, z0, x1, z1) per clip — where the map was actually checked
        self.layer_extents = {'landuse': []}  # per-layer checked extents from clip names (#253)
        seen = set()
        for src in sorted(root.glob('*/*.geojson')):
            bbox = _query_bbox(src)
            if bbox is not None and bbox not in self.extents:
                self.extents.append(bbox)
            for layer, ext in self.layer_extents.items():
                lb = _query_bbox(src, layer)
                if lb is not None and lb not in ext:
                    ext.append(lb)
            d = json.loads((src if read_only else _projected(src)).read_text())
            for f in d['features']:
                g, p = f['geometry'], f['properties']
                if not g:
                    continue
                key = (p.get('osm_id'), p.get('osm_way_id'), g['type'])
                if key in seen:
                    continue
                seen.add(key)
                if g['type'] in ('Polygon', 'MultiPolygon'):
                    polys = [g['coordinates']] if g['type'] == 'Polygon' else g['coordinates']
                    for poly in polys:
                        rings = [[mc(c) for c in r] for r in poly]
                        if p.get('building'):
                            self.buildings.append((rings, p))
                        else:
                            self.areas.append((rings, p))
                elif g['type'] == 'LineString' and p.get('highway'):
                    self.streets.append((p.get('name'), p['highway'], [mc(c) for c in g['coordinates']]))
                    self.street_props.append(p)
                elif g['type'] == 'Point':
                    tags = osm_tags(p)
                    if tags.get('entrance'):
                        x, z = mc(g['coordinates'][:2])
                        merged = dict(p)
                        merged['entrance'] = tags['entrance']
                        self.entrances.append((x, z, merged))
        self._cells = None

    # --- footprint raster ---------------------------------------------------
    def cells(self) -> dict:
        """{(x, z): building index} for every block whose centre is inside a footprint."""
        if self._cells is None:
            self._cells = {}
            for i, (rings, _p) in enumerate(self.buildings):
                xs = [x for x, _ in rings[0]]; zs = [z for _, z in rings[0]]
                gx, gz = np.meshgrid(np.arange(math.floor(min(xs)), math.ceil(max(xs)) + 1),
                                     np.arange(math.floor(min(zs)), math.ceil(max(zs)) + 1), indexing='ij')
                hit = _inside_grid(gx + 0.5, gz + 0.5, rings)
                ix, iz = np.nonzero(hit)                   # x-major, same order the nested loops visited
                for x, z in zip(gx[ix, iz].tolist(), gz[ix, iz].tolist()):
                    self._cells[(x, z)] = i
        return self._cells

    def near_footprint(self, x: int, z: int, margin: int = MARGIN) -> bool:
        c = self.cells()
        return any((x + dx, z + dz) in c for dx in range(-margin, margin + 1)
                   for dz in range(-margin, margin + 1))

    def covered(self, x: int, z: int) -> bool:
        """True if (x, z) lies inside a vector-map clip we actually have (else the check is blind)."""
        return any(x0 <= x <= x1 and z0 <= z <= z1 for x0, z0, x1, z1 in self.extents)

    def layer_covered(self, layer: str, x: int, z: int) -> bool:
        """True if (x, z) lies in a clip whose name says ``layer`` was queried (else ``not_checked``).

        @param layer: preset name tracked in ``layer_extents`` (``landuse``)
        """
        return any(x0 <= x <= x1 and z0 <= z <= z1 for x0, z0, x1, z1 in self.layer_extents.get(layer, ()))

    def clip_coverage(self, cell: tuple[int, int]) -> float:
        """Fraction of a cell's columns inside the union of queried clip bboxes.

        @param cell: (tx, tz)
        @returns: queried fraction in [0, 1]
        """
        return clip_coverage(cell, extents=self.extents)

    def setback(self, i: int) -> float:
        """Distance from building i's closest vertex to the nearest named street centreline."""
        if not hasattr(self, '_sb'):
            self._sb = {}
        if i not in self._sb:
            ds = [self.nearest_street(x, z) for x, z in self.buildings[i][0][0]]
            self._sb[i] = min((d[0] for d in ds if d), default=math.inf)
        return self._sb[i]

    def front_zone(self, x: int, z: int, reach: float = 30) -> bool:
        """True if (x, z) is between a street and the house fronts beside it (front garden / pavement)."""
        s = self.nearest_street(x + 0.5, z + 0.5)
        if not s:
            return False
        near = [i for i, (r, _p) in enumerate(self.buildings)
                if any(abs(vx - x) < reach and abs(vz - z) < reach for vx, vz in r[0])]
        return bool(near) and s[0] < min(self.setback(i) for i in near) - 0.5

    # --- streets --------------------------------------------------------------
    def nearest_street(self, x: float, z: float, named_only: bool = True):
        """(distance, name, highway, foot_point) of the closest highway centreline."""
        best = None
        for name, hw, pts in self.streets:
            if named_only and not name:
                continue
            for a, b in zip(pts, pts[1:]):
                d, foot = _seg_dist((x, z), a, b)
                if best is None or d < best[0]:
                    best = (d, name, hw, foot)
        return best


def _inside(x: float, z: float, rings) -> bool:
    """Even-odd point-in-polygon over outer ring + holes."""
    n = False
    for r in rings:
        for (x1, z1), (x2, z2) in zip(r, r[1:] + r[:1]):
            if (z1 > z) != (z2 > z) and x < (x2 - x1) * (z - z1) / (z2 - z1) + x1:
                n = not n
    return n


def _inside_grid(x, z, rings):
    """``_inside`` over numpy arrays of centres: the same even-odd test and float expression, so the same answer."""
    n = np.zeros(x.shape, dtype=bool)
    with np.errstate(divide='ignore', invalid='ignore'):
        for r in rings:
            for (x1, z1), (x2, z2) in zip(r, r[1:] + r[:1]):
                if z1 == z2:
                    continue                               # horizontal edge: (z1 > z) == (z2 > z), never crosses
                n ^= ((z1 > z) != (z2 > z)) & (x < (x2 - x1) * (z - z1) / (z2 - z1) + x1)
    return n


def _seg_dist(p, a, b):
    (px, pz), (ax, az), (bx, bz) = p, a, b
    dx, dz = bx - ax, bz - az
    t = 0 if dx == dz == 0 else max(0, min(1, ((px - ax) * dx + (pz - az) * dz) / (dx * dx + dz * dz)))
    foot = (ax + t * dx, az + t * dz)
    return math.dist(p, foot), foot


_HSTORE = re.compile(r'"((?:[^"\\]|\\.)*)"=>"((?:[^"\\]|\\.)*)"')


def osm_tags(p: dict) -> dict:
    """Flatten one ogr2ogr feature's properties: top-level columns plus the other_tags hstore.

    @param p: GeoJSON feature properties as written by osm_extract.py / ogr2ogr
    @returns: {key: value}; top-level columns win over duplicate other_tags keys
    """
    t = {k: v for k, v in p.items() if v not in (None, '') and k != 'other_tags'}
    for k, v in _HSTORE.findall(p.get('other_tags') or ''):
        t.setdefault(k.replace('\\"', '"'), v.replace('\\"', '"'))
    return t


def _addr(p) -> tuple[str, str]:
    tags = p.get('other_tags') or ''
    get = lambda k: (tags.split(f'"addr:{k}"=>"', 1)[1].split('"', 1)[0]
                     if f'"addr:{k}"=>"' in tags else '')
    return get('housenumber'), get('street')


# --- the guard used by site_kit.apply() ------------------------------------------
def guard(cells: dict, world=None, idx: OSMIndex | None = None) -> dict:
    """Find building-like blocks standing in front of / outside every vector-map footprint.

    @param cells: {(x, y, z): state}
    @param world: object with ``ground(x, z)`` for the ground reference (default: lidar composite world)
    @param idx: optional OSMIndex (tests inject a stub; default loads the clip tree)
    Front zone (street side of the house line): max MAX_RISE. Back gardens: BACK_RISE
    for non-roof-like blocks (sheds); concrete / slabs above MAX_RISE are always building-like.
    @returns: {'outside': {pos: [state, rise, zone, street, dist]}, 'blind': [pos...]} (blind = no map clip or no cached lidar)
    """
    idx = idx or OSMIndex()
    if world is None:
        import lidar_provider
        world = lidar_provider.composite_world()
    w = world
    ground = {}
    out, blind = {}, []
    for (x, y, z), st in cells.items():
        b = _bid(st)
        if b in AIRS or any(k in b for k in FURNITURE):
            continue
        covered = idx.covered(x, z)
        if covered and idx.near_footprint(x, z):
            continue
        if (x, z) not in ground:
            try:
                ground[(x, z)] = w.ground(x, z, top=120, bottom=0)
            except LookupError:       # lidar tiles for this cell are not cached: ground unknown
                blind.append((x, y, z))
                continue
        rise = y - ground[(x, z)]
        if rise <= MAX_RISE:
            continue          # kerbs, paving, low garden walls: fine anywhere
        if not covered:
            blind.append((x, y, z))
            continue
        front = idx.front_zone(x, z)
        # Shed exemption is for timber/brick outbuildings — not concrete or slab roofs
        # (those need outside_footprint_ok when they sit on open mapped ground).
        if not front and rise <= BACK_RISE and not _roof_like(b):
            continue          # back-garden shed / outbuilding height
        s = idx.nearest_street(x, z)
        out[(x, y, z)] = [st, rise, 'front' if front else 'back',
                          s[1] if s else None, round(s[0], 1) if s else None]
    return {'outside': out, 'blind': blind}


# --- CLI ------------------------------------------------------------------------
def frontage(args):
    from site_kit import World
    idx = OSMIndex()
    live = World()
    x0, z0, x1, z1 = map(int, args.bbox.split(','))
    rows = []
    for i, (rings, p) in enumerate(idx.buildings):
        cx = sum(x for x, _ in rings[0]) / len(rings[0]); cz = sum(z for _, z in rings[0]) / len(rings[0])
        if not (x0 <= cx <= x1 and z0 <= cz <= z1):
            continue
        num, street = _addr(p)
        if args.street and street != args.street:
            continue
        # the front is the footprint vertex closest to its own street (or the nearest named one)
        cands = [(idx.nearest_street(x, z), (x, z)) for x, z in rings[0]]
        if street:
            own = [(s, v) for s, v in ((_street_dist(idx, street, v), v) for _, v in cands) if s]
            cands = own or cands
        (d, sname, _hw, foot), front = min(cands, key=lambda c: c[0][0])
        # live block profile from the front wall back to the centreline
        n = max(1, int(math.ceil(d)))
        prof = []
        for k in range(1, n + 1):
            px = front[0] + (foot[0] - front[0]) * k / n; pz = front[1] + (foot[1] - front[1]) * k / n
            gx, gz = math.floor(px), math.floor(pz)
            y = live.ground(gx, gz, top=120, bottom=0)
            prof.append(live.block(gx, y, gz).split('[')[0][10:])
        rows.append((sname or '?', num or '-', round(d, 1), [round(front[0]), round(front[1])],
                     _runs(prof[::-1])))
    rows.sort(key=lambda r: (r[0], _numkey(r[1])))
    print('street | no. | front wall -> centreline (blocks) | front wall MC x,z | live surface centreline -> front')
    for r in rows:
        print(f'{r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]}')
    print('\nRead: setback ≫ (carriageway half-width + pavement) means FRONT GARDENS between '
          'pavement and house. Build the house front at "front wall MC x,z", never at the kerb.')


STREET_SURFACE = ('gray_terracotta', 'polished_diorite', 'smooth_stone', 'andesite', 'gravel')


def street_surface(world, x: int, z: int) -> bool:
    """True if the top surface block of column (x, z) is a road / pavement block.

    @param world: site_kit.World to read (the live save)
    @param x: MC block x
    @param z: MC block z
    @returns: whether the surface block id is in STREET_SURFACE
    """
    y = world.ground(x, z, top=120, bottom=0)
    return (world.block(x, y, z) or '').split('[')[0][10:] in STREET_SURFACE


def grow_plots(seeds: dict, passable, reach: float = 45) -> dict:
    """Grow plots outwards from footprint cells: an approximate Euclidean Voronoi.

    Dijkstra keyed on straight-line distance to the seeding footprint cell, so plot sides
    run perpendicular to the street like real garden strips.

    @param seeds: {(x, z): owner} footprint cells that start growing at distance 0
    @param passable: callable(x, z) -> bool; False stops growth (street surface, other footprints)
    @param reach: max straight-line distance a plot may grow from its seeding cell
    @returns: {(x, z): owner} for every seed cell and every cell grown over
    """
    import heapq
    owner, heap = {}, []
    for (x, z), i in seeds.items():
        heapq.heappush(heap, (0.0, x, z, x, z, i))
    while heap:
        d, x, z, sx, sz, i = heapq.heappop(heap)
        if (x, z) in owner:
            continue
        owner[(x, z)] = i
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                nx, nz = x + dx, z + dz
                if (dx or dz) and (nx, nz) not in owner and passable(nx, nz):
                    nd = math.hypot(nx - sx, nz - sz)
                    if nd <= reach:
                        heapq.heappush(heap, (nd, nx, nz, sx, sz, i))
    return owner


def plots(args):
    """First-guess property plots: grow every footprint outwards over garden ground
    (nearest house by straight-line distance) until it meets the street surface or a neighbour.
    Party walls -> plot sides, road -> front edge, meeting neighbour's garden -> back fence.
    A starting point only: move fences to what the aerial shows."""
    from site_kit import World, OUT
    idx = OSMIndex()
    live = World()
    x0, z0, x1, z1 = map(int, args.bbox.split(','))
    fp = idx.cells()
    seeds = {(x, z): i for (x, z), i in fp.items()
             if x0 - 60 <= x <= x1 + 60 and z0 - 60 <= z <= z1 + 60}
    surf = {}

    def passable(x, z):
        if (x, z) in fp:
            return False
        if (x, z) not in surf:
            surf[(x, z)] = street_surface(live, x, z)
        return not surf[(x, z)]
    owner = grow_plots(seeds, passable, args.reach)
    houses = {}
    for (x, z), i in owner.items():
        houses.setdefault(i, []).append((x, z))
    rep = {}
    for i, cs in houses.items():
        rings, p = idx.buildings[i]
        cx = sum(x for x, _ in rings[0]) / len(rings[0]); cz = sum(z for _, z in rings[0]) / len(rings[0])
        if not (x0 <= cx <= x1 and z0 <= cz <= z1):
            continue
        num, st = _addr(p)
        house = [c for c in cs if c in fp]
        front = [c for c in cs if c not in fp and idx.front_zone(*c)]
        rep[f'{st or "?"} {num or "#" + str(i)}'] = {
            'map_ref': p.get('osm_id') or p.get('osm_way_id'),
            'house_cells': len(house), 'front_garden_cells': len(front),
            'back_garden_cells': len(cs) - len(house) - len(front),
            'bbox': [min(x for x, _ in cs), min(z for _, z in cs), max(x for x, _ in cs), max(z for _, z in cs)],
            'cells': sorted([x, z] for x, z in cs)}
    out = OUT / f'plots-{x0}_{z0}_{x1}_{z1}.json'
    out.write_text(json.dumps(rep))
    for k, v in sorted(rep.items(), key=lambda kv: (kv[0].rsplit(' ', 1)[0], _numkey(kv[0].rsplit(' ', 1)[-1]))):
        print(f'{k}: house {v["house_cells"]}, front garden {v["front_garden_cells"]}, '
              f'back garden {v["back_garden_cells"]}, plot bbox {v["bbox"]}')
    print('cells per plot:', out)
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
        img = np.ones((z1 - z0 + 1, x1 - x0 + 1, 3))
        rng = np.random.default_rng(1)
        for k, v in rep.items():
            c = rng.uniform(0.35, 0.95, 3)
            for x, z in v['cells']:
                if x0 <= x <= x1 and z0 <= z <= z1:
                    img[z - z0, x - x0] = c * (0.6 if (x, z) in fp else 1)
        fig, ax = plt.subplots(figsize=(12, 12 * img.shape[0] / img.shape[1] + 1))
        ax.imshow(img, extent=(x0 - .5, x1 + .5, z1 + .5, z0 - .5), interpolation='nearest')
        for k, v in rep.items():
            b = v['bbox']
            ax.text((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, k.rsplit(' ', 1)[-1], fontsize=7, ha='center')
        ax.set_title('first-guess plots (dark = vector-map house footprint)')
        png = out.with_suffix('.png')
        plt.savefig(png, dpi=90)
        print('preview:', png)
    except ImportError:
        pass


def _street_dist(idx, name, v):
    best = None
    for n, hw, pts in idx.streets:
        if n != name:
            continue
        for a, b in zip(pts, pts[1:]):
            d, foot = _seg_dist(v, a, b)
            if best is None or d < best[0]:
                best = (d, n, hw, foot)
    return best


def _numkey(s):
    digits = ''.join(ch for ch in s if ch.isdigit())
    return (int(digits) if digits else 1 << 30, s)


def _runs(seq):
    out = []
    for s in seq:
        if out and out[-1][0] == s:
            out[-1][1] += 1
        else:
            out.append([s, 1])
    return ' '.join(f'{s}×{n}' for s, n in out)


def check(args):
    from cell_registry import latest_cells, view_registry
    if args.all:
        merged = {q: v for q, v in latest_cells().items() if v[1].split('[')[0] not in ('minecraft:air', 'minecraft:light')}
        cells = {q: st for q, (_n, st) in merged.items()}
        owner = {q: n for q, (n, _st) in merged.items()}
    else:
        cells = view_registry().recorded(args.build)
        if cells is None:
            raise SystemExit(f'no recorded build {args.build!r} in the cell journal')
        owner = {q: args.build for q in cells}
    rep = guard(cells)
    by = {}
    for q, v in rep['outside'].items():
        by.setdefault(owner[q], []).append((q, v))
    for n, hits in sorted(by.items()):
        print(f'{n}: {len(hits)} building-like cells outside vector-map footprints, e.g. {hits[:3]}')
    if rep['blind']:
        print(f'{len(rep["blind"])} cells outside every map clip (run osm_extract.py for the area), '
              f'e.g. {rep["blind"][:3]}')
    if not by and not rep['blind']:
        print('OK: no building-like cells outside vector-map footprints')
    return 1 if by else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    f = sub.add_parser('frontage', help='per-house street, front line and garden profile')
    f.add_argument('--bbox', required=True, help='x0,z0,x1,z1 (MC) — building centroids inside')
    f.add_argument('--street', help='only houses whose addr:street matches')
    pl = sub.add_parser('plots', help='first-guess plot per house (house + front + back garden)')
    pl.add_argument('--bbox', required=True, help='x0,z0,x1,z1 (MC) — house centroids inside')
    pl.add_argument('--reach', type=int, default=45, help='max garden depth grown from a house')
    c = sub.add_parser('check', help='building-like cells outside vector-map footprints')
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument('--build')
    g.add_argument('--all', action='store_true')
    a = ap.parse_args()
    if a.cmd == 'plots':
        return plots(a) or 0
    return frontage(a) or 0 if a.cmd == 'frontage' else check(a)


if __name__ == '__main__':
    sys.exit(main())

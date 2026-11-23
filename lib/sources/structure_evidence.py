"""Bridge, footbridge, culvert and tunnel evidence with measured deck heights.

Draft follow-up A of ``docs/plans/bridges-and-tunnels.md``. Turns mapped ways
(any carrier: road, path, rail or waterway) and recorded lidar height columns
into one :class:`Structure` record per mapped bridge or tunnel. Nothing here
places blocks, reads the live world or makes a design choice.

**Inputs.** ``ways`` are ``{'osm_id', 'tags', 'points'}`` entries already in
Minecraft x, z (:func:`read_map_ways` reads a GeoJSON extract). ``ground`` is the
planner's lidar mosaic: ``x0``, ``z0`` and arrays indexed [x, z] for ``terrain``
(DTM block Y), ``h`` (surface block Y), ``kind`` (4 = composite-lidar structure)
and optional ``have``. A way tagged ``bridge`` or ``tunnel`` is a structure.

**Deck height is evidence, never a guess.** Only lidar ``kind`` 4 columns that
stand at least :data:`MIN_DECK_RISE` above the DTM count. Tree and roof columns
over a deck (kind 1 to 3) are never read as a deck, and the terrain beneath is
never read as one. Each record carries a :class:`DeckHeight` whose ``status`` is

* ``measured``: at least :data:`MEASURED_MIN_COLUMNS` structure columns whose
  heights agree within :data:`MEASURED_SPREAD` blocks. ``top`` is their median.
* ``inferred``: some structure columns, but too few or in conflict. ``top`` is
  the lowest of them (the safe value to clear under) and ``uncertainty`` is the
  spread. The reason says which.
* ``unknown``: no usable structure column (too few columns, no lidar, a deck
  that only repeats a higher deck, or a tunnel, whose bore lidar cannot see).
  ``top`` and ``underside`` are None.

``underside`` is the Y of the lowest deck block, the lowest structure column
less :data:`DECK_THICKNESS` blocks plus one. Lidar sees tops, so the thickness
is an assumption and the record says so.

**Layers.** A bridge's layer defaults to 1 and a tunnel's to -1 when untagged
(``layer_tagged`` says which). A bridge crosses a way whose layer is lower and a
tunnel one whose layer is higher; equal layers are a junction, not a crossing.
Where two decks share columns, the lower-layer bridge ignores the shared
columns, because lidar there shows the upper deck.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path

import cellmath
import world_frame as wf
from osm_footprints import osm_tags
from railway_evidence import _deck_columns, _geom_lines, _polyline_hits, _seg_intersect

MEASURED = 'measured'
INFERRED = 'inferred'
UNKNOWN = 'unknown'

# A lidar structure column must stand this far above the DTM to be a deck.
MIN_DECK_RISE = 2
MEASURED_MIN_COLUMNS = 8
INFERRED_MIN_COLUMNS = 2
# Structure columns of one deck may differ by this many blocks and still agree.
MEASURED_SPREAD = 2
# Lidar sees only deck tops; this is the assumed beam or slab depth under it.
DECK_THICKNESS = 1
# Half width of a deck column corridor for carriers the road rule does not cover.
RAIL_HALF_WIDTH = 2.0
WATER_HALF_WIDTH = 2.0

DEFAULT_BRIDGE_LAYER = 1
DEFAULT_TUNNEL_LAYER = -1
FOOT_HIGHWAYS = ('footway', 'path', 'cycleway', 'pedestrian', 'steps', 'bridleway', 'corridor')


@dataclass(frozen=True)
class DeckHeight:
    """Measured deck height with its uncertainty.

    @param status: ``measured``, ``inferred`` or ``unknown``
    @param top: top block Y of the deck, or None when unknown
    @param underside: Y of the lowest deck block, or None when unknown
    @param uncertainty: spread in blocks of the structure columns used, or None
    @param columns: structure columns used
    @param reason: why this status, in words
    """

    status: str
    top: int | None
    underside: int | None
    uncertainty: int | None
    columns: int
    reason: str


@dataclass(frozen=True)
class Crossed:
    """A way a structure passes over (bridge) or under (tunnel).

    @param carrier: ``road``, ``path``, ``rail`` or ``water``
    @param osm_id: way id of the crossed way
    @param layer: layer of the crossed way
    @param at: crossing point (x, z), which may sit between blocks
    @param structure_id: id of the crossed way's own structure record, or None
    """

    carrier: str
    osm_id: str
    layer: int
    at: tuple[float, float]
    structure_id: str | None


@dataclass(frozen=True)
class Structure:
    """One mapped bridge or tunnel.

    @param id: stable id, ``structure-<osm_id>``
    @param osm_id: way id (``<id>#<n>`` for one part of a multi-part geometry)
    @param kind: ``bridge`` or ``tunnel``
    @param form: ``road_bridge``, ``footbridge``, ``rail_bridge``, ``waterway_bridge``,
        ``road_tunnel``, ``footway_tunnel``, ``rail_tunnel``, ``culvert`` or ``waterway_tunnel``
    @param carrier: what the structure carries: ``road``, ``path``, ``rail`` or ``water``
    @param carrier_tag: the ``highway``, ``railway`` or ``waterway`` value
    @param layer: ``layer`` tag, or the kind's default
    @param layer_tagged: whether the layer came from the map
    @param style: ``bridge`` or ``tunnel`` tag value (``yes``, ``viaduct``, ``culvert`` ...)
    @param points: centre line, Minecraft x, z
    @param start: first point (a bridge end or tunnel portal)
    @param end: last point (a bridge end or tunnel portal)
    @param deck: columns the structure covers, as the ground layer builds them
    @param cells: coverage cells the structure touches, sorted
    @param crosses: ways crossed, in way-id order
    @param height: measured deck height and its uncertainty
    @param refs: evidence references (map ways and lidar columns)
    """

    id: str
    osm_id: str
    kind: str
    form: str
    carrier: str
    carrier_tag: str
    layer: int
    layer_tagged: bool
    style: str
    points: tuple[tuple[float, float], ...]
    start: tuple[float, float]
    end: tuple[float, float]
    deck: frozenset[tuple[int, int]]
    cells: tuple[tuple[int, int], ...]
    crosses: tuple[Crossed, ...]
    height: DeckHeight
    refs: tuple[str, ...]


def read_map_ways(path: Path, frame: wf.WorldFrame) -> tuple[dict, ...]:
    """Read highway, railway and waterway lines from an OSM GeoJSON extract.

    @param path: GeoJSON FeatureCollection from the vector-map extract
    @param frame: world frame for lat/lon to Minecraft
    @returns: ``{'osm_id', 'tags', 'points'}`` entries; a multi-part geometry
        becomes ``<id>#<n>`` parts
    @raises ValueError: when ``path`` is not a FeatureCollection
    """
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or data.get('type') != 'FeatureCollection':
        raise ValueError(f'{path} is not a GeoJSON FeatureCollection')
    out = []
    for feat in data.get('features') or ():
        tags = osm_tags(feat.get('properties') or {})
        if not any(tags.get(k) for k in ('highway', 'railway', 'waterway')):
            continue
        osm_id = str(tags.get('osm_id') or tags.get('osm_way_id') or '')
        parts = _geom_lines(feat.get('geometry') or {})
        for i, part in enumerate(parts):
            points = [wf.wgs84_to_mc(c[1], c[0], frame) for c in part if len(c) >= 2]
            if len(points) >= 2:
                out.append({'osm_id': osm_id if len(parts) == 1 else f'{osm_id}#{i}',
                            'tags': tags, 'points': points})
    return tuple(out)


def build_structures(
    ways: list[dict] | tuple[dict, ...],
    ground: dict | None = None,
    *,
    cell_size: int | None = None,
) -> tuple[Structure, ...]:
    """One record per mapped bridge or tunnel, with measured deck heights.

    @param ways: mapped ways in Minecraft coordinates; the structures and every
        route or water they may cross
    @param ground: lidar mosaic described in the module docstring, or None
        (every height is then unknown)
    @param cell_size: coverage-grid size, or cellmath's configured size
    @returns: structures sorted by way id
    """
    items = []
    for way in ways:
        item = _classify(way)
        if item is not None:
            items.append(item)
    items.sort(key=lambda s: _id_key(s['osm_id']))
    for item in items:
        item['deck'] = _columns(item)
    # A way's layer decides what it crosses, structure or not.
    layers = {str(w.get('osm_id') or ''): _layer(w.get('tags') or {}, None)[0] for w in ways}
    ids = {item['osm_id']: 'structure-' + item['osm_id'] for item in items}
    built = []
    for item in items:
        crosses = _crossings(item, ways, layers, ids)
        masked = set()
        if item['kind'] == 'bridge':
            for other in items:
                if other is not item and other['kind'] == 'bridge' and other['layer'] > item['layer']:
                    masked |= other['deck'] & item['deck']
        height = _height(item, ground, masked)
        deck = item['deck']
        cells = sorted({cellmath.cell_of(x, z, size=cell_size) for x, z in deck})
        built.append(Structure(
            ids[item['osm_id']], item['osm_id'], item['kind'], item['form'], item['carrier'],
            item['carrier_tag'], item['layer'], item['layer_tagged'], item['style'],
            tuple(item['points']), tuple(item['points'][0]), tuple(item['points'][-1]),
            deck, tuple(cells), crosses, height, _refs(item, crosses, height)))
    return tuple(built)


def by_osm_id(structures) -> dict[str, Structure]:
    """Index structure records by their way id (the key railway overhead spans carry)."""
    return {s.osm_id: s for s in structures}


def to_dict(structure: Structure) -> dict:
    """JSON-able form of one record, deck columns sorted."""
    return {
        'id': structure.id, 'osm_id': structure.osm_id, 'kind': structure.kind,
        'form': structure.form, 'carrier': structure.carrier, 'carrier_tag': structure.carrier_tag,
        'layer': structure.layer, 'layer_tagged': structure.layer_tagged, 'style': structure.style,
        'points': [list(p) for p in structure.points],
        'start': list(structure.start), 'end': list(structure.end),
        'deck': [list(p) for p in sorted(structure.deck)],
        'cells': [list(c) for c in structure.cells],
        'crosses': [{'carrier': c.carrier, 'osm_id': c.osm_id, 'layer': c.layer,
                     'at': list(c.at), 'structure_id': c.structure_id} for c in structure.crosses],
        'height': {'status': structure.height.status, 'top': structure.height.top,
                   'underside': structure.height.underside,
                   'uncertainty': structure.height.uncertainty,
                   'columns': structure.height.columns, 'reason': structure.height.reason},
        'refs': list(structure.refs),
    }


def _tag_set(tags: dict, key: str) -> str | None:
    """A bridge or tunnel tag value, or None when absent or ``no``."""
    value = tags.get(key)
    return None if value in (None, '', 'no') else str(value)


def _carrier(tags: dict) -> tuple[str, str] | None:
    """What a way carries, as (class, tag value), or None for an unrelated way."""
    if tags.get('highway'):
        return ('path' if tags['highway'] in FOOT_HIGHWAYS else 'road'), str(tags['highway'])
    if tags.get('railway'):
        return 'rail', str(tags['railway'])
    if tags.get('waterway'):
        return 'water', str(tags['waterway'])
    return None


def _layer(tags: dict, kind: str | None) -> tuple[int, bool]:
    """Layer and whether the map gave it. Untagged or unreadable gets the kind's default."""
    default = {'bridge': DEFAULT_BRIDGE_LAYER, 'tunnel': DEFAULT_TUNNEL_LAYER}.get(kind, 0)
    try:
        return int(str(tags.get('layer')).strip()), True
    except ValueError:
        return default, False


def _classify(way: dict) -> dict | None:
    """The working record for a mapped bridge or tunnel way, else None."""
    tags = way.get('tags') or {}
    points = [tuple(p) for p in way.get('points') or ()]
    carrier = _carrier(tags)
    if carrier is None or len(points) < 2:
        return None
    bridge, tunnel = _tag_set(tags, 'bridge'), _tag_set(tags, 'tunnel')
    if bridge is None and tunnel is None:
        return None
    kind = 'bridge' if bridge is not None else 'tunnel'
    style = bridge if kind == 'bridge' else tunnel
    cls, tag = carrier
    if kind == 'bridge':
        form = {'road': 'road_bridge', 'path': 'footbridge', 'rail': 'rail_bridge',
                'water': 'waterway_bridge'}[cls]
    elif style == 'culvert':
        form = 'culvert'
    else:
        form = {'road': 'road_tunnel', 'path': 'footway_tunnel', 'rail': 'rail_tunnel',
                'water': 'waterway_tunnel'}[cls]
    layer, tagged = _layer(tags, kind)
    return {'osm_id': str(way.get('osm_id') or ''), 'tags': tags, 'points': points, 'kind': kind,
            'form': form, 'carrier': cls, 'carrier_tag': tag, 'layer': layer,
            'layer_tagged': tagged, 'style': style}


def _columns(item: dict) -> frozenset[tuple[int, int]]:
    """Columns a structure covers: the overhead-deck rule for roads and paths."""
    if item['carrier'] in ('road', 'path'):
        return _deck_columns({'highway': item['carrier_tag'], **item['tags']}, item['points'])
    from ground_sources import line_cells
    half = RAIL_HALF_WIDTH if item['carrier'] == 'rail' else WATER_HALF_WIDTH
    return frozenset(line_cells(item['points'], half))


def _crossings(item: dict, ways, layers: dict, ids: dict) -> tuple[Crossed, ...]:
    """Ways this structure passes over (bridge) or under (tunnel), by layer."""
    found = []
    for way in ways:
        osm_id = str(way.get('osm_id') or '')
        tags = way.get('tags') or {}
        carrier = _carrier(tags)
        if osm_id == item['osm_id'] or carrier is None:
            continue
        other = layers[osm_id]
        if item['kind'] == 'bridge' and not other < item['layer']:
            continue
        if item['kind'] == 'tunnel' and not other > item['layer']:
            continue
        pts = [tuple(p) for p in way.get('points') or ()]
        at = _first_crossing(item['points'], pts)
        if at is not None:
            found.append(Crossed(carrier[0], osm_id, other, at, ids.get(osm_id)))
    found.sort(key=lambda c: (_id_key(c.osm_id), c.at))
    return tuple(found)


def _first_crossing(a, b) -> tuple[float, float] | None:
    """Where two centre lines first meet along ``a``, or None."""
    for p, q in zip(a, a[1:]):
        for r, s in zip(b, b[1:]):
            got = _seg_intersect(p, q, r, s)
            if got is not None:
                return got[0]
    return None


def _height(item: dict, ground: dict | None, masked: set) -> DeckHeight:
    """Deck height from the lidar structure columns under the deck."""
    if item['kind'] == 'tunnel':
        return DeckHeight(UNKNOWN, None, None, None, 0,
                          'tunnel: lidar sees the ground above the bore, not the bore')
    if ground is None:
        return DeckHeight(UNKNOWN, None, None, None, 0, 'no lidar height columns supplied')
    seen, tops = 0, []
    for p in sorted(item['deck'] - masked):
        got = _column(ground, p)
        if got is None:
            continue
        seen += 1
        terrain, h, kind = got
        if kind == 4 and h - terrain >= MIN_DECK_RISE:
            tops.append(h)
    shared = len(item['deck'] & masked)
    note = f'; {shared} columns shared with a higher deck were ignored' if shared else ''
    if len(tops) < INFERRED_MIN_COLUMNS:
        return DeckHeight(UNKNOWN, None, None, None, len(tops),
                          f'{len(tops)} lidar structure columns under {seen} deck columns '
                          f'with lidar{note}')
    lowest, spread = min(tops), max(tops) - min(tops)
    underside = lowest - DECK_THICKNESS + 1
    if len(tops) >= MEASURED_MIN_COLUMNS and spread <= MEASURED_SPREAD:
        return DeckHeight(MEASURED, int(statistics.median_low(tops)), underside, spread, len(tops),
                          f'{len(tops)} lidar structure columns agree within {spread} blocks; '
                          f'underside assumes a {DECK_THICKNESS}-block deck{note}')
    why = (f'only {len(tops)} lidar structure columns' if spread <= MEASURED_SPREAD
           else f'lidar structure columns disagree by {spread} blocks')
    return DeckHeight(INFERRED, lowest, underside, spread, len(tops),
                      f'{why}; the lowest is used; underside assumes a '
                      f'{DECK_THICKNESS}-block deck{note}')


def _column(ground: dict, p) -> tuple[int, int, int] | None:
    """(terrain, h, kind) at a column, or None without lidar there."""
    i, j = p[0] - ground['x0'], p[1] - ground['z0']
    terrain = ground['terrain']
    if not (0 <= i < len(terrain) and 0 <= j < len(terrain[i])):
        return None
    have = ground.get('have')
    if have is not None and not have[i][j]:
        return None
    return int(terrain[i][j]), int(ground['h'][i][j]), int(ground['kind'][i][j])


def _refs(item: dict, crosses, height: DeckHeight) -> tuple[str, ...]:
    """Evidence references: the map way, crossed ways and the lidar columns."""
    refs = [f"osm:way/{item['osm_id']}"]
    refs += [f'osm:way/{c.osm_id}' for c in crosses]
    if item['kind'] == 'bridge' and height.columns:
        refs.append(f'lidar:structure-columns/{height.columns}')
    return tuple(refs)


def _id_key(osm_id: str) -> tuple:
    """Numeric way ids in number order, anything else after them."""
    base = str(osm_id).split('#')[0]
    return (not base.isdigit(), int(base) if base.isdigit() else 0, str(osm_id))

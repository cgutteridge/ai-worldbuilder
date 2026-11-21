#!/usr/bin/env python3
"""Optional road-name labels: floating text_display entities after decor.

Not an Element and not part of a cell's definition of done. Labels are entities,
so this pass writes no blocks and no mask. Undo is a tag kill
(``kill @e[tag=wb_label,tag=wb_cell_<tx>_<tz>]``).

  python3 scripts/road_labels.py --cell 48,-44
  SITE_OWNER=<owner> python3 scripts/road_labels.py --cell 48,-44 --apply
  SITE_OWNER=<owner> python3 scripts/road_labels.py --cell 48,-44 --undo

See docs/specs/road-name-labels.md.
"""
from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import cellmath

TAG = 'wb_label'
LABEL_ABOVE = 3
SPACING = 80.0
MIN_SPACING = 60.0
MAX_GAP = 100.0
SLIDE_LIMIT = 20.0
VIEW_RANGE = 4.0
SCALE = 2.0
JUNCTION_REACH = 6.0
JUNCTION_OFFSET = 8.0
JUNCTION_CLUSTER = 24.0
SAMPLE_STEP = 4.0
BRIDGE_REACH = 6.0
ENTITY_TYPE = 'minecraft:text_display'
DIMENSION = 'minecraft:overworld'
# How far outside the asked-for cells still affects spacing (min gap plus a slide).
WINDOW_PAD = MIN_SPACING + SLIDE_LIMIT

_AIR = frozenset({'air', 'cave_air', 'void_air'})

GroundY = Callable[[int, int], int | None]
BlockAt = Callable[[int, int, int], str | None]
BridgeAt = Callable[[int, int], bool]


@dataclass(frozen=True)
class NamedWay:
    """One mapped highway polyline in Minecraft XZ.

    @param way_id: stable map id, or a coordinate fallback
    @param name: ``name`` tag; blank means the way is not labelled
    @param points: centreline ``(x, z)`` in Minecraft blocks
    @param bridge: raised or bridge deck (labels must not sit on it)
    @param tunnel: underground; the way is not labelled
    """

    way_id: str
    name: str
    points: tuple[tuple[float, float], ...]
    bridge: bool = False
    tunnel: bool = False


@dataclass(frozen=True)
class Label:
    """One road-name entity to summon.

    @param way_id: source way
    @param name: text to show
    @param x: Minecraft X (centreline, possibly slid)
    @param z: Minecraft Z
    @param y: entity Y (lidar ground + 3)
    @param cell: cell that contains the landed column
    @param slid: True when the clear-air search moved off the first choice
    @param junction: True when this label marks where two different names meet
    """

    way_id: str
    name: str
    x: float
    z: float
    y: int
    cell: tuple[int, int]
    slid: bool
    junction: bool


@dataclass(frozen=True)
class Skip:
    """A label that was considered and not placed.

    @param way_id: source way
    @param name: road name
    @param reason: ``tunnel``, ``no clear air``, or ``too close to another label``
    """

    way_id: str
    name: str
    reason: str


@dataclass(frozen=True)
class LabelPlan:
    """Labels to summon, and the ones dropped for a stated reason.

    @param labels: accepted labels (only those in ``cells`` when a cell filter was set)
    @param skipped: drops whose anchor sat in the asked-for cells
    """

    labels: tuple[Label, ...]
    skipped: tuple[Skip, ...]


@dataclass(frozen=True)
class _Cand:
    """An anchor on a way, before the clear-air slide."""

    way_id: str
    name: str
    x: float
    z: float
    dist: float
    junction: bool
    way_index: int


def cell_tag(cell: tuple[int, int]) -> str:
    """Scoreboard tag for labels that landed in ``cell``.

    @param cell: ``(tx, tz)``
    @returns: ``wb_cell_<tx>_<tz>`` (a negative tz keeps the minus)
    """
    return f'wb_cell_{cell[0]}_{cell[1]}'


def snbt_string(text: str) -> str:
    """Quote ``text`` as an SNBT string.

    @param text: raw road name
    @returns: double-quoted SNBT, with backslash and quote escaped
    """
    parts = ['"']
    for ch in text:
        if ch in '\\"':
            parts.append('\\')
            parts.append(ch)
        elif ch == '\n':
            parts.append(' ')
        else:
            parts.append(ch)
    parts.append('"')
    return ''.join(parts)


def label_snbt(name: str, cell: tuple[int, int]) -> str:
    """SNBT for one road-name ``text_display`` (1.21.5 text compound, not JSON).

    @param name: road name
    @param cell: cell tag source
    @returns: summon NBT, including billboard, opacity, scale, and both tags
    """
    scale = f'{SCALE:.1f}f'
    return (
        '{text:{text:' + snbt_string(name) + ',color:white},'
        'billboard:center,background:0,text_opacity:127b,'
        f'view_range:{VIEW_RANGE:.1f}f,'
        'transformation:{translation:[0.0f,0.0f,0.0f],'
        f'scale:[{scale},{scale},{scale}],'
        'left_rotation:[0.0f,0.0f,0.0f,1.0f],right_rotation:[0.0f,0.0f,0.0f,1.0f]},'
        f'Tags:[{snbt_string(TAG)},{snbt_string(cell_tag(cell))}]}}'
    )


def kill_command(cell: tuple[int, int] | None = None) -> str:
    """Vanilla kill for road-name labels, optionally limited to one cell.

    @param cell: when set, only that cell's tag; otherwise every ``wb_label``
    @returns: command body (no leading slash)
    """
    if cell is None:
        return f'kill @e[tag={TAG}]'
    return f'kill @e[tag={TAG},tag={cell_tag(cell)}]'


def summon_args(label: Label) -> dict[str, Any]:
    """``entity_summon`` arguments for one label.

    @param label: planned label
    @returns: dimension, entity type, position, and SNBT
    """
    return {
        'dimension': DIMENSION,
        'entity_type': ENTITY_TYPE,
        'position': {'x': label.x, 'y': float(label.y), 'z': label.z},
        'nbt': label_snbt(label.name, label.cell),
    }


def ways_from_map(
    rows: Sequence[tuple[Mapping[str, Any], Sequence[Sequence[float]]]],
) -> tuple[NamedWay, ...]:
    """Turn vector-map highway rows into ways.

    @param rows: ``(properties, [(x, z), ...])`` as stored on a vector-map clip
    @returns: one way per map id (the longer polyline wins if a clip repeated it)
    """
    from osm_footprints import osm_tags

    best: dict[str, NamedWay] = {}
    for props, pts in rows:
        tags = osm_tags(dict(props))
        points = tuple((float(p[0]), float(p[1])) for p in pts)
        way_id = str(tags.get('osm_id') or tags.get('osm_way_id') or '')
        if not way_id:
            way_id = 'xy:' + ','.join(f'{x:.1f},{z:.1f}' for x, z in points[:2])
        layer = str(tags.get('layer') or '0')
        negative = _negative_layer(layer)
        tunnel = tags.get('tunnel') == 'yes' or negative
        bridge = (not tunnel) and (
            tags.get('bridge') in ('yes', 'viaduct') or (layer not in ('0', '') and not negative)
        )
        way = NamedWay(
            way_id, str(tags.get('name') or '').strip(), points, bridge=bridge, tunnel=tunnel,
        )
        prev = best.get(way_id)
        if prev is None or len(way.points) > len(prev.points):
            best[way_id] = way
    return tuple(best[key] for key in sorted(best))


def bridge_predicate(
    ways: Sequence[NamedWay], reach: float = BRIDGE_REACH,
) -> BridgeAt:
    """True when a column is close to a bridge or raised centreline.

    @param ways: ways from :func:`ways_from_map` (unnamed bridges still count)
    @param reach: blocks from the centreline that count as under the deck
    @returns: ``bridge_at(x, z)`` for integer columns
    """
    spans = [
        (a, b)
        for way in ways if way.bridge
        for a, b in zip(way.points, way.points[1:])
    ]

    def bridge_at(x: int, z: int) -> bool:
        px, pz = x + 0.5, z + 0.5
        return any(_point_seg(px, pz, a, b) <= reach for a, b in spans)

    return bridge_at


def plan_labels(
    ways: Sequence[NamedWay],
    *,
    ground_y: GroundY,
    block_at: BlockAt,
    bridge_at: BridgeAt,
    cells: frozenset[tuple[int, int]] | None = None,
) -> LabelPlan:
    """Choose road-name labels.

    A short named way gets one label. A long way gets labels about every 80 blocks,
    at least 60 apart, including across cell edges. Two different names may both
    keep a label where they meet. The spot must be clear air, not under a bridge
    and not in a tree; otherwise the label slides up to 20 blocks along the way.

    @param ways: named and unnamed highways (unnamed and tunnels are not labelled)
    @param ground_y: lidar surface Y, or None where it is missing
    @param block_at: live block id at a column (None counts as not clear)
    @param bridge_at: True on columns under a mapped bridge or raised way
    @param cells: when set, only labels that land in these cells are returned;
        spacing still sees anchors within :data:`WINDOW_PAD` of them
    @returns: labels and skips
    """
    skipped: list[Skip] = []
    usable: list[NamedWay] = []
    for way in ways:
        if way.tunnel and way.name.strip() and _report_way(way, cells):
            skipped.append(Skip(way.way_id, way.name.strip(), 'tunnel'))
            continue
        if not way.name.strip() or way.tunnel or len(way.points) < 2:
            continue
        usable.append(way)

    metrics = [_metrics(way.points) for way in usable]
    junctions = _junction_distances(usable, metrics)
    candidates: list[_Cand] = []
    for index, way in enumerate(usable):
        _seg, total = metrics[index]
        if total < 1.0:
            continue
        for dist, is_junction in _way_anchors(total, junctions[index]):
            x, z = _at(way.points, _seg, dist)
            candidates.append(_Cand(
                way.way_id, way.name.strip(), x, z, dist, is_junction, index,
            ))

    candidates.sort(key=lambda c: (0 if c.junction else 1, c.name, c.way_id, c.dist))
    placed: list[Label] = []
    for cand in candidates:
        if cells is not None and not _in_window(cand.x, cand.z, cells, WINDOW_PAD):
            continue
        way = usable[cand.way_index]
        seg, total = metrics[cand.way_index]
        found = _slide(way, cand.dist, seg, total, ground_y, block_at, bridge_at)
        anchor_cell = cellmath.cell_of(math.floor(cand.x), math.floor(cand.z))
        report = cells is None or anchor_cell in cells
        if found is None:
            if report:
                skipped.append(Skip(cand.way_id, cand.name, 'no clear air'))
            continue
        x, z, y, slid = found
        if any(_too_close(x, z, cand.name, cand.junction, other) for other in placed):
            if report:
                skipped.append(Skip(cand.way_id, cand.name, 'too close to another label'))
            continue
        ix, iz = math.floor(x), math.floor(z)
        placed.append(Label(
            cand.way_id, cand.name, x, z, y,
            cellmath.cell_of(ix, iz), slid, cand.junction,
        ))

    if cells is not None:
        placed = [label for label in placed if label.cell in cells]
    placed.sort(key=lambda label: (label.cell, label.name, label.x, label.z))
    return LabelPlan(tuple(placed), tuple(skipped))


def apply_labels(
    paced: Any,
    labels: Sequence[Label],
    cells: Sequence[tuple[int, int]],
    *,
    hop: Callable[..., Any] | None = None,
) -> list[str]:
    """Kill this cell's labels, then summon the plan. Writes no blocks.

    @param paced: MCP client with ``text(name, args)``
    @param labels: labels to summon (others are ignored)
    @param cells: cells to refresh; a cell with no label is still cleared
    @param hop: ``(lo, hi, why) -> restore|None`` so the chunks load; restore runs after
    @returns: tool result strings, kill then summons, one cell at a time
    """
    return _refresh(paced, labels, cells, hop=hop)


def undo_labels(
    paced: Any,
    cells: Sequence[tuple[int, int]],
    *,
    hop: Callable[..., Any] | None = None,
) -> list[str]:
    """Kill road-name labels in ``cells``. Writes no blocks.

    @param paced: MCP client with ``text(name, args)``
    @param cells: cells whose ``wb_cell`` tag is cleared
    @param hop: optional chunk-load hop, as in :func:`apply_labels`
    @returns: kill result strings
    """
    return _refresh(paced, (), cells, hop=hop)


def _refresh(paced, labels, cells, *, hop) -> list[str]:
    by_cell: dict[tuple[int, int], list[Label]] = {}
    for label in labels:
        by_cell.setdefault(label.cell, []).append(label)
    out: list[str] = []
    for cell in cells:
        lo, hi = _cell_box(cell)
        why = (
            f'Road-name labels for cell {cell[0]},{cell[1]}: '
            'moving you so this cell is loaded; I will put you back.'
        )
        restore = hop(lo, hi, why) if hop else None
        try:
            out.extend(_kill_cell(paced, cell))
            for label in by_cell.get(cell, ()):
                out.append(paced.text('entity_summon', summon_args(label)))
        finally:
            if restore:
                restore()
    return out


_QUERY_LIMIT = 1024


def label_uuids(listing: str, cell: tuple[int, int]) -> list[str]:
    """UUIDs of this cell's labels in an ``entity_query`` listing.

    The selector is not trusted to filter: an entry counts only when its own tags
    include both ``wb_label`` and the cell tag.

    @param listing: ``entity_query`` text (``- uuid: ...`` entries with a ``tags[n]:`` line)
    @param cell: cell whose ``wb_cell`` tag must be present
    @returns: matching entity UUIDs
    """
    want = {TAG, cell_tag(cell)}
    found: list[str] = []
    for entry in listing.split('- uuid:')[1:]:
        uuid = entry.split(None, 1)[0].strip('"')
        tags: set[str] = set()
        for line in entry.splitlines():
            head, _, rest = line.strip().partition(':')
            if head.startswith('tags'):
                tags = {t.strip().strip('"') for t in rest.split(',')}
        if want <= tags:
            found.append(uuid)
    return found


def _kill_cell(paced, cell: tuple[int, int]) -> list[str]:
    """Kill this cell's labels with ``entity_query`` + ``entity_kill``.

    The world server does not always register ``command_execute``, so the kill
    never depends on it. Equivalent to :func:`kill_command` for the cell.
    """
    listing = paced.text('entity_query', {
        'dimension': DIMENSION,
        'selector': f'@e[tag={TAG}]',
        'limit': _QUERY_LIMIT,
    })
    return [
        paced.text('entity_kill', {'uuid': uuid})
        for uuid in label_uuids(listing, cell)
    ]


def _cell_box(cell: tuple[int, int]) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    x0, z0, x1, z1 = cellmath.bounds(cell)
    return (x0, 60, z0), (x1, 90, z1)


def _negative_layer(layer: str) -> bool:
    try:
        return float(layer) < 0
    except ValueError:
        return False


def _is_air(block: str | None) -> bool:
    if not block:
        return False
    name = block.split('[', 1)[0]
    if name.startswith('minecraft:'):
        name = name[len('minecraft:'):]
    return name in _AIR


def _metrics(points: Sequence[tuple[float, float]]) -> tuple[list[float], float]:
    seg: list[float] = []
    total = 0.0
    for a, b in zip(points, points[1:]):
        dist = math.hypot(b[0] - a[0], b[1] - a[1])
        seg.append(dist)
        total += dist
    return seg, total


def _at(points: Sequence[tuple[float, float]], seg: Sequence[float], dist: float) -> tuple[float, float]:
    if dist <= 0 or not seg:
        return points[0]
    left = dist
    for (a, b), length in zip(zip(points, points[1:]), seg):
        if length <= 0:
            continue
        if left <= length:
            t = left / length
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        left -= length
    return points[-1]


def _point_seg(px: float, pz: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, az = a
    bx, bz = b
    dx, dz = bx - ax, bz - az
    denom = dx * dx + dz * dz
    if denom == 0:
        t = 0.0
    else:
        t = max(0.0, min(1.0, ((px - ax) * dx + (pz - az) * dz) / denom))
    return math.hypot(px - (ax + t * dx), pz - (az + t * dz))


def _samples(points, seg, total) -> list[tuple[float, float, float]]:
    if total <= 0:
        return []
    out: list[tuple[float, float, float]] = []
    dist = 0.0
    while dist < total:
        x, z = _at(points, seg, dist)
        out.append((dist, x, z))
        dist += SAMPLE_STEP
    end_x, end_z = _at(points, seg, total)
    if not out or math.hypot(out[-1][1] - end_x, out[-1][2] - end_z) > 0.5:
        out.append((total, end_x, end_z))
    return out


def _junction_distances(ways: Sequence[NamedWay], metrics) -> list[list[float]]:
    buckets: dict[tuple[int, int], list[tuple[int, float, float, float]]] = {}
    samples: list[tuple[int, float, float, float]] = []
    for index, way in enumerate(ways):
        seg, total = metrics[index]
        for dist, x, z in _samples(way.points, seg, total):
            item = (index, dist, x, z)
            samples.append(item)
            key = (math.floor(x / SAMPLE_STEP), math.floor(z / SAMPLE_STEP))
            buckets.setdefault(key, []).append(item)
    found: list[list[float]] = [[] for _ in ways]
    for index, dist, x, z in samples:
        bx, bz = math.floor(x / SAMPLE_STEP), math.floor(z / SAMPLE_STEP)
        hit = False
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for other, _od, ox, oz in buckets.get((bx + dx, bz + dz), ()):
                    if other == index or ways[other].name == ways[index].name:
                        continue
                    if math.hypot(x - ox, z - oz) <= JUNCTION_REACH:
                        found[index].append(dist)
                        hit = True
                        break
                if hit:
                    break
            if hit:
                break
    return found


def _cluster(distances: Sequence[float], tol: float = SAMPLE_STEP) -> list[float]:
    out: list[float] = []
    for dist in sorted(distances):
        if not out or dist - out[-1] > tol:
            out.append(dist)
    return out


def _offset(dist: float, length: float) -> float:
    if length <= JUNCTION_OFFSET:
        return length / 2.0
    forward = dist + JUNCTION_OFFSET
    back = dist - JUNCTION_OFFSET
    forward_ok = forward <= length
    back_ok = back >= 0
    if forward_ok and back_ok:
        return forward if (length - dist) >= dist else back
    if forward_ok:
        return forward
    if back_ok:
        return back
    return min(max(dist, 0.0), length)


def _way_anchors(length: float, junction_ds: Sequence[float]) -> list[tuple[float, bool]]:
    junctions: list[tuple[float, bool]] = []
    for dist in _cluster(junction_ds):
        anchor = _offset(dist, length)
        if any(abs(anchor - prev) < MIN_SPACING for prev, _flag in junctions):
            continue
        junctions.append((anchor, True))
    if not junctions and length <= MAX_GAP:
        return [(length / 2.0, False)]
    regular: list[tuple[float, bool]] = []
    dist = SPACING / 2.0
    steps: list[float] = []
    while dist < length - 1e-6:
        steps.append(dist)
        dist += SPACING
    for step in steps:
        if any(abs(step - prev) < MIN_SPACING for prev, _flag in junctions):
            continue
        if regular and abs(step - regular[-1][0]) < MIN_SPACING:
            continue
        regular.append((step, False))
    return junctions + regular


def _column_clear(ix: int, iz: int, gy: int | None, block_at: BlockAt, bridge_at: BridgeAt) -> bool:
    if gy is None or int(gy) <= 0:
        return False
    if bridge_at(ix, iz):
        return False
    base = int(gy)
    for dy in range(1, LABEL_ABOVE + 1):
        if not _is_air(block_at(ix, base + dy, iz)):
            return False
    return True


def _slide(way, dist, seg, total, ground_y, block_at, bridge_at):
    steps = [0.0]
    for i in range(1, int(SLIDE_LIMIT) + 1):
        steps.append(float(i))
        steps.append(-float(i))
    for delta in steps:
        along = dist + delta
        if along < 0 or along > total:
            continue
        x, z = _at(way.points, seg, along)
        ix, iz = math.floor(x), math.floor(z)
        gy = ground_y(ix, iz)
        if not _column_clear(ix, iz, gy, block_at, bridge_at):
            continue
        return x, z, int(gy) + LABEL_ABOVE, delta != 0.0
    return None


def _too_close(x: float, z: float, name: str, junction: bool, other: Label) -> bool:
    gap = math.hypot(x - other.x, z - other.z)
    if gap >= MIN_SPACING:
        return False
    if junction and other.junction and name != other.name and gap <= JUNCTION_CLUSTER:
        return False
    return True


def _in_window(x: float, z: float, cells: frozenset[tuple[int, int]], pad: float) -> bool:
    margin = math.ceil(pad)
    for cell in cells:
        x0, z0, x1, z1 = cellmath.bounds(cell, margin)
        if x0 <= x <= x1 and z0 <= z <= z1:
            return True
    return False


def _report_way(way: NamedWay, cells: frozenset[tuple[int, int]] | None) -> bool:
    if cells is None:
        return True
    seg, total = _metrics(way.points)
    for _dist, x, z in _samples(way.points, seg, total):
        if cellmath.cell_of(math.floor(x), math.floor(z)) in cells:
            return True
    return False


def _parse_cell(text: str) -> tuple[int, int]:
    tx, tz = text.split(',')
    return int(tx), int(tz)


def _print_plan(plan: LabelPlan) -> None:
    for label in plan.labels:
        slid = '  slid' if label.slid else ''
        print(
            f'{label.cell[0]},{label.cell[1]}  {label.x:.1f} {label.y} {label.z:.1f}  '
            f'{label.name}  way {label.way_id}{slid}'
        )
    print(f'{len(plan.labels)} labels, {len(plan.skipped)} skipped')
    for skip in plan.skipped[:20]:
        print(f'  skip {skip.name} ({skip.way_id}): {skip.reason}')
    if len(plan.skipped) > 20:
        print(f'  … {len(plan.skipped) - 20} more skipped')


def _live_callbacks():
    """Lidar heights, and blocks from the saved world.

    A live scan lists an unloaded chunk as air, so a label would be planned
    inside a tree whenever the player is too far away to have the cell loaded.
    The saved world returns None for a missing chunk, and None is not clear air.
    """
    from lidar_heightmap import Maps
    from site_kit import fresh_planning_world

    maps = Maps()
    world = fresh_planning_world()

    def ground_y(x: int, z: int) -> int | None:
        for field in ('terrain', 'base'):
            try:
                y = int(maps.get(x, z, field))
            except (KeyError, IndexError, TypeError):
                continue
            if y > 0:
                return y
        return None

    def block_at(x: int, y: int, z: int) -> str | None:
        return world.block(x, y, z)

    return ground_y, block_at


def _make_hop(paced):
    from site_kit import _bring_player

    def hop(lo, hi, why):
        return _bring_player(paced, lo, hi, why=why)

    return hop


def main(argv: list[str] | None = None) -> int:
    """Plan, summon, or remove road-name labels for one or more cells.

    @param argv: arguments without the program name
    @returns: 0 on success
    """
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cell', action='append', required=True, help='tx,tz (repeatable)')
    ap.add_argument('--apply', action='store_true', help='summon labels (needs the world lock)')
    ap.add_argument('--undo', action='store_true', help='kill this cell\'s labels (needs the world lock)')
    args = ap.parse_args(argv)
    if args.apply and args.undo:
        ap.error('pass only one of --apply and --undo')
    cells = tuple(_parse_cell(text) for text in args.cell)
    if args.apply or args.undo:
        import site_lock
        site_lock.check()
        from mc_paced import Paced
        paced = Paced()
        hop = _make_hop(paced)
        if args.undo:
            undo_labels(paced, cells, hop=hop)
            print(f'cleared road-name labels in {len(cells)} cell(s)')
            return 0
        ways = _load_ways()
        ground_y, block_at = _live_callbacks()
        plan = plan_labels(
            ways, ground_y=ground_y, block_at=block_at,
            bridge_at=bridge_predicate(ways), cells=frozenset(cells),
        )
        _print_plan(plan)
        apply_labels(paced, plan.labels, cells, hop=hop)
        print(f'summoned {len(plan.labels)} road-name labels')
        return 0
    ways = _load_ways()
    ground_y, block_at = _live_callbacks()
    plan = plan_labels(
        ways, ground_y=ground_y, block_at=block_at,
        bridge_at=bridge_predicate(ways), cells=frozenset(cells),
    )
    _print_plan(plan)
    return 0


def _load_ways() -> tuple[NamedWay, ...]:
    from osm_footprints import OSMIndex

    index = OSMIndex()
    rows = []
    for (name, highway, pts), props in zip(index.streets, index.street_props):
        merged = dict(props)
        if name and not merged.get('name'):
            merged['name'] = name
        if highway and not merged.get('highway'):
            merged['highway'] = highway
        rows.append((merged, pts))
    return ways_from_map(rows)


if __name__ == '__main__':
    sys.exit(main())

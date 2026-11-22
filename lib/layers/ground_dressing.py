"""Ground dressing: frontage planting, rough-grass texture and woodland floors.

The ground builder supplies classified surfaces and explicit protection masks.
This module adds the existing seeded palette choices without changing heights
or interpreting place evidence into a new design.
"""
from __future__ import annotations

import math
import random

import cellmath
from block_store import block_id
from ground_sources import MARGIN
from layer_common import AIRS
from site_kit import Plan, World, full
from tree_species import DEFAULT_LEAVES

SHRUB_EDGE_REACH_SQ = 2   # shrubs only within one column (diagonals too) of the house wall or plot boundary (#339)


COVER_EDGE_REACH_SQ = 8   # grass tufts and ferns within two columns of that edge (#339)


ROUGH_GRASS_TAGS = {
    ('landuse', 'meadow'): 'meadow',
    ('landuse', 'farmland'): 'pasture',
    ('natural', 'grassland'): 'meadow',
    ('natural', 'heath'): 'pasture',
    ('natural', 'scrub'): 'pasture',
    ('leisure', 'nature_reserve'): 'pasture',
}


LAWN_TAGS = frozenset({
    ('landuse', 'grass'),
    ('leisure', 'pitch'),
    ('leisure', 'golf_course'),
    ('leisure', 'garden'),
    ('leisure', 'playground'),
})


TEXTURE_RADIUS = 8          # local brightness spread for aerial variance (#319 prototype)


BARE_DIRT = ('coarse_dirt', 'rooted_dirt')


def rough_grass_columns(cell: tuple[int, int], idx, classes: dict | None = None) -> dict:
    """``{column: palette}`` for open-grass columns gated by OSM tag and ``ground_texture`` (#319).

    Gates: ``landuse=meadow|farmland``, ``natural=grassland|heath|scrub``, ``leisure=nature_reserve``.
    Lawns (``landuse=grass``, ``leisure=pitch|golf_course|garden|playground``) are never included.
    Meadow beats pasture when both cover a column. The column's land-use class must ship a matching
    ``ground_texture`` palette.

    @param cell: ``(tx, tz)``
    @param idx: ``OSMIndex``
    @param classes: ``coverage.area_classes`` result when the caller already has it
    @returns: only eligible columns; empty when no class enables ground texture
    """
    import land_use
    from coverage import area_classes, scan_polygon
    from osm_footprints import osm_tags

    if classes is None:
        classes = area_classes(idx, cellmath.bounds(cell, MARGIN))
    enabled = {cid: land_use.ground_texture(cid) for cid in land_use.CLASSES}
    if not any(enabled.values()):
        return {}
    bbox = cellmath.bounds(cell, MARGIN)
    lawn: set = set()
    pasture: set = set()
    meadow: set = set()
    for rings, props in getattr(idx, 'areas', ()) or ():
        tags = osm_tags(props)
        pairs = [(k, tags[k]) for k in ('landuse', 'leisure', 'natural') if tags.get(k)]
        if any(pair in LAWN_TAGS for pair in pairs):
            lawn.update(scan_polygon(rings, bbox))
            continue
        for key, value in pairs:
            palette = ROUGH_GRASS_TAGS.get((key, value))
            if palette == 'meadow':
                meadow.update(scan_polygon(rings, bbox))
            elif palette == 'pasture':
                pasture.update(scan_polygon(rings, bbox))
    out = {}
    for q, hit in classes.items():
        if q in lawn:
            continue
        palettes = enabled.get(hit[0])
        if not palettes:
            continue
        name = 'meadow' if q in meadow and 'meadow' in palettes else (
            'pasture' if q in pasture and 'pasture' in palettes else None)
        if name is None and q in meadow and 'pasture' in palettes:
            name = 'pasture'
        if name is None and q in pasture and 'meadow' in palettes:
            name = 'meadow'
        if name is not None:
            out[q] = name
    return out


def woodland_floor_columns(cell: tuple[int, int], idx, classes: dict | None = None) -> tuple[dict, set]:
    """``({column: class id}, wood mask)`` for the woodland floor (#348).

    Wood is ``natural=wood`` or ``landuse=forest``. A column is eligible when its land-use class
    ships ``ground_texture.wood``, it is not a lawn, and a meadow or pasture tag does not already
    claim it (#319 stays on those columns). ``natural=scrub`` stays on the pasture palette unless
    that class's wood palette sets ``scrub`` true, in which case the floor takes the column.
    The mask is every wood column (and opted-in scrub), including the scan margin, so the edge
    test sees the polygon rather than the cell border. The grass rim itself is applied later
    from the class ``edge`` reach.

    @param cell: ``(tx, tz)``
    @param idx: ``OSMIndex``
    @param classes: ``coverage.area_classes`` result when the caller already has it
    @returns: eligible columns (empty when no class ships a wood palette) and the wood mask
    """
    import land_use
    from coverage import scan_polygon
    from osm_footprints import osm_tags

    if classes is None:
        from coverage import area_classes
        classes = area_classes(idx, cellmath.bounds(cell, MARGIN))
    wood_rules = {}
    for cid in land_use.CLASSES:
        texture = land_use.ground_texture(cid) or {}
        rule = texture.get('wood') if isinstance(texture, dict) else None
        if isinstance(rule, dict):
            wood_rules[cid] = rule
    if not wood_rules:
        return {}, set()
    reach = max(int(rule['edge']) for rule in wood_rules.values())
    bbox = cellmath.bounds(cell, max(MARGIN, reach))
    lawn: set = set()
    claimed: set = set()
    wood: set = set()
    scrub: set = set()
    for rings, props in getattr(idx, 'areas', ()) or ():
        tags = osm_tags(props)
        pairs = [(k, tags[k]) for k in ('landuse', 'leisure', 'natural') if tags.get(k)]
        is_lawn = any(pair in LAWN_TAGS for pair in pairs)
        is_wood = any(pair in land_use.WOOD_TAGS for pair in pairs)
        is_scrub = any(pair in land_use.SCRUB_TAGS for pair in pairs)
        is_claimed = any(pair not in land_use.SCRUB_TAGS and pair in ROUGH_GRASS_TAGS for pair in pairs)
        if not (is_lawn or is_wood or is_scrub or is_claimed):
            continue
        cols = set(scan_polygon(rings, bbox))
        if is_lawn:
            lawn.update(cols)
            continue
        if is_wood:
            wood.update(cols)
        if is_scrub:
            scrub.update(cols)
        if is_claimed:
            claimed.update(cols)
    wood -= lawn
    scrub -= lawn
    claimed -= lawn
    mask = set(wood)
    eligible = {}
    for q in wood:
        if q in claimed:
            continue
        hit = classes.get(q)
        if hit and hit[0] in wood_rules:
            if q in scrub and not wood_rules[hit[0]].get('scrub'):
                continue
            eligible[q] = hit[0]
    for q in scrub:
        if q in wood or q in claimed:
            continue
        hit = classes.get(q)
        if not hit or hit[0] not in wood_rules or not wood_rules[hit[0]].get('scrub'):
            continue
        mask.add(q)
        eligible[q] = hit[0]
    return eligible, mask


def _wood_floor_edge(q: tuple[int, int], mask: set, reach: int) -> bool:
    """True when ``q`` is within ``reach`` columns (Chebyshev) of a column outside ``mask``.

    @param q: column
    @param mask: wood polygon columns, including those outside the cell
    @param reach: grass rim in columns; 0 keeps no rim
    @returns: whether the column keeps grass as polygon edge
    """
    if reach <= 0:
        return False
    x, z = q
    for dx in range(-reach, reach + 1):
        for dz in range(-reach, reach + 1):
            if (x + dx, z + dz) not in mask:
                return True
    return False


def aerial_texture_field(rgb: dict, columns, *, radius: int = TEXTURE_RADIUS) -> dict:
    """Per-column aerial texture score and pale/brown flag from local brightness spread (#319).

    Score is local brightness standard deviation over ``±radius``, scaled between the set's
    10th and 90th percentiles into ``[0, 1]``. Pale or brown (brightness above the 85th percentile,
    or red ≥ 0.98 × green) marks dry/bare ground.

    @param rgb: ``{(x, z): (r, g, b)}`` in 0..1
    @param columns: columns to score
    @param radius: neighbourhood half-width in blocks
    @returns: ``{(x, z): {'score': float, 'bare': bool}}``
    """
    cols = [q for q in columns if q in rgb]
    if not cols:
        return {}
    bright = {q: sum(rgb[q][:3]) / 3 for q in cols}
    spreads = {}
    for x, z in cols:
        samples = [bright[n] for dx in range(-radius, radius + 1) for dz in range(-radius, radius + 1)
                   if (n := (x + dx, z + dz)) in bright]
        if len(samples) < 2:
            spreads[(x, z)] = 0.0
            continue
        mean = sum(samples) / len(samples)
        spreads[(x, z)] = (sum((v - mean) ** 2 for v in samples) / len(samples)) ** 0.5
    ordered = sorted(spreads.values())
    lo = ordered[max(0, int(0.10 * (len(ordered) - 1)))]
    hi = ordered[min(len(ordered) - 1, int(0.90 * (len(ordered) - 1)))]
    span = hi - lo
    bright_vals = sorted(bright.values())
    pale_cut = bright_vals[min(len(bright_vals) - 1, int(0.85 * (len(bright_vals) - 1)))]
    out = {}
    for q in cols:
        r, g, _b = rgb[q][:3]
        score = 0.0 if span <= 1e-9 else max(0.0, min(1.0, (spreads[q] - lo) / span))
        # Strict above the cut so a flat field (every column the same) is not all "bare".
        out[q] = {'score': score, 'bare': bright[q] > pale_cut or r >= 0.98 * g}
    return out


def plan_ground_texture(plan: Plan, world: World, cell: tuple[int, int], ys: dict, kind: dict,
                        rgb: dict, palettes: dict, *, masked=(), owned=(), clear=(),
                        barriers=()) -> dict:
    """Place rough-grass tufts, tall grass, mottled dirt and meadow flowers (#319).

    Driven by ``ground_texture`` palettes and aerial texture, not by ``planting`` (null when
    ``frontage.inference`` is false). Flower kinds come from ``surface_probe.REFINE_PLANTS``. Skips
    footprints, routes, tree/decor masks, neighbour-owned columns and barrier columns. Seeded
    per cell and column so reruns match. Counts land in the ground report; plants sit in air
    above grass (cleared on redo / foliage strip); dirt replaces the grass surface.

    @param plan: ground plan under construction
    @param world: live world reader
    @param cell: ``(tx, tz)`` for seeds
    @param ys: surface Y per column
    @param kind: surface kind per column (only ``grass`` is eligible)
    @param rgb: aerial colours
    @param palettes: ``{column: 'pasture'|'meadow'}`` from :func:`rough_grass_columns`
    @param masked: tree/decor mask positions
    @param owned: neighbour-owned positions
    @param clear: reserved route/opening columns
    @param barriers: hedge/wall/fence columns
    @returns: counts and item rows for the report
    """
    import land_use
    import provenance as prov

    report = {'tufts': 0, 'tall_grass': 0, 'bare': 0, 'flowers': 0, 'lawn_skipped': 0, 'items': []}
    if not palettes:
        return report
    tx, tz = cell
    protected = set(masked) | set(owned)
    blocked = set(clear) | {(x, z) for x, _y, z in protected} | set(barriers)
    candidates = {q: name for q, name in palettes.items()
                  if q in ys and q in kind and kind[q] == 'grass' and q not in blocked}
    report['lawn_skipped'] = sum(1 for q in ys if q in kind and kind[q] == 'grass'
                                 and q not in palettes and q not in blocked)
    texture = aerial_texture_field(rgb, candidates)
    rules: dict = {}
    for cid in ('park', 'rural', 'natural'):
        for name, palette in (land_use.ground_texture(cid) or {}).items():
            rules.setdefault(name, palette)

    def block(p):
        return plan.cells[p] if p in plan.cells else world.block(*p)

    def air(p):
        b = block(p)
        return b is not None and block_id(b, policy='read') in AIRS

    def is_grass_top(p):
        return block_id(block(p), policy='read') == 'minecraft:grass_block' or block_id(block(p), policy='read') == 'grass_block'

    taken = set()
    for q in sorted(candidates):
        name = candidates[q]
        rule = rules.get(name)
        # Wood floor is a different palette (#348); only open-grass rules place tufts here.
        if not rule or 'bare' not in rule:
            continue
        x, z = q
        gy = ys[q]
        top = (x, gy, z)
        if top in protected or not is_grass_top(top):
            continue
        field = texture.get(q) or {'score': 0.0, 'bare': False}
        rnd = random.Random(f'ground_texture:{tx}:{tz}:{x}:{z}')
        if rule['bare'] and field['bare']:
            dirt = rnd.choice(BARE_DIRT)
            plan.set(x, gy, z, dirt)
            report['bare'] += 1
            report['items'].append({
                'kind': 'bare', 'position': list(top), 'block': dirt,
                'palette': name, 'label': 'ground texture bare (#319)',
                'provenance': prov.provenance_record(source='inference', confidence='low'),
            })
            taken.add(q)
            continue
        score = field['score']
        tuft_lo, tuft_hi = rule['tufts']
        tall_lo, tall_hi = rule['tall_grass']
        plant = (x, gy + 1, z)
        live_plant = block_id(block(plant), policy='read')
        if not air(plant) and live_plant not in ('short_grass', 'tall_grass',
                                                 'minecraft:short_grass', 'minecraft:tall_grass'):
            continue
        # Tall grass first (rarer), then tufts; share rises with aerial texture.
        if rnd.random() < tall_lo + score * (tall_hi - tall_lo):
            upper = (x, gy + 2, z)
            upper_ok = air(upper) or block_id(block(upper), policy='read') in ('tall_grass', 'minecraft:tall_grass')
            if air(plant) and upper_ok:
                plan.set(*plant, 'tall_grass[half=lower]')
                plan.set(*upper, 'tall_grass[half=upper]')
                report['tall_grass'] += 1
                report['items'].append({
                    'kind': 'tall_grass', 'position': list(plant), 'block': 'tall_grass',
                    'palette': name, 'label': 'ground texture tall grass (#319)',
                    'provenance': prov.provenance_record(source='inference', confidence='low'),
                })
                taken.add(q)
                continue
        if rnd.random() < tuft_lo + score * (tuft_hi - tuft_lo):
            plan.set(*plant, 'short_grass')
            report['tufts'] += 1
            report['items'].append({
                'kind': 'tufts', 'position': list(plant), 'block': 'short_grass',
                'palette': name, 'label': 'ground texture tuft (#319)',
                'provenance': prov.provenance_record(source='inference', confidence='low'),
            })
            taken.add(q)

    meadow_cols = [q for q, name in candidates.items() if name == 'meadow' and q not in taken]
    flower_rule = (rules.get('meadow') or {}).get('flowers')
    if flower_rule and meadow_cols:
        target = round(flower_rule['share'] * len(meadow_cols))
        kinds = flower_rule['kinds']
        ranked = sorted(
            meadow_cols,
            key=lambda q: (random.Random(f'ground_texture:flower:{tx}:{tz}:{q[0]}:{q[1]}').random(), q))
        for x, z in ranked:
            if report['flowers'] >= target:
                break
            gy = ys[(x, z)]
            plant = (x, gy + 1, z)
            flower = random.Random(f'ground_texture:flower-kind:{tx}:{tz}:{x}:{z}').choice(kinds)
            live = block_id(block(plant), policy='read')
            if not (air(plant) or live in (flower, full(flower))):
                continue
            if not is_grass_top((x, gy, z)):
                continue
            plan.set(*plant, flower)
            report['flowers'] += 1
            report['items'].append({
                'kind': 'flowers', 'position': list(plant), 'block': flower,
                'palette': 'meadow', 'label': 'ground texture flower (#319)',
                'provenance': prov.provenance_record(source='inference', confidence='low'),
            })
    return report


def plan_woodland_floor(plan: Plan, world: World, cell: tuple[int, int], ys: dict, kind: dict,
                        columns: dict, mask: set, *, masked=(), owned=(), clear=(),
                        barriers=()) -> dict:
    """Replace the grass surface with the woodland floor mix at lidar height (#348).

    ``columns`` is ``{column: class id}`` from :func:`woodland_floor_columns` (the rim included).
    Columns within the class ``edge`` reach of a column outside ``mask`` keep grass. Interior
    columns take ``podzol``, ``coarse_dirt`` and ``moss_block`` at the configured shares; the
    rest keep grass. Nothing is written at any other Y, and no plant is placed. Skips columns
    that are not grass, plus footprints, routes, tree and decor masks, feature and neighbour
    columns, and barriers. Seeded per cell and column so a rerun matches. Counts land in the
    ground report.

    @param plan: ground plan under construction; the grass surface is already set
    @param world: live world reader
    @param cell: ``(tx, tz)`` for seeds
    @param ys: surface Y per column (lidar height); not modified
    @param kind: surface kind per column (only ``grass`` is eligible)
    @param columns: eligible ``{column: class id}``
    @param mask: wood polygon columns used for the edge test
    @param masked: tree/decor mask positions
    @param owned: feature and neighbour owned positions
    @param clear: reserved route, opening and footprint columns
    @param barriers: hedge/wall/fence columns
    @returns: per-block counts (``edge`` is the grass rim) and item rows for replaced blocks
    """
    import land_use
    import provenance as prov

    report = {'podzol': 0, 'coarse_dirt': 0, 'moss_block': 0, 'grass': 0,
              'edge': 0, 'skipped': 0, 'items': []}
    if not columns:
        return report
    tx, tz = cell
    protected = set(masked) | set(owned)
    blocked = set(clear) | {(x, z) for x, _y, z in protected} | set(barriers)
    rules = {}

    def block(p):
        return plan.cells[p] if p in plan.cells else world.block(*p)

    def is_grass_top(p):
        name = block_id(block(p), policy='read')
        return name in ('grass_block', 'minecraft:grass_block')

    for q in sorted(columns):
        if q not in ys or q not in kind or kind[q] != 'grass' or q not in mask or q in blocked:
            report['skipped'] += 1
            continue
        x, z = q
        gy = ys[q]
        top = (x, gy, z)
        if top in protected or not is_grass_top(top):
            report['skipped'] += 1
            continue
        cid = columns[q]
        if cid not in rules:
            rules[cid] = (land_use.ground_texture(cid) or {}).get('wood')
        rule = rules[cid]
        if not rule:
            report['skipped'] += 1
            continue
        if _wood_floor_edge(q, mask, int(rule['edge'])):
            report['edge'] += 1
            continue
        roll = random.Random(f'woodland_floor:{tx}:{tz}:{x}:{z}').random()
        cursor = 0.0
        chosen = None
        for name in land_use.WOOD_FLOOR_BLOCKS:
            cursor += rule[name]
            if roll < cursor:
                chosen = name
                break
        if chosen is None:
            report['grass'] += 1
            continue
        plan.set(x, gy, z, chosen)
        report[chosen] += 1
        report['items'].append({
            'kind': chosen, 'position': [x, gy, z], 'block': chosen,
            'palette': 'wood', 'label': 'woodland floor (#348)',
            'provenance': prov.provenance_record(source='inference', confidence='low'),
        })
    return report


def plan_planting(plan: Plan, world: World, guessed: set, ys: dict, plots: tuple, segments: dict,
                  *, clear=(), masked=(), owned=(), barriers=()) -> list[dict]:
    """Plan labelled planting on guessed grass only; return per-plot counts and provenance.

    ``plots`` and ``segments`` are the existing garden-source geometry. Mask and ownership exclusions
    apply to whole columns; retained shrubs and barriers also reserve their neighbours. Clearance
    and placement air checks read the ground plan over the input world.
    """
    import land_use
    import provenance as prov

    owner, info, _street, idx = plots
    fp = idx.cells()
    protected = set(masked) | set(owned)
    blocked = set(clear) | set(fp) | {(x, z) for x, _y, z in protected}
    houses, boundaries, bed_edges, lawns = {}, {}, {}, {}
    for q, i in fp.items():
        houses.setdefault(i, set()).add(q)
    for (i, role), cols in segments.items():
        boundaries.setdefault(i, set()).update(cols)
        if role in ('front', 'side_front', 'side_back'):
            bed_edges.setdefault(i, set()).update(cols)
    for q in sorted(ys):
        if q in owner:
            lawns.setdefault(owner[q], [])
            if q in guessed and plan.cells.get((q[0], ys[q], q[1])) == full('grass_block'):
                lawns[owner[q]].append(q)

    def block(p):
        return plan.cells[p] if p in plan.cells else world.block(*p)

    # Retained leaves and barriers also need a column's gap, including content just outside
    # this cell. Read only protected positions near candidate lawn, after planned clearing.
    nearby = {(x + a, z + b) for lawn in lawns.values() for x, z in lawn
              for a in (-1, 0, 1) for b in (-1, 0, 1)}
    retained_barriers = set()
    for p in protected:
        x, _y, z = p
        if (x, z) in nearby:
            name = block_id(block(p), policy='read')
            if name.endswith(('_leaves', '_wall', '_fence', '_fence_gate', '_planks', '_shelf')) or name == full('iron_bars'):
                retained_barriers.add((x, z))
    barrier_gap = {(x + a, z + b) for x, z in set(barriers) | retained_barriers
                   for a in (-1, 0, 1) for b in (-1, 0, 1)}

    def distance(q, cols):
        return min(((q[0] - x) ** 2 + (q[1] - z) ** 2 for x, z in cols), default=math.inf)

    def air(p):
        b = block(p)
        return b is not None and block_id(b, policy='read') in AIRS

    def edge_distance(q, i):
        return min(distance(q, houses.get(i, ())), distance(q, boundaries.get(i, ())))

    shrub_gap = set()
    report = []
    for i, lawn in sorted(lawns.items()):
        meta = info[i]
        lu = meta.get('land_use') or 'unknown'
        cls = lu.get('class', 'unknown') if isinstance(lu, dict) else lu
        row = {'plot': i, 'street': meta.get('street', ''), 'number': meta.get('number', ''),
               'land_use': cls, 'guessed_lawn': len(lawn), 'shrubs': 0, 'flowers': 0, 'cover': 0, 'items': []}
        report.append(row)
        rule = land_use.planting_rule(cls, 'shrubs')
        target = len(lawn) // rule['per_columns'] if rule is not None else 0
        candidates = sorted((q for q in lawn if edge_distance(q, i) <= SHRUB_EDGE_REACH_SQ),
                            key=lambda q: (distance(q, houses.get(i, ())),
                                           distance(q, boundaries.get(i, ())), q))
        for x, z in candidates:
            if row['shrubs'] >= target:
                break
            if (x, z) in blocked or (x, z) in barrier_gap or (x, z) in shrub_gap:
                continue
            height = 2 if (row['shrubs'] + 1) % 3 == 0 else 1
            positions = [(x, ys[(x, z)] + dy, z) for dy in range(1, height + 1)]
            if not all(air(p) for p in positions):
                continue
            for p in positions:
                plan.set(*p, DEFAULT_LEAVES)
            row['shrubs'] += 1
            row['items'].append({'kind': 'shrubs', 'position': list(positions[0]), 'height': height,
                                 'label': 'shrub inference (#265)',
                                 'provenance': prov.provenance_record(source='inference', confidence='low')})
            shrub_gap.update((x + a, z + b) for a in (-1, 0, 1) for b in (-1, 0, 1))
        rule = land_use.planting_rule(cls, 'flowers')
        if rule is not None:
            target = round(rule['share'] * len(lawn))
            flower = random.Random(f'flower-kind:{i}').choice(rule['kinds'])
            shrub_columns = {(item['position'][0], item['position'][2]) for item in row['items']}
            candidates = []
            for x, z in lawn:                              # already sorted by (x,z)
                q = (x, z)
                if q in blocked or q in shrub_columns:
                    continue
                if min(distance(q, houses.get(i, ())), distance(q, bed_edges.get(i, ()))) > 4:
                    continue
                p = (x, ys[q] + 1, z)
                if air(p) or block_id(block(p), policy='read') == full(flower):  # retain the same inferred flower on rerun
                    candidates.append(q)
            candidates.sort(key=lambda q: (random.Random(f'flower:{i}:{q[0]}:{q[1]}').random(), q))
            for x, z in candidates[:target]:
                p = (x, ys[(x, z)] + 1, z)
                plan.set(*p, flower)
                row['flowers'] += 1
                row['items'].append({'kind': 'flowers', 'position': list(p), 'block': flower,
                                     'label': 'flower inference (#266)',
                                     'provenance': prov.provenance_record(source='inference', confidence='low')})
        rule = land_use.planting_rule(cls, 'cover')
        if rule is None:
            continue
        kinds = [full(k) for k in rule['kinds']]
        target = round(rule['share'] * len(lawn))
        shrub_columns = {(item['position'][0], item['position'][2]) for item in row['items']}
        candidates = []
        for x, z in lawn:
            q = (x, z)
            if q in blocked or q in shrub_columns or edge_distance(q, i) > COVER_EDGE_REACH_SQ:
                continue
            p = (x, ys[q] + 1, z)
            if air(p) or block_id(block(p), policy='read') in kinds:  # retain the same inferred cover on rerun
                candidates.append(q)
        candidates.sort(key=lambda q: (random.Random(f'cover:{i}:{q[0]}:{q[1]}').random(), q))
        for x, z in candidates[:target]:
            p = (x, ys[(x, z)] + 1, z)
            kind = random.Random(f'cover-kind:{i}:{x}:{z}').choice(rule['kinds'])
            plan.set(*p, full(kind))
            row['cover'] += 1
            row['items'].append({'kind': 'cover', 'position': list(p), 'block': kind,
                                 'label': 'cover inference (#339)',
                                 'provenance': prov.provenance_record(source='inference', confidence='low')})
    return report

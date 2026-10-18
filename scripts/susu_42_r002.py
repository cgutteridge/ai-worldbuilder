#!/usr/bin/env python3
"""SUSU / Building 42, bake-off round R-002 (cold). Cell (48, -44).

Evidence table (real sources only; no previous Minecraft build):

| Fact | Source |
| --- | --- |
| Footprint | OSM way 33830305 ``University of Southampton Students' Union (SUSU)``, ``loc_ref`` 42, ``building:levels`` 4, 84 vertices, raster 3924 cells, bbox MC 6142,-5582 .. 6226,-5492. University register outline for building 42 matches that bbox (6142,-5582 .. 6228,-5491). Neighbours kept out: way 33830236 (B57 retail), way 34391673 (West Building). |
| Massing | Orthogonal boxes inside a 2-block OSM margin, so the outline is straight runs instead of the 84-vertex stair. Two open courts kept (west light well, east court). A short recess in front of portal 42-A so the concourse door sits on a wall. |
| Heights | DEFRA lidar via ``shims/lidar_query.py`` over the footprint (Geocraft rounding, ``top_y`` / ``ground_y`` / ``diff``). Three absolute roof plates, not one 4-storey box: high north/east median top_y 59 (p90 61, ground ~49-51); mid centre/south median top_y 57 (ground ~45-48); low west wing median top_y 52 (ground 44, standing ~8 m). Plates from the build floor (surrounding surface, not the lowest bare earth): HIGH 4 storeys (slab ~59), MID 2 storeys (slab ~56), LOW 1 storey (slab ~52). |
| Walls, openings | North is the public front (every portal snaps to a north-facing wall). 42_2025.jpg: salmon terracotta panel with the SUSU wordmark, white fascia, full-height glass doors (concourse, portal 42-A at 6192,-5564). 42-A.jpg is the same lobby before the 2025 rebrand. 42-B.jpg: dark-framed glass shopfront, double doors (Piazza, 6204,-5564). 42-C.jpg: black single-storey box, double glass doors (Latte's, 6224,-5555) — the east pavement is already ~Y 52, so the tall plate reads as one storey there. 42-D.jpg: white render, a brick pier, glazed bands, a narrower cafe door (6180,-5575). The outside stair is not in this build. Other faces are not in those photos: a quieter pier rhythm only, so the block is not a blank shell. |
| Roof + greeblies | Google satellite sheet z19 ``at_6184_-5537`` and z20 ``at_6178_-5560`` (look-only). SUSU is flat grey membrane. The red pitched roof to the north is a different building. Street-scale bits on the grey roofs: a row of flat skylights along the north edge of the high roof; a cluster of round vents mid-roof; two large rectangular rooflights on the south of that roof; a row of dark plant boxes on the lower west roof; a row of pyramid skylights on the mid roof; a parapet upstand on the roof edge. |

Parts (same high plate, three facade colours along the north front):

- HIGH_C x<6186: white concrete (cafe / 42-D), 4 storeys
- HIGH_W 6186<=x<6200: terracotta (SUSU panel / 42-A), 4 storeys
- HIGH_E x>=6200: black concrete (Piazza / Latte's), 4 storeys
- MID: white concrete, 2 storeys. Surrounding floor lands near Y 50, so two storeys put the slab on the lidar plate at ~57 (a third storey would overshoot).
- LOW: bricks, 1 storey. ``floor_min`` 48 keeps the west wing from sitting in the slope sample; one storey then lands the slab on lidar top_y 52. The taller glazing in 42-D is the white HIGH_C block beside it.

  python3 scripts/susu_42_r002.py
  SITE_OWNER=bakeoff-r002 python3 scripts/susu_42_r002.py --apply
"""
from __future__ import annotations

import argparse
import collections
import json

import feature_build as fb
import lidar_query
import osm_footprints
import portals
from script_env import ROOT
from site_kit import apply, preview

WAY = '33830305'
SPEC = ROOT / 'site' / 'builds' / 'susu_42_r002.json'
# Boxes (x0, x1, z0, z1) inclusive. Tuned to the lidar occupancy of way 33830305.
RECTS = (
    (6158, 6186, -5582, -5572),
    (6154, 6212, -5578, -5544),
    (6194, 6226, -5568, -5532),
    (6166, 6206, -5550, -5502),
    (6142, 6168, -5556, -5504),
    (6176, 6204, -5508, -5492),
)
# Notches that are open on the aerial; do not fill them when squaring.
COURTS = (
    (6160, 6174, -5556, -5542),
    (6198, 6214, -5538, -5516),
)
# Pull the concourse wall onto portal 42-A (it sat 5.1 blocks inside the squared roof edge).
RECESS = (6190, 6194, -5568, -5565)
ORTH = ((1, 0), (-1, 0), (0, 1), (0, -1))
# Squared wall for 42-A is just over portals.DOOR_REACH (5). One extra block, printed.
DOOR_REACH = 6
HIGH_LABELS = ('HIGH_C', 'HIGH_W', 'HIGH_E')
# Ghost-surface sample (before this build existed): HIGH 48, MID 51, LOW 48.
# floor_y reads the live world, so a second apply would sit on our own walls.
FLOORS = {'HIGH_C': 48, 'HIGH_W': 48, 'HIGH_E': 48, 'MID': 51, 'LOW': 48}


def osm_cells(way: str) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """Raster cells of one OSM way, and cells of every other building.

    @param way: OSM way id string
    @returns: (cells of ``way``, cells of all other buildings)
    """
    idx = osm_footprints.OSMIndex()
    own: set[tuple[int, int]] = set()
    other: set[tuple[int, int]] = set()
    for (x, z), i in idx.cells().items():
        props = idx.buildings[i][1]
        wid = str(props.get('osm_way_id') or props.get('osm_id'))
        if wid == way:
            own.add((x, z))
        else:
            other.add((x, z))
    return own, other


def _dilate(cells: set[tuple[int, int]], radius: int) -> set[tuple[int, int]]:
    """Chebyshev dilation.

    @param cells: source cells
    @param radius: radius in blocks
    @returns: dilated set
    """
    out: set[tuple[int, int]] = set()
    for x, z in cells:
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                if max(abs(dx), abs(dz)) <= radius:
                    out.add((x + dx, z + dz))
    return out


def readable_footprint(orig: set[tuple[int, int]], other: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """Orthogonal mass inside the OSM margin, courts and the concourse recess kept open.

    @param orig: raster of way 33830305
    @param other: cells of neighbouring OSM buildings
    @returns: cells to build
    """
    allow = _dilate(orig, 2) - other
    union: set[tuple[int, int]] = set()
    for x0, x1, z0, z1 in RECTS:
        for x in range(x0, x1 + 1):
            for z in range(z0, z1 + 1):
                if (x, z) in allow:
                    union.add((x, z))
    for x0, x1, z0, z1 in COURTS:
        for x in range(x0, x1 + 1):
            for z in range(z0, z1 + 1):
                if (x, z) not in orig:
                    union.discard((x, z))
    x0, x1, z0, z1 = RECESS
    for x in range(x0, x1 + 1):
        for z in range(z0, z1 + 1):
            union.discard((x, z))
    return union


def _median_top(union: set[tuple[int, int]]) -> dict[tuple[int, int], int]:
    """Smoothed lidar top_y for each footprint cell.

    @param union: cells to classify
    @returns: cell → median top_y in a 5×5 of standing roof
    """
    xs = [c[0] for c in union]
    zs = [c[1] for c in union]
    x0, z0, x1, z1 = min(xs) - 3, min(zs) - 3, max(xs) + 3, max(zs) + 3
    grids = lidar_query.query(
        area=('mc', (x0, z0, x1, z1)),
        layers=('top_y', 'diff'),
        step=1,
        max_cells=0,
    )
    top = grids['layers']['top_y']
    diff = grids['layers']['diff']
    med: dict[tuple[int, int], int] = {}
    for x, z in union:
        vals = []
        for dx in range(-2, 3):
            for dz in range(-2, 3):
                xx, zz = x + dx, z + dz
                if not (x0 <= xx <= x1 and z0 <= zz <= z1):
                    continue
                if int(diff[zz - z0, xx - x0]) >= 4:
                    vals.append(int(top[zz - z0, xx - x0]))
        vals.sort()
        med[(x, z)] = vals[len(vals) // 2] if vals else int(top[z - z0, x - x0])
    return med


def split_parts(union: set[tuple[int, int]]) -> dict[str, set[tuple[int, int]]]:
    """Split the footprint into the five labelled plates.

    @param union: readable footprint
    @returns: label → cells (empty labels omitted)
    """
    med = _median_top(union)
    raw = {}
    for cell, height in med.items():
        if height >= 59:
            raw[cell] = 'H'
        elif height >= 55:
            raw[cell] = 'M'
        else:
            raw[cell] = 'L'
    for _ in range(3):
        nxt = {}
        for cell in union:
            votes: collections.Counter[str] = collections.Counter()
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    band = raw.get((cell[0] + dx, cell[1] + dz))
                    if band:
                        votes[band] += 1
            nxt[cell] = votes.most_common(1)[0][0]
        raw = nxt
    parts: dict[str, set[tuple[int, int]]] = {
        'HIGH_C': set(), 'HIGH_W': set(), 'HIGH_E': set(), 'MID': set(), 'LOW': set(),
    }
    for (x, z), band in raw.items():
        if band == 'L':
            parts['LOW'].add((x, z))
        elif band == 'M':
            parts['MID'].add((x, z))
        elif x < 6186:
            parts['HIGH_C'].add((x, z))
        elif x < 6200:
            parts['HIGH_W'].add((x, z))
        else:
            parts['HIGH_E'].add((x, z))
    # Entrance rims take the plate behind them (lidar rim is a storey low) so the
    # photo colours sit on the door wall: white cafe, terracotta concourse, black shops.
    grab = (
        ((6168, 6185, -5582, -5566), 'HIGH_C'),
        ((6186, 6199, -5574, -5558), 'HIGH_W'),
        ((6200, 6226, -5572, -5546), 'HIGH_E'),
    )
    for (x0, x1, z0, z1), label in grab:
        grabbed: set[tuple[int, int]] = set()
        for cells in parts.values():
            hit = {c for c in cells if x0 <= c[0] <= x1 and z0 <= c[1] <= z1}
            cells -= hit
            grabbed |= hit
        parts[label] |= grabbed
    return {label: cells for label, cells in parts.items() if cells}


def bind_doors(build: fb.Build) -> None:
    """Place each portal door once, on the nearest wall of any part.

    ``feature_build`` would repeat the whole door map on every part. Floor Y
    is the floor of the part that owns the wall.

    @param build: feature build after ``__init__``, before ``build()``
    """
    def doors(_k, _f):
        if getattr(build, '_doors_once', False):
            return
        build._doors_once = True
        for feat in build.features:
            feat.door_cells = set()
            feat.door = None
            feat.rear = None
        by_id = {p['id']: p for p in portals.load()}
        reach2 = DOOR_REACH ** 2
        specs = build.spec.get('doors') or {}
        for pid, row in sorted(specs.items()):
            portal = by_id.get(pid)
            if portal is None:
                print(f'door {pid}: unknown portal: skipped')
                continue
            px, pz = portal['x'], portal['z']
            leaves = row['leaves']
            block = row['block']
            best = None
            for feat in build.features:
                for face in fb.DIRS:
                    for cell in build._door_candidates(feat, face):
                        dist2 = (cell[0] - px) ** 2 + (cell[1] - pz) ** 2
                        if best is None or dist2 < best[0]:
                            best = (dist2, cell, face, feat)
            if best is None or best[0] > reach2:
                where = f'{px},{pz}'
                print(f'door {pid} "{portal["label"]}": no wall near {where}: skipped')
                continue
            _dist2, c0, face, feat = best
            lat = (1, 0) if face in 'NS' else (0, 1)
            cells = [c0]
            if leaves == 2:
                opts = [
                    (c0[0] + lat[0], c0[1] + lat[1]),
                    (c0[0] - lat[0], c0[1] - lat[1]),
                ]
                opts.sort(key=lambda c: (c[0] - px) ** 2 + (c[1] - pz) ** 2)

                def framed_pair(other):
                    """True when both leaves and both outer jambs are in this part.

                    @param other: candidate second leaf
                    @returns: whether the pair has a wall cell outside each leaf
                    """
                    if other not in feat.walls or not build.exposed(other, face) or other in feat.door_cells:
                        return False
                    pair = sorted([c0, other], key=lambda c: c[0] * lat[0] + c[1] * lat[1])
                    ends = (
                        (pair[0][0] - lat[0], pair[0][1] - lat[1]),
                        (pair[-1][0] + lat[0], pair[-1][1] + lat[1]),
                    )
                    return all(end in feat.cells for end in ends)

                pair = next((c for c in opts if framed_pair(c)), None)
                if pair is None:
                    print(f'door {pid} "{portal["label"]}": no pair cell; using 1-leaf')
                    leaves = 1
                else:
                    cells = sorted([c0, pair], key=lambda c: c[0] * lat[0] + c[1] * lat[1])
            hinges = ('left',) if leaves == 1 else ('left', 'right')
            for cell, hinge in zip(cells, hinges):
                build._set_door_leaf(cell, face, block, hinge, feat.b)
                feat.door_cells.add(cell)
            if feat.door is None:
                feat.door = cells[0]
            # Solid piers beside the leaves, and a lintel. Windows skip door_cells,
            # so a glazed rhythm cannot eat the frame.
            wall = {
                'HIGH_C': 'white_concrete', 'HIGH_W': 'terracotta', 'HIGH_E': 'black_concrete',
                'MID': 'white_concrete', 'LOW': 'bricks',
            }.get(feat.no, build.wall)
            outer = [
                (cells[0][0] - lat[0], cells[0][1] - lat[1]),
                (cells[-1][0] + lat[0], cells[-1][1] + lat[1]),
            ]
            top = feat.b + feat.storeys * fb.STOREY_H
            for jx, jz in outer:
                if (jx, jz) not in feat.cells:
                    continue
                feat.door_cells.add((jx, jz))
                for y in range(feat.b, top):
                    build.set(jx, y, jz, wall)
            for cell in cells:
                for y in range(feat.b + 3, top):
                    build.set(cell[0], y, cell[1], wall)
            dx, dz = fb.DIRS[face]
            build.step_out((cells[0][0] + dx, cells[0][1] + dz), face, feat.b)
            dist = best[0] ** 0.5
            print(
                f'door {pid} "{portal["label"]}" {leaves}-leaf {block} '
                f'at {cells[0][0]},{feat.b + 1},{cells[0][1]} facing {fb.FACE[face]} '
                f'on {feat.no} ({dist:.1f} from portal)'
            )

    build.doors = doors  # type: ignore[method-assign]


def _interior(cells: set[tuple[int, int]], x: int, z: int) -> bool:
    """True when all four orthogonal neighbours are in ``cells``.

    @param cells: part or plate
    @param x: block X
    @param z: block Z
    @returns: whether the cell is not on the plate edge
    """
    return all((x + dx, z + dz) in cells for dx, dz in ORTH)


def _roof_y(build: fb.Build, label: str) -> int:
    """Slab Y of one part.

    @param build: built feature
    @param label: part label
    @returns: roof Y
    """
    feat = next(f for f in build.features if f.no == label)
    return feat.b + feat.storeys * fb.STOREY_H


def add_greeblies(plan, build: fb.Build, parts: dict[str, set[tuple[int, int]]]) -> dict[str, int]:
    """Parapet plus the street-scale roof bits read off the Google sheet.

    Greeblies replace the roof slab with a full block (or sit on that block).
    They are not placed on slabs, and not on the edge upstand.

    @param plan: site_kit plan
    @param build: feature build after ``build()``
    @param parts: label → cells
    @returns: counts by kind
    """
    counts = {'parapet': 0, 'skylight': 0, 'vent': 0, 'rooflight': 0, 'plant': 0, 'pyramid': 0}
    high: set[tuple[int, int]] = set()
    for label in HIGH_LABELS:
        high |= parts.get(label, set())
    high_y = _roof_y(build, next(label for label in HIGH_LABELS if label in parts))
    plates = {
        'high': (high, high_y),
        'mid': (parts.get('MID', set()), _roof_y(build, 'MID') if 'MID' in parts else 0),
        'low': (parts.get('LOW', set()), _roof_y(build, 'LOW') if 'LOW' in parts else 0),
    }
    for cells, y in plates.values():
        if not cells:
            continue
        for x, z in cells:
            if any((x + dx, z + dz) not in cells for dx, dz in ORTH):
                plan.set(x, y, z, 'light_gray_concrete')
                plan.set(x, y + 1, z, 'light_gray_concrete')
                counts['parapet'] += 1

    def paint(plate: set[tuple[int, int]], cells: set[tuple[int, int]], y: int, block: str, kind: str) -> bool:
        """Replace roof slabs with ``block`` when every cell is interior to ``plate``.

        @param plate: roof plate the cells must sit inside
        @param cells: cells to paint
        @param y: roof Y
        @param block: block id
        @param kind: count key
        @returns: whether the patch was placed
        """
        if not cells or any((x, z) not in plate or not _interior(plate, x, z) for x, z in cells):
            return False
        for x, z in cells:
            plan.set(x, y, z, block)
        counts[kind] += 1
        return True

    if high:
        xs = [c[0] for c in high]
        zs = [c[1] for c in high]
        north = min(zs)
        # Flat skylights just inside the north edge of the high grey roof.
        z_sky = north + 4
        x = min(xs) + 6
        while x + 1 < max(xs) - 4:
            paint(high, {(x, z_sky), (x + 1, z_sky)}, high_y, 'glass', 'skylight')
            x += 7
        cx = (min(xs) + max(xs)) // 2
        cz = (min(zs) + max(zs)) // 2
        for dx, dz in ((-4, 2), (4, 2), (-2, -3), (3, -2)):
            x, z = cx + dx, cz + dz
            if _interior(high, x, z):
                plan.set(x, high_y, z, 'smooth_stone')
                plan.set(x, high_y + 1, z, 'cauldron')
                counts['vent'] += 1
        # Two rectangular rooflights on the south of the high plate.
        south = max(zs)
        z_light = south - 8
        span = max(xs) - min(xs)
        for frac in (0.35, 0.62):
            x0 = min(xs) + int(span * frac)
            cells = {(x0 + dx, z_light + dz) for dx in range(4) for dz in range(3)}
            paint(high, cells, high_y, 'glass', 'rooflight')

    low, low_y = plates['low']
    if low:
        y = low_y
        xs = sorted({c[0] for c in low})
        zs = [c[1] for c in low]
        z_mid = sorted(zs)[len(zs) // 2]
        placed = 0
        for x in xs[4:-4:8]:
            cells = {(x, z_mid), (x + 1, z_mid), (x, z_mid + 1), (x + 1, z_mid + 1)}
            if all(_interior(low, a, b) for a, b in cells):
                for a, b in cells:
                    plan.set(a, y, b, 'gray_concrete')
                counts['plant'] += 1
                placed += 1
            if placed >= 4:
                break

    mid, mid_y = plates['mid']
    if mid:
        y = mid_y
        xs = [c[0] for c in mid]
        zs = [c[1] for c in mid]
        z_row = sorted(zs)[len(zs) // 2]
        placed = 0
        for x in range(min(xs) + 8, max(xs) - 4, 8):
            if not (_interior(mid, x, z_row) and _interior(mid, x + 1, z_row)):
                continue
            plan.set(x, y, z_row, 'glass')
            plan.set(x + 1, y, z_row, 'glass')
            plan.set(x, y, z_row + 1, 'glass')
            plan.set(x + 1, y, z_row + 1, 'glass')
            plan.set(x, y + 1, z_row, 'glass')
            counts['pyramid'] += 1
            placed += 1
            if placed >= 4:
                break
    return counts


def solid_membrane(plan) -> int:
    """Rewrite leftover roof slabs to full blocks.

    A one-block fill of ``light_gray_concrete_slab`` changes nothing on this
    world (the cell stays air or the previous roof). ``light_gray_concrete``
    does place. The parapet upstand sits one block above this membrane.

    @param plan: site_kit plan
    @returns: number of slab cells rewritten
    """
    rewritten = 0
    for (x, y, z), state in list(plan.cells.items()):
        name = state.split('[', 1)[0]
        if not name.endswith('_slab'):
            continue
        plan.set(x, y, z, name[:-5])
        rewritten += 1
    return rewritten


def build_plan():
    """Load the spec, square the footprint, and return the plan plus the build.

    @returns: (plan, build, parts)
    """
    spec = fb.load_spec(SPEC)
    orig, other = osm_cells(WAY)
    union = readable_footprint(orig, other)
    parts = split_parts(union)
    missing = [label for label in spec['ways'].values() if label not in parts]
    if missing:
        raise SystemExit(f'susu_42_r002: empty parts {missing}')
    covered = len(orig & union) / max(1, len(orig))
    print(
        f'footprint orig {len(orig)} readable {len(union)} '
        f'cover {covered:.1%} pad {len(union - orig)} missed {len(orig - union)}'
    )
    for label in ('HIGH_C', 'HIGH_W', 'HIGH_E', 'MID', 'LOW'):
        print(f'  {label} {len(parts[label])} cells')
    built = fb.Build(spec, footprints=parts)
    built.idx = osm_footprints.OSMIndex()
    bind_doors(built)
    original_floor = built.floor_y

    def floor_y(feature):
        """Pinned floor from the ghost-surface sample.

        @param feature: part being floored
        @returns: Y of the ground storey
        """
        if feature.no in FLOORS:
            return FLOORS[feature.no]
        return original_floor(feature)

    built.floor_y = floor_y
    # feature_build's clash filter drops any cell whose recorded state differs from
    # an earlier plan. The ghost save is back, but susu_42 / susu_42_r001 still own
    # these columns in verification/site. That filter does not read ``modifies``
    # (apply does). Ignore those two records while the plan is assembled.
    import site_kit
    previous = {'susu_42', 'susu_42_r001'}
    real_built_cells = site_kit.built_cells

    def built_cells(exclude=(), within=None):
        """Ownership lookup that lets this plan replace the earlier SUSU records.

        @param exclude: build names already ignored by the caller
        @param within: cells to look up
        @returns: ``{(x, y, z): (build, state)}``
        """
        return real_built_cells(exclude=set(exclude) | previous, within=within)

    site_kit.built_cells = built_cells
    try:
        plan = built.build()
    finally:
        site_kit.built_cells = real_built_cells
    counts = add_greeblies(plan, built, parts)
    membrane = solid_membrane(plan)
    print('greeblies', json.dumps(counts), 'membrane_blocks', membrane)
    for feat in built.features:
        roof = feat.b + feat.storeys * fb.STOREY_H
        print(f'roof {feat.no} floor {feat.b} storeys {feat.storeys} slab_y {roof}')
    return plan, built, parts


def main(argv: list[str] | None = None) -> int:
    """Dry-run, or apply when ``--apply`` is passed (``SITE_OWNER`` required).

    @param argv: argument list, defaulting to the process argv
    @returns: exit code
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    spec = json.loads(SPEC.read_text())
    plan, _built, _parts = build_plan()
    out = ROOT / f'verification/site/{spec["name"]}-preview.png'
    preview(plan, out, scale=4)
    print(out)
    print(json.dumps(plan.summary())[:600])
    apply(plan, dry=not args.apply, modifies=tuple(spec.get('modifies') or ()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

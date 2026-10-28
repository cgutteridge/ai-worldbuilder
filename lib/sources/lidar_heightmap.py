#!/usr/bin/env python3
"""Lidar height map from the national composite DTM/DSM, and the building report built from it.

Evidence heights come from the lidar provider only (``lidar_provider.composite_world()``; ADR 003), never from
the live world, so the report is stable across builds and resets. ``heightmap(world, cell)`` also reads a live
``World`` when a caller wants the live surface to compare.

Per column (cell = 128 x 128, arrays indexed [x - x0, z - z0]):
  h      top of the lidar surface: carpet is ignored (it sits ON the lidar), leaves and logs count, because
         where there is no building a tree is log + leaf. Grass tufts and flowers are ignored.
  base   solid surface top under carpet / leaves / logs (DSM-derived wall or object top; under trees the
         bare ground). Not the bare-terrain grade for free ground — see ``terrain``.
  terrain bare-terrain (DTM) ground height when the provider exports it; used by the ground layer for
         free columns so cars / planters / kiosks on the DSM do not raise the road (#172). Absent on
         live column scans; callers fall back to ``base``.
  roof   y of the roof carpet over the column, 0 if none
  kind   0 ground / paving, 1 roof (building lump), 2 tree, 3 tree overhanging a roof,
         4 structure (composite lidar only: tall solid outside any footprint-map polygon)

The lump report groups roof columns into buildings and gives what a feature build needs: footprint,
ground, eave and ridge height (so storeys), flat or pitched, ridge axis, trees over the roof, and
footprint-map ways under it (none = unmapped).

  python3 scripts/lidar_heightmap.py --cell 51,-41            # report
  python3 scripts/lidar_heightmap.py --cell 51,-41 --json     # machine-readable

Source: national composite lidar (composite world module + lidar shim); a cell whose tiles are not cached
raises LookupError (fetch them with the lidar provider first). Trees are first-return crowns.

Lookups that may straddle a cell edge (B42 at z -5505):

  maps = Maps()                     # lidar composite by default; Maps(World()) for live
  maps.top_at(x, z)                 # roof carpet y if there is a roof, else surface h
  top_at(x, z)                      # same, with a module-level Maps cache
"""
from __future__ import annotations

import argparse
import json
import statistics

import numpy as np

import cellmath
from script_env import ROOT  # noqa: E402

TOP = 140
MIN_COLUMNS = 12                    # ignore slivers (bin stores, small sheds)
MIN_CORE = 6                        # columns left after eroding a lump 3 times (~7+ blocks wide) before its interior may judge flat vs pitched
STOREY = 3                          # blocks per storey
RIDGE_BASE = 3                      # ridge height above ground minus this, / STOREY = storeys. Calibrated 2026-10-16 against
                                    # map building:levels on 54 single-building lumps in touched cells: 57% exact, 89%
                                    # within one level (eave height is useless: ~4 above ground whatever the levels)
GROUND, ROOF, TREE, TREE_OVER_ROOF, STRUCTURE = 0, 1, 2, 3, 4
_AIRS = ('minecraft:air', 'minecraft:cave_air', 'minecraft:void_air', 'minecraft:light')
_TREE = ('leaves', '_log', '_wood')
_PLANT = ('short_grass', 'tall_grass', 'fern', 'flower', 'tulip', 'poppy', 'dandelion', 'bush', 'vine', 'daisy',
          'bluet', 'orchid', 'allium', 'cornflower', 'lily', 'sugar_cane', 'mushroom', 'azure', 'peony', 'rose',
          'lilac', 'sunflower', 'dead_bush', 'petals', 'seagrass', 'kelp', '_sign')


def _id(b) -> str:
    return (b or 'minecraft:air').split('[')[0]


def column(world, x: int, z: int, top: int = TOP) -> tuple[int, int, int, int]:
    """Scan one column top-down.

    @param world: object with block(x, y, z) (and optionally uniform_air / prefetch)
    @returns: (h, base, roof, kind); zeros when the column is empty
    """
    skip = getattr(world, 'uniform_air', None)
    # Batch MCP / BlockStore reads for the whole column when the world supports it.
    warm = getattr(world, 'prefetch', None)
    if warm is not None:
        warm((x, y, z) for y in range(top, 0, -1))
    h = base = roof = 0
    tree = False
    y = top
    while y > 0:
        if skip is not None and skip(x, y, z, _AIRS):
            y = ((y >> 4) << 4) - 1
            continue
        b = _id(world.block(x, y, z))
        if b in _AIRS or any(k in b for k in _PLANT):
            pass
        elif b.endswith('_carpet'):
            roof = roof or y
        elif any(k in b for k in _TREE):
            h = h or y
            tree = True
        else:
            h = h or y
            base = y
            break
        y -= 1
    kind = TREE_OVER_ROOF if (tree and roof) else ROOF if roof else TREE if tree else GROUND
    return h, base, roof, kind


def heightmap(world, cell: tuple[int, int]) -> dict:
    """Height map of one cell.

    @param world: a live World, or a world with its own ``heightmap(cell)`` (composite world, ReferenceWorld)
    @param cell: (tx, tz)
    @returns: {'cell', 'x0', 'z0', 'h', 'base', 'roof', 'kind'} (plus ``terrain`` when
        the world provider exports it) with 128x128 int16 / uint8 arrays
    """
    own = getattr(world, 'heightmap', None)
    if own is not None:
        return own(cell)
    x0, z0, _x1, _z1 = cellmath.bounds(cell)
    n = cellmath.CELL
    # Live column scan has no DTM; ground falls back from missing ``terrain`` to ``base`` (#172).
    arr = {k: np.zeros((n, n), dtype=np.int16) for k in ('h', 'base', 'roof')}
    arr['kind'] = np.zeros((n, n), dtype=np.uint8)
    for i in range(n):
        for j in range(n):
            arr['h'][i, j], arr['base'][i, j], arr['roof'][i, j], arr['kind'][i, j] = column(world, x0 + i, z0 + j)
    return {'cell': cell, 'x0': x0, 'z0': z0, **arr}


def top_of(hm: dict, x: int, z: int) -> int:
    """Lidar top Y of one column inside ``hm``: roof carpet where present, else surface h.

    Same rule as lidar_audit.target: a roof carpet marks building tops.
    """
    i, j = x - hm['x0'], z - hm['z0']
    r = int(hm['roof'][i, j])
    return r if r else int(hm['h'][i, j])


class Maps:
    """Cached height maps keyed by cell, for lookups that may straddle cell edges.

    @param world: object with block(x,y,z) (a live World); default is the lidar composite reference
    """

    def __init__(self, world=None):
        self.world = world
        self._hm: dict[tuple[int, int], dict] = {}

    def of(self, cell: tuple[int, int]) -> dict:
        """Height map of one cell, loaded once per Maps instance."""
        if cell not in self._hm:
            if self.world is None:
                self._hm[cell] = reference_world().heightmap(cell)
            else:
                self._hm[cell] = heightmap(self.world, cell)
        return self._hm[cell]

    def top_at(self, x: int, z: int) -> int:
        """Lidar top Y at (x, z): roof carpet if there is a roof, else surface h.

        Loads the cell containing the column (works across cell edges).
        """
        return top_of(self.of(cellmath.cell_of(x, z)), x, z)

    def get(self, x: int, z: int, field: str = 'h') -> int:
        """One array value at (x, z): field is 'h', 'base', 'terrain', 'roof' or 'kind'.

        ``terrain`` is present on composite lidar heightmaps; live column scans
        omit it (KeyError) so ground can fall back to ``base`` (#172).
        """
        hm = self.of(cellmath.cell_of(x, z))
        return int(hm[field][x - hm['x0'], z - hm['z0']])


_DEFAULT_MAPS: Maps | None = None


def top_at(x: int, z: int, maps: Maps | None = None) -> int:
    """Lidar top Y at (x, z) across cell edges (module-level Maps cache of the lidar reference).

    @param x: block X
    @param z: block Z
    @param maps: optional Maps instance (live world, tests); default is a shared lidar Maps
    """
    global _DEFAULT_MAPS
    if maps is not None:
        return maps.top_at(x, z)
    if _DEFAULT_MAPS is None:
        _DEFAULT_MAPS = Maps()
    return _DEFAULT_MAPS.top_at(x, z)


def _group(cols: set) -> list:
    left, out = set(cols), []
    while left:
        seed = min(left)
        comp, todo = {seed}, [seed]
        left.discard(seed)
        while todo:
            x, z = todo.pop()
            for dx, dz in cellmath.N8:
                q = (x + dx, z + dz)
                if q in left:
                    left.discard(q)
                    comp.add(q)
                    todo.append(q)
        out.append(comp)
    return sorted(out, key=lambda c: (-len(c), min(c)))


def _erode(cols: set) -> set:
    """Columns whose 8 neighbours are all in cols (one ring of the rim removed)."""
    return {(x, z) for x, z in cols if all((x + dx, z + dz) in cols for dx, dz in cellmath.N8)}


def _ground(hm: dict, comp: set, reach: int = 3) -> int | None:
    """Median ground top of the non-building columns ringing a lump (inside this cell)."""
    x0, z0 = hm['x0'], hm['z0']
    n = cellmath.CELL
    ring = {(x + dx, z + dz) for x, z in comp for dx in range(-reach, reach + 1)
            for dz in range(-reach, reach + 1)} - comp
    ys = [int(hm['base'][x - x0, z - z0]) for x, z in sorted(ring)
          if 0 <= x - x0 < n and 0 <= z - z0 < n and hm['kind'][x - x0, z - z0] == GROUND and hm['base'][x - x0, z - z0]]
    return int(statistics.median(ys)) if ys else None


def lumps(hm: dict, min_columns: int = MIN_COLUMNS) -> list:
    """Buildings in the height map (connected roof columns), largest first.

    Each item: bbox, centre, columns, ground_y, eave_y (10th percentile of roof y; of the interior columns for a wide lump, see MIN_CORE, because
    lidar blurs the rim low), ridge_y, height (ridge - ground), storeys (estimate: (ridge - ground - RIDGE_BASE)
    / STOREY, at least 1; map_levels is added by report() when the map has building:levels), roof ('flat' | 'pitched'), ridge_axis ('x' | 'z' | None), trees_over (columns) and
    cols (the column set, for footprint-way matching).
    """
    x0, z0 = hm['x0'], hm['z0']
    n = cellmath.CELL
    roofs = {(x0 + i, z0 + j) for i, j in zip(*np.nonzero(hm['roof'])) if hm['roof'][i, j] and i < n and j < n}
    out = []
    for comp in _group(roofs):
        if len(comp) < min_columns:
            continue
        ry = {c: int(hm['roof'][c[0] - x0, c[1] - z0]) for c in comp}
        vals = sorted(ry.values())
        lo, hi = vals[len(vals) // 10], vals[-1]      # eave = 10th percentile: ignores low annex / dormer slivers
        # Lidar blurs the building edge: the rim columns read 3-6 blocks low whatever the roof, so hi - lo over the whole
        # footprint says "pitched" for nearly every building (Oakdene Court, a flat 4-level block, included). A wide enough
        # building is judged on its interior (rim eroded away). A narrow one is not: its interior is just the ridge strip
        # and would read flat, so it keeps the whole-footprint test.
        inner = _erode(set(comp))
        if len(_erode(_erode(inner))) >= MIN_CORE:
            lo = sorted(ry[c] for c in inner)[len(inner) // 10]
            pitched = max(ry[c] for c in inner) - lo >= 2
        else:
            pitched = hi - lo >= 2
        g = _ground(hm, comp)
        xs = [x for x, _ in comp]
        zs = [z for _, z in comp]
        ridge = [c for c, y in ry.items() if y == hi]
        rx = max(x for x, _ in ridge) - min(x for x, _ in ridge)
        rz = max(z for _, z in ridge) - min(z for _, z in ridge)
        out.append({
            'bbox': [min(xs), min(zs), max(xs), max(zs)],
            'centre': [(min(xs) + max(xs)) // 2, (min(zs) + max(zs)) // 2],
            'columns': len(comp), 'ground_y': g, 'eave_y': lo, 'ridge_y': hi,
            'height': hi - g if g is not None else None,
            'storeys': max(1, round((hi - g - RIDGE_BASE) / STOREY)) if g is not None else None,
            'roof': 'pitched' if pitched else 'flat',
            'ridge_axis': ('x' if rx >= rz else 'z') if pitched and len(ridge) > 1 else None,
            'trees_over': sum(1 for c in comp if hm['kind'][c[0] - x0, c[1] - z0] == TREE_OVER_ROOF),
            'cols': comp})
    return out


def map_refs_under(lump: dict, idx) -> list:
    """Vector-map buildings sharing at least a quarter of the smaller footprint (min 4 columns) with the lump.

    @param lump: one lumps() item
    @param idx: osm_footprints.OSMIndex
    @returns: [map way ref]
    """
    from coverage import osm_ref, scan_polygon
    x0, z0, x1, z1 = lump['bbox']
    out = []
    for rings, p in idx.buildings:
        xs = [x for x, _ in rings[0]]
        zs = [z for _, z in rings[0]]
        if max(xs) < x0 or min(xs) > x1 or max(zs) < z0 or min(zs) > z1:
            continue
        fp = set(scan_polygon(rings, (x0 - 2, z0 - 2, x1 + 2, z1 + 2)))
        if fp and len(fp & lump['cols']) >= max(4, min(len(fp), lump['columns']) / 4):
            out.append(osm_ref(p))
    return out


def report(hm: dict, with_osm: bool = True) -> list:
    """lumps() plus footprint-map ways under each (map-ref key empty = not mapped).

    Also attaches ``map_hints`` (roof:shape / roof:levels / height / building:levels)
    as designer hints beside lidar — never an override (#159).
    """
    items = lumps(hm)
    if with_osm:
        from osm_footprints import OSMIndex
        idx = OSMIndex()
        levels = map_levels(idx)
        hints = map_hints(idx)
        for lp in items:
            lp['map_refs'] = map_refs_under(lp, idx)
            lv = {levels[r] for r in lp['map_refs'] if r in levels}
            lp['map_levels'] = sorted(lv)
            merged: dict = {}
            for r in lp['map_refs']:
                for k, v in (hints.get(r) or {}).items():
                    merged.setdefault(k, v)
            if merged:
                lp['map_hints'] = merged
    return items


def _composite_world():
    """Open the cached composite lidar world via the type-facing lidar seam."""
    import lidar_provider as lp
    return lp.composite_world()


class ReferenceWorld:
    """The lidar reference: composite tiles (DTM/DSM) only, per 128x128 cell.

    @param composite: composite lidar world handle (default: shared cache, no downloads)
    """

    def __init__(self, composite=None):
        self._composite = composite

    @property
    def composite(self):
        """Composite lidar world (national tiles where cached)."""
        if self._composite is None:
            self._composite = _composite_world()
        return self._composite

    def uses_composite(self, cell: tuple[int, int]) -> bool:
        """True when composite tiles cover the cell.

        @param cell: (tx, tz)
        @raises LookupError: when the tiles for the cell are not cached
        """
        if self.composite.covers(cell):
            return True
        raise LookupError(
            f'Composite lidar tiles for cell {cell[0]},{cell[1]} are not cached; '
            f'fetch the lidar provider tiles for that cell first')

    def source_of(self, cell: tuple[int, int]) -> str:
        self.uses_composite(cell)
        return 'composite'

    def heightmap(self, cell: tuple[int, int]) -> dict:
        self.uses_composite(cell)
        return self.composite.heightmap(cell)

    def _world(self, x: int, z: int):
        self.uses_composite(cellmath.cell_of(x, z))
        return self.composite

    def block(self, x, y, z):
        return self._world(x, z).block(x, y, z)

    def uniform_air(self, x, y, z, airs) -> bool:
        w = self._world(x, z)
        return w.uniform_air(x, y, z, airs) if hasattr(w, 'uniform_air') else False

    def ground(self, x, z, top=140, bottom=40):
        return self._world(x, z).ground(x, z, top, bottom)


_REFERENCE: ReferenceWorld | None = None


def reference_world() -> ReferenceWorld:
    """The shared lidar reference world (composite DTM/DSM)."""
    global _REFERENCE
    if _REFERENCE is None:
        _REFERENCE = ReferenceWorld()
    return _REFERENCE


def map_levels(idx) -> dict:
    """{way ref: building:levels as int} for footprint polygons that carry the tag."""
    import re
    from coverage import osm_ref
    out = {}
    for _rings, p in idx.buildings:
        m = re.search(r'"building:levels"=>"(\d+)', p.get('other_tags') or '')
        if m:
            out[osm_ref(p)] = int(m.group(1))
    return out


osm_levels = map_levels  # back-compat alias


def map_hints(idx) -> dict:
    """{way ref: map hint tags} for footprints carrying roof/height/levels (#159).

    Hints never override lidar; they sit beside the estimate in the report.

    @param idx: osm_footprints.OSMIndex
    @returns: {ref: {tag: value}}
    """
    from building_evidence import map_building_hints
    from coverage import osm_ref
    out = {}
    for _rings, p in idx.buildings:
        hints = map_building_hints(p)
        if hints:
            out[osm_ref(p)] = hints
    return out


def format_report(cell, items: list, source: str | None = None) -> str:
    """Plain-text table of the building report; ``source`` ('composite') is named in the heading when given."""
    lines = [f'LIDAR LUMPS cell {cell[0]},{cell[1]}: {len(items)} building(s)' + (f' [{source}]' if source else ''),
             'bbox                       cols ground eave ridge st roof     axis trees lvl map']
    for lp in items:
        refs = ','.join(lp.get('map_refs', [])[:3]) or ('UNMAPPED' if 'map_refs' in lp else '-')
        b = lp['bbox']
        lines.append(f'{b[0]},{b[1]}..{b[2]},{b[3]}'.ljust(27)
                     + f'{lp["columns"]:>4} {str(lp["ground_y"]):>6} {lp["eave_y"]:>4} {lp["ridge_y"]:>5} '
                     f'{str(lp["storeys"]):>2} {lp["roof"]:<8} {str(lp["ridge_axis"] or "-"):<4} {lp["trees_over"]:>5} '
                     f'{",".join(map(str, lp.get("map_levels", []))) or "-":>3} {refs}')
        hints = lp.get('map_hints') or {}
        if hints:
            parts = [f'{k}={v}' for k, v in hints.items()]
            lines.append('  map hint (never override lidar): ' + ', '.join(parts))
    return '\n'.join(lines)


def touched_cells() -> list:
    """Cells with any Element started in the cell journal (``CellRegistry.seeds``)."""
    from cell_registry import view_registry
    return view_registry().seeds()


def write_tables() -> int:
    """Write the lump table of every worked cell to site/cells/<tx>_<tz>-LIDAR.txt (regenerable; commit them)."""
    ref = reference_world()
    for cell in touched_cells():
        text = format_report(cell, report(ref.heightmap(cell)), ref.source_of(cell)) + '\n'
        (ROOT / f'site/cells/{cell[0]}_{cell[1]}-LIDAR.txt').write_text(text)
        print(f'{cell[0]},{cell[1]}: {text.splitlines()[0]}')
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cell', help='tx,tz')
    ap.add_argument('--tables', action='store_true', help='write site/cells/<tx>_<tz>-LIDAR.txt for every cell with any Element started in the cell journal')
    ap.add_argument('--json', action='store_true', help='print JSON instead of a table')
    ap.add_argument('--no-osm', action='store_true', help='skip footprint-map match (faster)')
    a = ap.parse_args(argv)
    if a.tables:
        return write_tables()
    if not a.cell:
        ap.error('--cell or --tables is required')
    cell = cellmath.parse_cell(a.cell)
    ref = reference_world()
    hm = ref.heightmap(cell)
    items = report(hm, with_osm=not a.no_osm)
    if a.json:
        print(json.dumps([{k: v for k, v in lp.items() if k != 'cols'} for lp in items], indent=1))
    else:
        print(format_report(cell, items, ref.source_of(cell)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

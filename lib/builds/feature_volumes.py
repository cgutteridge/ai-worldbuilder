"""Volumes outside the map way: ``volumes[label]`` in a feature spec (rules/feature-builds.md).

A map way can stop short of the building (a rear bin store, a single-storey rear
extension). A volume names that mass with a polygon (from the image or the owner) and
lidar confirms it: only polygon columns where the surface stands at least ``mass_m`` above
the ground are added, as a plain mass of its own (walls and its own roof form, no doors,
windows or street face). A column is refused, with its reason in the dry run, when it lies
in another map way, an earlier volume, a column another feature build owns, or has no
lidar mass; a volume that would move the plan's home cell is refused whole.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING
import statistics

import numpy as np

from cellmath import N4, N8, majority_cell
from feature_roof_zones import ZONE_MIN_FOOTPRINT_CELLS
from feature_spec import STOREY_H
from world_frame import y_to_metres_aod


if TYPE_CHECKING:
    from feature_build import Feature


# A one-storey outbuilding stands about 2.5 m; garden fences and hedges mostly stay under this.
VOLUME_LIDAR_MASS_M = 2.0


# Columns listed per refusal reason in the dry run before the list is cut.
REFUSED_SHOWN = 8


def polygon_columns(polygon: list) -> set[tuple[int, int]]:
    """Block columns whose centre lies inside a polygon (even-odd, like zone regions).

    @param polygon: ``[[x, z], ...]`` MC block coordinates
    @returns: set of ``(x, z)``
    """
    from coverage import scan_polygon
    xs = [p[0] for p in polygon]
    zs = [p[1] for p in polygon]
    bbox = (int(np.floor(min(xs))), int(np.floor(min(zs))), int(np.ceil(max(xs))), int(np.ceil(max(zs))))
    return set(scan_polygon([[tuple(p) for p in polygon]], bbox))


def connected_pieces(cells: set) -> list[set]:
    """4-connected pieces of a column set, largest first.

    @param cells: ``(x, z)`` columns
    @returns: list of column sets
    """
    left = set(cells)
    out = []
    while left:
        seed = left.pop()
        piece, todo = {seed}, [seed]
        while todo:
            x, z = todo.pop()
            for dx, dz in N4:
                n = (x + dx, z + dz)
                if n in left:
                    left.discard(n)
                    piece.add(n)
                    todo.append(n)
        out.append(piece)
    return sorted(out, key=lambda p: (-len(p), min(p)))


def refused_line(vno: str, reason: str, cols: list) -> str:
    """One dry-run line naming refused volume columns and why.

    @param vno: volume label
    @param reason: why the columns are refused
    @param cols: refused ``(x, z)`` columns
    @returns: printable line
    """
    cols = sorted(cols)
    shown = ' '.join(f'{x},{z}' for x, z in cols[:REFUSED_SHOWN])
    more = f' (+{len(cols) - REFUSED_SHOWN} more)' if len(cols) > REFUSED_SHOWN else ''
    return f'{vno} volume refused {len(cols)} columns: {reason}: {shown}{more}'


class VolumePlanning:
    """Add ``volumes[label]`` masses outside the map way; used by feature_build.Build."""

    def _feature_build_owners(self, positions: set) -> dict[tuple[int, int], str]:
        """Columns where another real feature build owns a block (ground and decor passes do not count).

        @param positions: ``(x, y, z)`` to look up
        @returns: ``{(x, z): plan}``
        """
        from cell_registry import view_registry
        reg = view_registry()
        owned = reg.owned(positions, exclude={self.name}, real_only=True)
        is_feature: dict[str, bool] = {}
        out: dict[tuple[int, int], str] = {}
        for (x, _y, z), (plan, _state) in owned.items():
            if plan not in is_feature:
                is_feature[plan] = any(e.kind == 'build' and e.pass_ == 'feature' for e in reg.entries(plan=plan))
            if is_feature[plan]:
                out.setdefault((x, z), plan)
        return out

    def prepare_volumes(self) -> None:
        """Turn each ``volumes[label]`` polygon into a Feature of its own, refusing columns with reasons.

        Runs after :meth:`prepare_zones` (the way's readable footprint is final) and before the
        shared margin is computed. Adds the accepted volumes to ``features``, ``groups``,
        ``owner`` and ``by_no_all``; the dry-run lines go to ``_volume_lines``.
        """
        from feature_build import Feature
        from floor_reference import floor_reference
        self._volume_lines = []
        self.volume_cols = set()
        rows = self.spec.get('volumes') or {}
        if not rows:
            return
        way_cols = set(self.owner)
        home = majority_cell(way_cols)
        by_label = {f.no: f for f in self.features}
        for lab, vols in rows.items():
            parent = by_label[lab]
            for i, vol in enumerate(vols):
                vno = f'{lab}.v{i + 1}'
                group = {'nos': [vno], 'mode': 'each', 'front': parent.group['front']}
                roof = {'kind': vol['kind'], 'material': vol.get('material') or parent.roof['material']}
                f = Feature(vno, polygon_columns(vol['polygon']), group, vol['storeys'], roof)
                f.volume_of = lab
                refused: dict[str, list] = defaultdict(list)
                cand = set()
                for c in sorted(f.cells):
                    if c in way_cols:
                        refused[f'inside this build\'s map way (left to {self.features[self.owner[c]].no})'].append(c)
                    elif c in self.foreign:
                        refused['inside another map way'].append(c)
                    elif c in self.volume_cols:
                        refused['inside an earlier volume'].append(c)
                    else:
                        cand.add(c)
                mass_m = float(vol.get('mass_m', VOLUME_LIDAR_MASS_M))
                heights: dict[tuple[int, int], float] = {}
                grids = None
                if cand:
                    f.cells = cand
                    grids = self._load_zone_grids(f)
                    dsm = np.asarray(grids['dsm'], dtype=float)
                    ground = np.asarray(grids['ground_y'])
                    x0, z0 = int(grids['x0']), int(grids['z0'])
                    for c in sorted(cand):
                        zi, xi = c[1] - z0, c[0] - x0
                        if not (0 <= zi < dsm.shape[0] and 0 <= xi < dsm.shape[1]) or not np.isfinite(dsm[zi, xi]):
                            refused['no lidar'].append(c)
                            continue
                        h = float(dsm[zi, xi]) - y_to_metres_aod(int(ground[zi, xi]))
                        if h < mass_m:
                            refused[f'lidar shows no mass (under {mass_m:g} m above ground)'].append(c)
                        else:
                            heights[c] = h
                    cand = set(heights)
                if cand:
                    b = floor_reference(self.name, vno, cand, self.spec['floor_min'], grids)
                    top = b + vol['storeys'] * STOREY_H + 1
                    owners = self._feature_build_owners(
                        {(x, y, z) for x, z in cand for y in range(b, top + 1)})
                    for c, plan in sorted(owners.items()):
                        refused[f'owned by feature build {plan}'].append(c)
                    cand -= set(owners)
                kept = set()
                for piece in connected_pieces(cand):
                    if len(piece) >= ZONE_MIN_FOOTPRINT_CELLS:
                        kept |= piece
                    else:
                        refused[f'piece under {ZONE_MIN_FOOTPRINT_CELLS} columns'].extend(piece)
                if kept:
                    moved = majority_cell(way_cols | self.volume_cols | kept)
                    if moved != home:
                        refused[f'would move the plan\'s home cell from {home[0]},{home[1]} to '
                                f'{moved[0]},{moved[1]} (a build is planned in the cell holding most '
                                'of its columns)'].extend(kept)
                        kept = set()
                for reason, cols in refused.items():
                    self._volume_lines.append(refused_line(vno, reason, cols))
                if not kept:
                    self._volume_lines.append(f'{vno} volume of {lab}: no columns left; not built')
                    continue
                f.cells = kept
                f.b = floor_reference(self.name, vno, kept, self.spec['floor_min'], grids)
                self.features.append(f)
                self.groups.append(group)
                self.volume_cols |= kept
                self.by_no_all[vno] = set(kept)
                self._volume_lines.append(self._volume_summary(f, lab, heights, way_cols))
        self.owner = {}
        for k, f in enumerate(self.features):
            for c in f.cells:
                self.owner[c] = k

    def _volume_summary(self, f: Feature, lab: str, heights: dict, way_cols: set) -> str:
        """Dry-run line for a built volume: columns, form, storeys, floor and roof Y, lidar height.

        @param f: the volume feature
        @param lab: parent way label
        @param heights: lidar mass in metres per candidate column
        @param way_cols: the build's map-way columns
        @returns: printable line
        """
        eave = f.b + f.storeys * STOREY_H
        lidar = statistics.median(heights[c] for c in f.cells)
        touches = any((x + dx, z + dz) in way_cols for x, z in f.cells for dx, dz in N8)
        return (
            f'{f.no} volume of {lab}: {len(f.cells)} columns {f.roof["kind"]} {f.roof["material"]} '
            f'storeys {f.storeys}; floor Y {f.b}, roof Y {eave} ({eave - f.b} blocks); '
            f'lidar mass median {lidar:.1f} m; '
            f'{"touches the map way" if touches else "detached from the map way"}'
        )


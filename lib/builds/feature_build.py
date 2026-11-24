#!/usr/bin/env python3
"""Plan and apply non-house building features.

The spec schema is in rules/feature-builds.md. Validation, lidar roof zones,
doors, facades and details have separate feature_* modules; Build orchestrates
the shared plan, shell and roof dispatch.

Usage: python3 scripts/feature_build.py --world <world> site/builds/<name>.json
       [--apply [--overwrite]] [--landmark-override REASON]
"""

from __future__ import annotations

from collections import Counter
import argparse
import json

from block_store import state_props
from cellmath import DIRS, N4, N8
from feature_details import DetailPlanning
from feature_doors import DoorPlanning
from feature_facade import FacadePlanning
from feature_roof_zones import (
    RoofZonePlanning,
    _bottom_slab_or_full,
    edge_wall_expose,
    roof_material_for_zone,
    roof_zone_colour_lines,
)
from feature_spec import STOREY_H, load_spec
from feature_volumes import VolumePlanning
from roof_geometry import (
    ROOF_DELTA,
    ROOF_FACE_ORDER,
    SlopeStairRule,
    align_stair_shapes,
    edge_distance_field,
    pitched_roof_states,
    regularise_roof_half_heights,
    roof_block_y,
    stair_effect_lines,
)
from roof_material import (
    block_id_names,
    carpet_block,
    family_variants,
    load_catalogue,
    roof_material_warnings,
)
from script_env import ROOT
from site_kit import (
    AIR_IDS,
    Plan,
    apply,
    bid,
    fresh_planning_world,
    guard_door_frames,
    guard_roof_jogs,
    interior_ground_clears,
    is_bottom_half_slab,
    preview,
)
from stoop import GROUND
import footprint_overlap
import land_use
import osm_footprints
import place_config
import stoop


# Full solid cubes a finish carpet may stand on (#455), besides the catalogue's full roof blocks
# and the spec's wall and floor. A positive list: glass, panes, fences, walls, trapdoors, signs,
# lanterns, doors, slabs, stairs and carpets never count.
CARPET_SUPPORT_SUFFIXES = ('_concrete', '_terracotta', '_wool', '_planks', '_bricks', '_tiles')
CARPET_SUPPORT_BLOCKS = frozenset({
    'stone', 'smooth_stone', 'cobblestone', 'terracotta', 'andesite', 'diorite', 'granite',
    'deepslate', 'tuff', 'sandstone', 'red_sandstone', 'bricks',
})


def carpet_support_ok(state: str | None, extra: frozenset | set = frozenset()) -> bool:
    """True when a block is a full solid cube a carpet may stand on.

    @param state: block id or state (with or without ``minecraft:``); None is air
    @param extra: further full-block names (spec wall and floor)
    @returns: whether the block supports a finish carpet
    """
    if not state:
        return False
    name = state.removeprefix('minecraft:').split('[', 1)[0]
    if name in extra or name in CARPET_SUPPORT_BLOCKS or name.endswith(CARPET_SUPPORT_SUFFIXES):
        return True
    return name in {family['full'] for family in load_catalogue()['families'].values()}


class Feature:
    """One labelled footprint mass inside a feature build."""

    def __init__(self, no, cells, group, storeys, roof):
        self.no = no
        self.cells = cells
        self.group = group
        self.storeys = storeys
        self.roof = roof
        self.front = group['front']
        # parent way label when this mass is a ``volumes[label]`` entry outside the way (#456)
        self.volume_of: str | None = None
        # carpet finish (#455): {(x, z): deck row y} whose deck is a wall top course under a carpet
        self.carpet_cells: dict[tuple[int, int], int] = {}


class Build(RoofZonePlanning, VolumePlanning, DoorPlanning, FacadePlanning, DetailPlanning):
    """Plan a non-house feature from a validated spec."""

    def __init__(
        self,
        spec: dict,
        footprints: dict | None = None,
        world=None,
        osm_root=None,
        zone_data: dict | None = None,
        aerial_rgb: dict | None = None,
        building_tags: dict | None = None,
        streets: list | None = None,
    ):
        """
        @param spec: validated feature spec
        @param footprints: optional ``{label: set[(x,z)]}`` (else OSMIndex from ways)
        @param world: optional ``World``-like with ``block(x,y,z)`` (else
            :func:`site_kit.fresh_planning_world` — save then on-disk region files; #111)
        @param osm_root: optional vector-map clip root for ``OSMIndex`` (unit-test /
            fixture trees only). Production always uses this world's
            ``reference/vector_map`` via ``vector_map_clips.vector_map_clip_root``;
            do not point at another world's clips.
        @param zone_data: optional ``{label: {dsm, ground_y, x0, z0}}`` grids for tests (optional ``dsm_fine``: float DSM for pitched half-steps, NaN = no data)
            (arrays indexed ``[z, x]`` / row=z; ``ground_y`` is block Y; ``dsm`` metres AOD).
            Unzoned labels may inject ground_y without dsm. When omitted, zoned labels fetch composite lidar via ``lidar_provider.mc_heights``.
        @param aerial_rgb: optional ``{(x, z): (r, g, b) 0..1}`` for roof-colour
            dry-run lines (tests). When omitted, samples the place aerial mosaic.
        @param building_tags: optional flattened tags by label (synthetic fixtures)
        @param streets: optional ``(highway, points, tags)`` rows (synthetic fixtures)
        """
        self.spec = spec
        self.name = spec['name']
        self.groups = list(spec['groups'])   # volumes append their own groups; the spec stays as loaded
        self.wall = spec['wall']
        self.floor_mat = spec['floor']
        self.keep = set()
        self.p = Plan(self.name)
        self.live = world if world is not None else fresh_planning_world()
        self.zone_data = zone_data or {}
        self.aerial_rgb = aerial_rgb
        by_no = {k: set(v) for k, v in (footprints or {}).items()}
        if footprints is None:
            idx = osm_footprints.OSMIndex(root=osm_root) if osm_root is not None else osm_footprints.OSMIndex()
            for (x, z), i in idx.cells().items():
                bp = idx.buildings[i][1]
                n = self.spec['ways'].get(str(bp.get('osm_way_id') or bp.get('osm_id')))
                if n:
                    by_no.setdefault(n, set()).add((x, z))
            self.idx = idx
        else:
            self.idx = None
        self.building_tags = dict(building_tags or {})
        self.land_uses = {}
        if self.idx is not None:
            for rings, props in self.idx.buildings:
                label = spec['ways'].get(str(props.get('osm_way_id') or props.get('osm_id')))
                if label is None:
                    continue
                tags = osm_footprints.osm_tags(props)
                self.building_tags.setdefault(label, tags)
                if getattr(self.idx, 'areas', None):
                    from coverage import building_land_use
                    cls = building_land_use(self.idx, rings, props)['class']
                else:
                    cls = land_use.classify_building(tags)['class']
                self.land_uses[label] = cls
        self.streets = (streets if streets is not None else
                        footprint_overlap.streets_from_index(self.idx) if self.idx is not None and
                        hasattr(self.idx, 'streets') else [])
        self.streets = [(highway, pts, osm_footprints.osm_tags(tags))
                        for highway, pts, tags in self.streets]
        self.excluded: set = set()
        for lab, boxes in (spec.get('exclude') or {}).items():
            if lab in by_no:
                kept = {c for c in by_no[lab]
                        if not any(b[0] <= c[0] <= b[2] and b[1] <= c[1] <= b[3] for b in boxes)}
                self.excluded |= by_no[lab] - kept
                by_no[lab] = kept
        missing = [lab for lab in spec['ways'].values() if lab not in by_no or not by_no[lab]]
        if missing:
            raise SystemExit(f'{self.name}: no footprint cells for {missing}')
        self.features = []
        for g in self.groups:
            for no in g['nos']:
                self.features.append(Feature(no, by_no[no], g, spec['storeys'][no], spec['roofs'][no]))
        self.owner = {}
        for k, f in enumerate(self.features):
            for c in f.cells:
                self.owner[c] = k
        self.by_no_all = by_no
        self._zone_pad_lines: list[str] = []
        self._roof_warnings: list[str] = []
        self._volume_lines: list[str] = []
        self.volume_cols: set = set()
        self._window_lines: list[str] = []
        self._detail_clip_lines: list[str] = []
        self._pv_lines: list[str] = []
        self._entrance_covers: list[tuple] = []
        # Stamp against every cell the footprint covers so neighbour prepares elsewhere
        # do not invalidate this plan, while cross-cell geometry still counts (task 232).
        import cellmath
        self.p.capture_inputs({cellmath.cell_of(x, z) for cols in by_no.values() for x, z in cols})

    def set(self, x, y, z, s):
        self.p.set(x, y, z, s)

    def _set_roof(self, x, y, z, s):
        """Set a roof-membrane cell.

        Bottom-half slabs are exempted from the apply rewrite and get a wall
        block under them when the cell below is air (#209).

        @param x: block x
        @param y: block y
        @param z: block z
        @param s: roof block state
        """
        if is_bottom_half_slab(s) and not self._is_solid(x, y - 1, z):
            # a bottom-half slab never floats: wall under it when the cell below is air
            self.p.set(x, y - 1, z, self.wall)
        self.p.set(x, y, z, s)
        if is_bottom_half_slab(s):
            self.p.keep_slab_cells.add((x, y, z))

    def _is_solid(self, x: int, y: int, z: int) -> bool:
        """True when the finished plan (or live world) has a non-air block here.

        @param x: block x
        @param y: block y
        @param z: block z
        @returns: whether the cell is a framing solid (not air)
        """
        st = self.p.cells.get((x, y, z))
        if st is None:
            st = self.live.block(x, y, z)
        return bool(st) and bid(st)[10:] not in AIR_IDS

    def kset(self, x, y, z, st):
        """Set a stoop/step cell; with keep_edits it survives the earlier-build clash filter."""
        if self.spec['keep_edits']:
            self.keep.add((x, y, z))
        self.set(x, y, z, st)

    def _prefetch_live(self, positions) -> None:
        """Warm ``self.live`` for a known set of positions when it supports prefetch.

        Skips positions an overlay world (``PlannedWorld``) already serves from ``.cells``.
        No-op for stubs without ``prefetch``. Call only during plan construction — after
        ``mark_written`` live stores raise ``StaleRead``.

        @param positions: iterable of ``(x, y, z)``
        """
        warm = getattr(self.live, 'prefetch', None)
        if warm is None:
            return
        overlay = getattr(self.live, 'cells', None)
        if overlay is not None:
            positions = (p for p in positions if p not in overlay)
        warm(positions)

    def floor_y(self, f: Feature) -> int:
        """Median lidar bare-terrain floor under the footprint, with floor_min override.

        @param f: feature whose footprint samples the DTM
        @returns: median ground block Y, raised by floor_min when specified
        """
        from floor_reference import floor_reference
        return floor_reference(self.name, f.no, f.cells, self.spec['floor_min'], self.zone_data.get(f.no))

    def _feature_has_zones(self, f: Feature) -> bool:
        """True when this label has a zones entry in the spec."""
        return f.no in (self.spec.get('zones') or {})


    def clear(self):
        cols = set()
        for (x, z) in self.owner:
            cols.update((x + dx, z + dz) for dx in (-2, -1, 0, 1, 2) for dz in (-2, -1, 0, 1, 2))
        self.cols = cols
        tops = []
        for f in self.features:
            if getattr(f, 'zone_roof', None):
                tops.append(max(f.zone_roof.values()) + 4)
            else:
                tops.append(f.b + f.storeys * STOREY_H + 8)
        top_clear = max(tops)
        work = []
        for x, z in cols:
            nearest = min(self.features, key=lambda f: min(max(abs(x - c[0]), abs(z - c[1])) for c in f.cells))
            if (x, z) in self.owner and getattr(nearest, 'zone_roof', None):
                continue  # shell_zoned owns these columns
            if (x, z) in self.foreign or (x, z) in self.shared:
                continue
            work.append((x, z, nearest.b, (x, z) in self.owner))
        need = []
        for x, z, b, own in work:
            if not own:
                need.extend((x, y, z) for y in range(b, top_clear + 1))
        self._prefetch_live(need)
        for x, z, b, own in work:
            if own:
                for y in range(place_config.base_y(), b):
                    self.set(x, y, z, 'stone')
                rng = range(b, top_clear + 1)
            else:
                # Margin: clear only — no stone shelf at floor level (#115).
                # Door stoops are placed later by step_out / _set_door_leaf.
                rng = range(b, top_clear + 1)
            for y in rng:
                if own or (self.live.block(x, y, z) or 'minecraft:air').split('[')[0] not in GROUND:
                    self.set(x, y, z, 'air')

    def exposed(self, c, d) -> bool:
        dx, dz = DIRS[d]
        return (c[0] + dx, c[1] + dz) not in self.owner

    def shell(self, k: int, f: Feature):
        b = f.b
        own = f.cells
        height = f.storeys * STOREY_H
        f.walls = set()
        for c in own:
            for dx, dz in N8:
                n = (c[0] + dx, c[1] + dz)
                ko = self.owner.get(n)
                if ko is None or (ko != k and k < ko):
                    f.walls.add(c)
                    break
        f.inner = own - f.walls
        for x, z in own:
            for y in range(b - 4, b):
                self.set(x, y, z, 'stone')
        for x, z in f.walls:
            for y in range(b, b + height):
                self.set(x, y, z, self.wall)
        for x, z in f.inner:
            self.set(x, b, z, self.floor_mat)
            for y in range(b + 1, b + height):
                self.set(x, y, z, 'air')
            for s in range(1, f.storeys):
                self.set(x, b + s * STOREY_H, z, self.floor_mat)

    def shell_zoned(self, k: int, f: Feature) -> None:
        """Per-column shell from local lidar ground up to snapped roof zones.

        Walls only where a neighbour top is lower (exterior or zone step).
        Flat zones place a full-block deck at the plateau Y; pitched zones place
        stair / slab / full-block membrane (``zone_roof_half`` via
        ``pitched_roof_states``) rising from the eave toward a lidar ridge.
        Optional ``f.roof['edge']`` places a parapet (default: none).
        Optional ``f.roof['finish']`` lays a carpet on flat deck columns
        (:meth:`_carpet_columns`); an edge block wins over the carpet.
        Interior floor slabs follow the facade storey rhythm from ``f.b``
        (median ground), strictly below each column's eave.

        @param k: feature index (ownership / wall rivalry)
        @param f: feature with ``zone_roof`` / ``zone_eave`` / ``zone_ground`` filled
        """
        del k  # ownership already baked into f.cells; kept for call-site parity
        roof = f.zone_roof
        halfs = getattr(f, 'zone_roof_half', None)
        gy = f.zone_ground
        zl = getattr(f, 'zone_level', None) or {}
        material_levels = getattr(f, 'roof_material_level', zl)
        edge = f.roof.get('edge')
        datum = f.b
        pitched_states: dict[tuple[int, int], str] | None = None
        if halfs is not None:
            mats = {
                c: roof_material_for_zone(f.roof, material_levels.get(c)) for c in halfs
            }
            rule = self._slope_stair_rule(f) if f.roof.get('stairs') == 'slope' else None
            pitched_states = pitched_roof_states(
                halfs, mats, warnings=self._roof_warnings, levels=zl,
                flat_cells=getattr(f, 'zone_flat_cells', None), stair_ok=rule,
            )
            if rule is not None:
                self._zone_pad_lines.append(rule.summary(f.no))

        def top_of(q: tuple[int, int]) -> int:
            return roof[q] if q in roof else gy.get(q, -999)

        def wants_edge(x: int, z: int, ry: int) -> bool:
            if not edge:
                return False
            if edge['where'] == 'all':
                return any(top_of((x + dx, z + dz)) < ry for dx, dz in N4)
            # outer: only columns that touch outside the roof mass (skip zone steps)
            return any((x + dx, z + dz) not in roof for dx, dz in N4)

        f.walls = set()
        for c, ry in roof.items():
            if any(top_of((c[0] + dx, c[1] + dz)) < ry for dx, dz in N4):
                f.walls.add(c)
            # also treat N8 ownership edge as wall for doors/facades
            for dx, dz in N8:
                if (c[0] + dx, c[1] + dz) not in roof:
                    f.walls.add(c)
                    break
        f.inner = set(roof) - f.walls
        carpets = self._carpet_columns(f, material_levels)
        for (x, z), ry in roof.items():
            g = gy[(x, z)]
            expose = edge_wall_expose(roof, gy, (x, z))
            rm = roof_material_for_zone(f.roof, material_levels.get((x, z)))
            for y in range(g - 4, g):
                self.set(x, y, z, 'stone')
            self.set(x, g, z, self.floor_mat)
            for y in range(g + 1, ry):
                if y > expose:
                    self.set(x, y, z, self.wall)
                elif (y - datum) % STOREY_H == 0 and y < f.zone_eave[(x, z)]:
                    self.set(x, y, z, self.floor_mat)
                else:
                    self.set(x, y, z, 'air')
            if (x, z) in carpets and not wants_edge(x, z, ry):
                self._lay_carpet(f, (x, z), ry, carpets[(x, z)])
                if pitched_states is not None:
                    pitched_states[(x, z)] = self.wall
                continue
            if pitched_states is not None:
                self._set_roof(x, ry, z, pitched_states[(x, z)])
            else:
                self.set(x, ry, z, family_variants(rm)['full'])
            if wants_edge(x, z, ry):
                self.set(x, ry + 1, z, edge['block'])
            else:
                self.set(x, ry + 1, z, 'air')
        if pitched_states is not None:
            self._back_roof_stair_edges(roof, pitched_states)

    @staticmethod
    def _carpet_columns(f: Feature, material_levels: dict) -> dict[tuple[int, int], str]:
        """Zoned deck columns that take the ``roofs[label].finish`` carpet, with their carpet block.

        Flat roofs only (#455): every column of a flat roof; on a pitched roof only the
        columns lidar reads as flat (``zone_flat_cells``) whose fitted surface, when there is
        one, is flat, and whose membrane is a full-block height (a slab-height column keeps
        its slab: a full block there would raise the roof half a block). Never a slope.

        @param f: zoned feature (``zone_roof`` filled)
        @param material_levels: per-column roof material key for ``roof_material_for_zone``
        @returns: ``{(x, z): carpet block}``; empty without a finish
        """
        finish = f.roof.get('finish')
        if not finish:
            return {}
        halfs = getattr(f, 'zone_roof_half', None)
        if f.roof['kind'] == 'flat' or halfs is None:
            cols = set(f.zone_roof)
        else:
            surfaces = getattr(f, 'zone_surface', None) or {}
            cols = {
                c for c in (getattr(f, 'zone_flat_cells', None) or ())
                if c in f.zone_roof and halfs.get(c, 1) % 2 == 0
                and (c not in surfaces or surfaces[c].kind == 'flat')
            }
        return {
            c: carpet_block(roof_material_for_zone(f.roof, material_levels.get(c)), finish)
            for c in cols
        }

    def _lay_carpet(self, f: Feature, cell: tuple[int, int], deck_y: int, carpet: str) -> None:
        """Turn a deck cell into a wall top course with a carpet on it (#455).

        The deck row takes the wall material (a shopfront later writes its fascia there)
        and the carpet sits one row up, so the roof changes material without gaining height.

        @param f: feature that owns the column
        @param cell: ``(x, z)``
        @param deck_y: the deck row y
        @param carpet: carpet block name
        """
        self.set(cell[0], deck_y, cell[1], self.wall)
        self.set(cell[0], deck_y + 1, cell[1], carpet)
        f.carpet_cells[cell] = deck_y

    def _check_carpet_support(self) -> list[str]:
        """Drop any finish carpet that no longer stands on a full solid block (#455).

        Later steps (street face, prunes, real-owner cells) may change the deck row; a
        carpet stays only on a full solid cube (:func:`carpet_support_ok`).

        @returns: one dry-run line per feature with a carpet finish
        """
        lines = []
        for f in self.features:
            if not f.carpet_cells:
                continue
            kept = dropped = 0
            for (x, z), deck_y in sorted(f.carpet_cells.items()):
                if '_carpet' not in self.p.cells.get((x, deck_y + 1, z), ''):
                    continue
                below = self.p.cells.get((x, deck_y, z)) or self.live.block(x, deck_y, z)
                if carpet_support_ok(below, {self.wall, self.floor_mat}):
                    kept += 1
                    continue
                self.set(x, deck_y + 1, z, 'air')
                dropped += 1
            lines.append(
                f'{f.no} roof finish {f.roof["finish"]}: carpet {kept} cells on solid blocks; '
                f'dropped {dropped} without a solid block below'
            )
        return lines

    @staticmethod
    def _slope_stair_rule(f: Feature) -> SlopeStairRule:
        """Stair rule for ``roofs[label].stairs: "slope"``: rim or fitted slope only (#434).

        @param f: zoned pitched feature (``zone_roof``, ``zone_flat_cells``, ``zone_volume``,
            ``zone_surface`` filled by ``prepare_zones``)
        @returns: the rule to pass to ``pitched_roof_states``
        """
        import roof_surface
        surfaces = getattr(f, 'zone_surface', None) or {}

        def rise(c, facing):
            surface = surfaces.get(c)
            if surface is None:
                return None
            gx, gz = surface.gradient(*c)
            dx, dz = ROOF_DELTA[facing]
            return gx * dx + gz * dz

        return SlopeStairRule(
            f.zone_roof, flat=getattr(f, 'zone_flat_cells', None),
            volume=getattr(f, 'zone_volume', None), rise=rise, min_rise=roof_surface.MIN_SLOPE,
        )

    def _back_roof_stair_edges(self, roof: dict, states: dict) -> None:
        """Close horizontal slits beside lower stairs inside a zoned roof mass.

        ``edge_wall_expose`` compares whole-block tops. At the lowest adjacent
        roof Y it can leave air below a taller column, even when a lower stair's
        upper half opens into that air. Back only those openings with wall.
        A storey floor block sitting at the stair's Y in the taller column is
        also swapped for wall: left in place it shows as a light-grey square
        through the stair's open half (#310, I-022 S-087). Other blocks and
        exterior air are left alone.

        @param roof: membrane block Y per owned roof column
        @param states: membrane states (after same-Y stair shape derivation)
        """
        for (x, z), state in states.items():
            if '_stairs[' not in state:
                continue
            props = state_props(state)
            facing = props['facing']
            shape = props.get('shape', 'straight')
            # A straight stair's upper half fills its facing side. Inner
            # corners also fill their left/right side; outer corners fill
            # only a quadrant, so every side has an open upper part.
            closed = {facing}
            if shape.startswith('outer_'):
                closed = set()
            elif shape.startswith('inner_'):
                turn = -1 if shape.endswith('_left') else 1
                closed.add(ROOF_FACE_ORDER[(ROOF_FACE_ORDER.index(facing) + turn) % 4])
            y = roof[(x, z)]
            for face, (dx, dz) in ROOF_DELTA.items():
                neighbour = (x + dx, z + dz)
                if face in closed or roof.get(neighbour, y) <= y:
                    continue
                pos = (neighbour[0], y, neighbour[1])
                if self.p.cells.get(pos) in ('minecraft:air', f'minecraft:{self.floor_mat}'):
                    self.set(*pos, self.wall)

    def top_solid(self, x, z, near):
        return stoop.top_solid(self.live, x, z, near)

    def step_out(self, st, d, b):
        stoop.step_out(self, st, d, b, self.kset, prefetch=self._prefetch_live)


    def roof_flat(self, features: list[Feature], own: set):
        """Flat roof membrane; optional overhang fringe when ``edge`` is set.

        Membrane uses ``roofs[].material`` slabs on owned columns. The one-block
        N8 overhang fringe is built only when ``roofs[].edge`` is present
        (default none). Pitched overhang is unchanged in ``roof_pitched``.
        With ``roofs[].finish`` and a full-block material each owned deck cell is a wall
        top course under a carpet (:meth:`_lay_carpet`); the fringe stays roof slab. A slab
        material refuses the finish (it would raise the roof half a block).

        @param features: group masses sharing this roof
        @param own: owned (x, z) columns for the roof deck
        """
        rm = features[0].roof['material']
        eave = features[0].b + features[0].storeys * STOREY_H
        finish = features[0].roof.get('finish')
        if finish and 'slab' in family_variants(rm):
            # the slab deck tops out half a block lower than a full top course: never raise it
            self._roof_warnings.append(
                f'{features[0].no} roof finish {finish}: refused; carpet on an unzoned slab-deck flat roof '
                f'would raise it half a block; zone the roof or use a full-block roof material')
        elif finish:
            carpet = carpet_block(rm, finish)
            for f in features:
                for x, z in f.cells:
                    self._lay_carpet(f, (x, z), eave, carpet)
        rs = set(own)
        if features[0].roof.get('edge'):
            for (x, z) in own:
                for dx, dz in N8:
                    n = (x + dx, z + dz)
                    if n not in self.owner and n not in self.foreign and n not in self.shared:
                        rs.add(n)
        # carpeted columns keep their wall top course; the overhang fringe stays roof slab
        rs -= {c for f in features for c in f.carpet_cells}
        for x, z in rs:
            self.set(x, eave, z, _bottom_slab_or_full(rm))

    def roof_pitched(self, features: list[Feature], own: set):
        """Hip roof from the footprint edge, with slab or opposed-stair ridge caps.

        Chebyshev distance from the roof outline sets a one-block rise per step
        (45°). ``pitched_roof_states`` picks stair facing/shape, slabs and full
        blocks for the roof material family.

        @param features: group masses sharing this roof
        @param own: owned (x, z) columns for the roof deck
        """
        rm = features[0].roof['material']
        b = features[0].b
        eave = b + features[0].storeys * STOREY_H
        dist = edge_distance_field(own, set(self.owner) | self.foreign | self.shared)
        if features[0].roof.get('frame') == 'plot' and self._roof_plot_hip(features[0], own, set(dist), eave):
            return
        half_heights = regularise_roof_half_heights(
            {c: 2 * (eave + d) for c, d in dist.items()},
        )
        materials = {c: rm for c in dist}
        states = pitched_roof_states(
            half_heights, materials, warnings=self._roof_warnings,
        )
        for (x, z), st in states.items():
            y = roof_block_y(half_heights[(x, z)])
            if (x, z) in self.owner:
                for yy in range(eave, y):
                    self.set(x, yy, z, self.wall)
            self._set_roof(x, y, z, st)

    def _roof_plot_hip(self, f: Feature, own: set, columns: set, eave: int) -> bool:
        """Hip designed in the plot frame for ``roofs[label].frame: "plot"`` (#463; ``roof_plot_hip``).

        @param f: the roof's (first) feature
        @param own: owned (x, z) columns of the roof
        @param columns: footprint plus eave ring
        @param eave: eave block Y
        @returns: False when the footprint is not close to a rectangle (the caller builds the default hip)
        """
        from roof_plot_hip import design_plot_hip, exposed_backing, plot_hip_states
        hip = design_plot_hip(own, columns, eave)
        if hip is None:
            self._roof_warnings.append(
                f'{f.no} roof frame plot: footprint is not close to a rectangle; using the default hip')
            return False
        rm = f.roof['material']
        states = plot_hip_states(hip, rm)
        backing = exposed_backing(hip)
        full = family_variants(rm)['full']
        for (x, z), st in states.items():
            y = roof_block_y(hip.half_heights[(x, z)])
            if (x, z) in self.owner:
                for yy in range(eave, y):
                    self.set(x, yy, z, full if (x, yy, z) in backing else self.wall)
            self._set_roof(x, y, z, st)
        stairs = sum('_stairs[' in st for st in states.values())
        self._roof_warnings.append(
            f'{f.no} roof frame plot: rectangle at {hip.angle} degrees (fill {hip.fill:.2f}); '
            f'{stairs} stair(s), every face one facing')
        return True

    def roof(self):
        todo = []
        for g in self.groups:
            hs = [f for f in self.features if f.group is g and not getattr(f, 'zone_roof', None)]
            if not hs:
                continue
            if g['mode'] == 'group':
                todo.append((hs, set().union(*[f.cells for f in hs])))
            else:
                todo.extend(([f], f.cells) for f in hs)
        for hs, own in todo:
            kind = hs[0].roof['kind']
            if kind == 'flat':
                self.roof_flat(hs, own)
            else:
                self.roof_pitched(hs, own)

    def _prune_outside_guard_hits(self) -> None:
        """Drop outside fringe the vector-map guard flags, without wiping door stoops.

        Roof-overhang slabs are always removed. When a label is zoned, non-slab
        hits on columns owned by those zoned features are also removed (expanded
        readable mass the clip index may not cover). Stoops sit one step outside
        the mass, so they are kept — zoning must not delete every outside block.

        @returns: None
        """
        zoned_cols = {
            c for f in self.features
            if getattr(f, 'zone_roof', None)
            for c in f.cells
        }
        for q, v in osm_footprints.guard(self.p.cells)['outside'].items():
            if (q[0], q[2]) in self.volume_cols:
                continue  # a volume is outside the map way by design; its roof and walls stay
            name = v[0]
            if 'slab' in name or '_stairs' in name:
                del self.p.cells[q]
            elif (q[0], q[2]) in zoned_cols:
                del self.p.cells[q]

    def _foreign_cells(self) -> set:
        """Map cells of other buildings (not this build's, not clipped off by ``exclude``).

        @returns: cells to treat as someone else's footprint; empty without a map index
        """
        if self.idx is None:
            return set()
        return {c for c in self.idx.cells() if c not in self.owner and c not in self.excluded}

    def build(self) -> Plan:
        """Produce the offline plan for this feature build.

        @returns: ``site_kit.Plan``
        """
        self.foreign = self._foreign_cells()
        self.by_no_all_cells = set(self.owner) | self.foreign
        for f in self.features:
            if not self._feature_has_zones(f):
                f.b = self.floor_y(f)
        self.prepare_zones()
        self.prepare_volumes()
        self.by_no_all_cells = set(self.owner) | self.foreign
        self.shared = {(x + dx, z + dz) for (x, z) in self.foreign for dx, dz in N8} - self.foreign - set(self.owner)
        for g in self.groups:
            hs = [f for f in self.features if f.group is g and not getattr(f, 'zone_roof', None)]
            if g['mode'] == 'group' and hs:
                bb = min(f.b for f in hs)
                for f in hs:
                    f.b = bb
        self.clear()
        for k, f in enumerate(self.features):
            if getattr(f, 'zone_roof', None):
                self.shell_zoned(k, f)
            else:
                self.shell(k, f)
        self._window_lines = []
        for k, f in enumerate(self.features):
            if not f.volume_of:
                self.doors(k, f)
        # Roof before windows so the eave slab can lintel the top glazed row
        # (opening checks need the finished lintel above the top glazed row).
        self.roof()
        for f in self.features:
            if not f.volume_of:
                self.windows(f)
        if self.idx is not None:
            self._prune_outside_guard_hits()
        own1 = {(x + dx, z + dz) for (x, z) in self.owner for dx in (-1, 0, 1) for dz in (-1, 0, 1)}
        leaf_q = [q for q in self.p.cells if (q[0], q[2]) not in own1]
        self._prefetch_live(leaf_q)
        for q in list(self.p.cells):
            if (q[0], q[2]) not in own1 and 'leaves' in (self.live.block(*q) or ''):
                del self.p.cells[q]
        self.p.outside_footprint_ok = 'roof-overhang fringe slabs; evidence: footprint-hits json'
        if self.volume_cols:
            self.p.outside_footprint_ok += (
                '; volumes outside the map way from spec polygons with lidar mass: '
                + ', '.join(f.no for f in self.features if f.volume_of))
        # Door opening clears (#143) and keep_edits stoops: survive both this filter and apply clash.
        self.keep.update(interior_ground_clears(
            self.p, {c: self._column_floor(f, c) for f in self.features for c in f.inner}))
        self.p.keep = set(self.keep)
        self._preserve_real_owners()
        # same refusals as the live apply, so the designer sees them before hand-off (#99)
        guard_door_frames(self.p, self.live.block)
        guard_roof_jogs(self.p)
        # Greeblies after the outside-slab prune so awning_like/ledge overhangs survive.
        self.details()
        self.pv_rows()
        self.entrance_covers()
        for f in self.features:
            if not f.volume_of:
                self.street_face(f)
        # Shop doors are placed with the street face, after the earlier frame check.
        guard_door_frames(self.p, self.live.block)
        self.p.keep = set(self.keep)
        self._preserve_real_owners()
        # Last word on stair shapes: later steps (pv rows, entrance covers, the street face, the
        # real-owner prune) may have added or removed stairs, so shape them all once more (#415).
        for (x, y, z), state in align_stair_shapes(
                self.p.cells, self.live.block, getattr(self.live, 'prefetch', None)).items():
            if self.p.cells.get((x, y, z)) != state:
                self.set(x, y, z, state)
        carpet_lines = self._check_carpet_support()
        for line in stair_effect_lines(self.p.cells, self.live.block):
            print(line)
        self.p.signs = [row for row in self.p.signs if '_wall_sign[' in self.p.cells.get(row[0], '')]
        for line in self._zone_pad_lines:
            print(line)
        for line in carpet_lines:
            print(line)
        for line in self._volume_lines:
            print(line)
        for line in self._roof_warnings:
            print(line)
        for f in self.features:
            bits = [
                f.no, 'b', f.b, 'storeys', f.storeys, 'cells', len(f.cells),
                'door', getattr(f, 'door', None), 'rear', getattr(f, 'rear', None),
            ]
            if getattr(f, 'zone_roof', None):
                bits.append(f'zones={sorted(set(f.zone_level.values()))}')
            print(*bits)
        for line in self._window_lines:
            print(line)
        for line in self.glazed_share_report():
            print(line)
        for line in roof_zone_colour_lines(self.features, aerial_rgb=self.aerial_rgb):
            print(line)
        for line in self.detail_report():
            print(line)
        for line in self._pv_lines:
            print(line)
        for line in self.footprint_overlap_report():
            print(line)
        # Material warnings are listed here, beside the unknown-id check.
        # The spec is not rewritten; a pitched roof with no triad still
        # places the full block (pitched_roof_states).
        for f in self.features:
            for line in roof_material_warnings(f.roof):
                print(line)
        valid_ids = block_id_names()
        counts = Counter(state.split('[', 1)[0] for state in self.p.cells.values())
        for name, count in sorted(counts.items()):
            if name.removeprefix('minecraft:') not in valid_ids:
                print(f'unknown block id {name} x{count} (source: vendored Minecraft 26.2 client blockstates; config/minecraft-block-ids-26.2.txt)')
        # Re-stamp from the finished geometry so roof overhangs into neighbours are
        # part of the plan identity apply will check (task 232).
        self.p.capture_inputs()
        return self.p

    def _preserve_real_owners(self) -> None:
        """Keep real-build cells intact after shell and late detail placement."""
        # cells of earlier real builds are theirs (ghost owners yield: the shell overwrites them)
        from cell_registry import view_registry
        owned = view_registry().owned(self.p.cells, exclude={self.name}, real_only=True)
        for q in [q for q, st in self.p.cells.items() if q in owned and owned[q][1] != st and q not in self.keep]:
            del self.p.cells[q]

    def footprint_overlap_report(self) -> list[str]:
        """Path / open-lidar / aerial overlap lines for each feature mass (dry-run).

        Uses the live vector-map index when present. Synthetic ``footprints=`` builds (unit
        tests) skip unless streets were injected via ``self._overlap_streets``.

        @returns: printable report lines (may be empty)
        """
        streets = getattr(self, '_overlap_streets', None)
        if streets is None and self.idx is not None:
            streets = footprint_overlap.streets_from_index(self.idx)
        if streets is None:
            return []
        lines: list[str] = []
        for f in self.features:
            lines.extend(footprint_overlap.report_for_cells(
                set(f.cells), streets=streets, label=str(f.no),
            ))
        return lines


def build(
    spec: dict,
    footprints: dict | None = None,
    world=None,
    osm_root=None,
    zone_data: dict | None = None,
    aerial_rgb: dict | None = None,
) -> Plan:
    """Build a feature plan from a validated spec.

    @param spec: validated feature spec
    @param footprints: optional label→cells map (tests / offline)
    @param world: optional World-like
    @param osm_root: optional clip root (tests / fixtures only; not cross-world)
    @param zone_data: optional lidar grids for zoned labels (tests)
    @param aerial_rgb: optional column RGB map for roof-colour dry-run lines (tests)
    @returns: Plan
    """
    return Build(
        spec, footprints=footprints, world=world, osm_root=osm_root,
        zone_data=zone_data, aerial_rgb=aerial_rgb,
    ).build()


def landed_build_seqs(name: str) -> list[int]:
    """Journal seqs of live (landed, not undone) builds recorded under plan ``name``.

    @param name: plan name
    @returns: seq numbers, empty when the plan has no live build
    """
    import cell_registry
    return [e.seq for e in cell_registry.view_registry().entries(plan=name)
            if e.kind == 'build' and e.status == 'landed' and not e.undone]


def run(
    spec_path,
    do_apply: bool = False,
    overwrite: bool = False,
    footprints: dict | None = None,
    world=None,
    osm_root=None,
    zone_data: dict | None = None,
    aerial_rgb: dict | None = None,
    landmark_override: str = '',
) -> Plan:
    """Load spec, build plan, preview, and site_kit.apply (dry unless do_apply).

    @param spec_path: path to site/builds/<name>.json
    @param do_apply: when True, live apply; otherwise dry run
    @param overwrite: apply over a plan that already has a landed build (default: refuse)
    @param footprints: optional label→cells map
    @param world: optional World-like
    @param osm_root: optional clip root (tests / fixtures only; not cross-world)
    @param zone_data: optional lidar grids for zoned labels (tests)
    @param aerial_rgb: optional column RGB map for roof-colour dry-run lines (tests)
    @param landmark_override: non-empty reason skips the landmark street-level image gate
    @returns: Plan
    """
    from builder_provenance import FEATURE_BUILDER, resolve_builder_path, builder_record, provenance
    spec_path = resolve_builder_path(spec_path)
    spec = load_spec(spec_path)
    if do_apply and not overwrite:
        seqs = landed_build_seqs(spec['name'])
        if seqs:
            raise SystemExit(
                f'{spec["name"]}: already built (journal seq {seqs[-1]}). A re-apply writes only '
                'the new plan, so old blocks outside it survive. Undo first: '
                f'element_undo.py --world <world> {spec["name"]} --apply; '
                'or pass --overwrite to apply over it anyway.')
    if do_apply:
        from readiness_evidence import require_feature_ready
        from landmark_gate import feature_cells_for_plan, require_landmark_street_check
        require_feature_ready(spec['name'])
        require_landmark_street_check(
            spec,
            override_reason=landmark_override,
            cells=feature_cells_for_plan(spec['name']),
        )
    plan = build(
        spec, footprints=footprints, world=world, osm_root=osm_root,
        zone_data=zone_data, aerial_rgb=aerial_rgb,
    )
    out = ROOT / f'verification/site/{spec["name"]}-preview.png'
    preview(plan, out, scale=4)
    print(json.dumps(plan.summary())[:600])
    apply(plan, dry=not do_apply, modifies=tuple(spec['modifies']),
          meta={'builder': builder_record('spec', spec_path), **provenance(FEATURE_BUILDER, spec_path)})      # ghost owners yield in the journal
    return plan


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('spec', help='site/builds/<name>.json')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--overwrite', action='store_true',
                    help='apply over a plan that already has a landed build (leaves stale blocks)')
    ap.add_argument(
        '--landmark-override',
        default=None,
        metavar='REASON',
        help='build a landmark without a recorded street-level image source (non-empty reason required)',
    )
    a = ap.parse_args()
    override = ''
    if a.landmark_override is not None:
        override = a.landmark_override.strip()
        if not override:
            raise SystemExit('--landmark-override needs a non-empty reason')
    run(a.spec, do_apply=a.apply, overwrite=a.overwrite, landmark_override=override)

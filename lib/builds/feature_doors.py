"""Evidence-driven feature doors, approaches and entrance covers."""

from __future__ import annotations

from typing import TYPE_CHECKING
import math

import numpy as np

from cellmath import DIRS
from feature_spec import STOREY_H
from roof_material import family_variants
from site_kit import ground_scan_range
from stoop import FACE
import provenance as provenance_mod


if TYPE_CHECKING:
    from feature_build import Feature


class DoorPlanning:
    """Evidence-driven feature doors, approaches and entrance covers.

    Used by feature_build.Build; methods share its feature-plan state.
    """

    def doors(self, k: int, f: Feature):
        """Place doors from ``facade_plan`` entrances for this feature's label.

        Unresolved entrances are printed and skipped. No plan entrances means
        no doors (legacy spruce / ``doors`` map removed in #146).

        @param k: feature index (unused; call-site parity)
        @param f: feature mass
        """
        del k
        self.plan_doors(f)

    def _door_jambs_solid(self, cell, face: str, b: int) -> bool:
        """True when both wall-line sides are solid at door height (guard rule).

        Uses block solidity like ``door_frame_issues`` / ``guard_door_frames``,
        not footprint ``f.walls`` membership (#164).

        @param cell: door wall ``(x, z)``
        @param face: ``N``/``S``/``E``/``W``
        @param b: floor y under the door
        @returns: whether both jamb columns are solid at ``b+1`` and ``b+2``
        """
        lat = ((1, 0), (-1, 0)) if face in 'NS' else ((0, 1), (0, -1))
        return all(
            self._is_solid(cell[0] + lx, b + 1, cell[1] + lz)
            and self._is_solid(cell[0] + lx, b + 2, cell[1] + lz)
            for lx, lz in lat
        )

    def _ensure_door_jambs(self, cell, face: str, b: int) -> None:
        """Build missing jamb solids at ``b+1``/``b+2`` and keep them (#164).

        On stepped / rotated faces a coplanar wall neighbour may be absent; the
        door stays on the register cell and the jambs are written into the plan.

        @param cell: door wall ``(x, z)``
        @param face: ``N``/``S``/``E``/``W``
        @param b: floor y under the door
        """
        lat = ((1, 0), (-1, 0)) if face in 'NS' else ((0, 1), (0, -1))
        for lx, lz in lat:
            jx, jz = cell[0] + lx, cell[1] + lz
            for y in (b + 1, b + 2):
                if not self._is_solid(jx, y, jz):
                    self.keep.add((jx, y, jz))
                    self.set(jx, y, jz, self.wall)

    def _entrance_wall_candidates(self, f: Feature, face: str, placed: set) -> list:
        """Wall cells on ``face`` with interior behind.

        Jamb framing is ensured at place time via ``_ensure_door_jambs`` (#164);
        do not drop nearer cells when a framed cell exists elsewhere on the face
        (that jumped doors ~14 blocks on stepped rotated walls).

        @param f: feature mass
        @param face: ``N``/``S``/``E``/``W``
        @param placed: wall cells already used by a door
        @returns: candidate ``(x, z)`` cells
        """
        dx, dz = DIRS[face]
        return [
            c for c in f.walls
            if c not in placed
            and self.exposed(c, face)
            and (c[0] - dx, c[1] - dz) in f.inner
        ]

    def _pick_entrance_wall(
        self, f: Feature, face: str, at, placed: set,
    ) -> tuple[int, int] | None:
        """Nearest wall cell on ``face`` to ``at`` (or mid-face when ``at`` absent).

        When choosing among equals by distance, prefer cells that already have
        solid jambs (same solidity rule as the door-frame guard).

        @param f: feature mass
        @param face: entrance face
        @param at: optional ``[x, z]`` from the plan
        @param placed: already used wall cells
        @returns: wall ``(x, z)`` or ``None``
        """
        cand = self._entrance_wall_candidates(f, face, placed)
        if not cand:
            return None

        def jamb_rank(c: tuple[int, int]) -> int:
            if not self.spec['door_frame']:
                return 0
            return 0 if self._door_jambs_solid(c, face, self._column_floor(f, c)) else 1

        if at is not None and isinstance(at, (list, tuple)) and len(at) == 2:
            ax, az = int(at[0]), int(at[1])
            return min(
                cand,
                key=lambda c: (
                    (c[0] - ax) ** 2 + (c[1] - az) ** 2,
                    jamb_rank(c),
                ),
            )
        if face in 'NS':
            mid = sum(c[0] for c in cand) / len(cand)
            return min(cand, key=lambda c: (abs(c[0] - mid), jamb_rank(c)))
        mid = sum(c[1] for c in cand) / len(cand)
        return min(cand, key=lambda c: (abs(c[1] - mid), jamb_rank(c)))

    def _outside_door_floor(
        self, f: Feature, cell, face: str, floor_offset: int,
    ) -> int:
        """Door floor from outside lidar ground; refuse stale live approaches (#204).

        @param f: feature mass
        @param cell: wall ``(x, z)``
        @param face: facing letter
        @param floor_offset: blocks relative to outside ground (0 = level)
        @returns: floor block y under the door
        """
        dx, dz = DIRS[face]
        st = (cell[0] + dx, cell[1] + dz)
        if f.no in self.zone_data:
            grid = self.zone_data[f.no]
            ground = np.asarray(grid['ground_y'])
            ix, iz = st[0] - grid['x0'], st[1] - grid['z0']
            if not (0 <= iz < ground.shape[0] and 0 <= ix < ground.shape[1]):
                raise ValueError(f'lidar ground unavailable at {st[0]},{st[1]}')
            outside = float(ground[iz, ix])
        else:
            import lidar_provider as lp
            from world_frame import load_frame, mc_grid_to_en
            lidar = lp.tile_client(download=False)
            if lidar.missing('DTM', *lp.mc_box_en(*st, *st)):
                raise ValueError(f'lidar DTM cache missing at {st[0]},{st[1]}')
            frame = load_frame()
            east, north = mc_grid_to_en(np.array([st[0]]), np.array([st[1]]), frame)
            outside = float(np.trunc(lidar.sample('DTM', east, north, True))[0] + frame.y_offset)
        if not math.isfinite(outside):
            raise ValueError(f'lidar ground unavailable at {st[0]},{st[1]}')
        outside = int(outside)
        self._prefetch_live((st[0], y, st[1]) for y in ground_scan_range(outside))
        live_y = self.top_solid(*st, outside)
        if live_y is None or abs(live_y - outside) > 3:
            raise ValueError(
                f'live y={live_y}, lidar y={outside} at {st[0]},{st[1]} '
                '(maximum difference 3 blocks)'
            )
        floor = self._column_floor(f, cell)
        top = (getattr(f, 'zone_roof', None) or {}).get(cell, f.b + f.storeys * STOREY_H)
        # Keep the opening below its roof/lintel.
        return min(max(outside + int(floor_offset), outside), max(floor, top - 3))

    def _column_floor(self, f: Feature, cell: tuple[int, int]) -> int:
        """Floor y at a column (local lidar ground when zoned, else ``f.b``).

        @param f: feature mass
        @param cell: ``(x, z)``
        @returns: floor block y
        """
        gy = getattr(f, 'zone_ground', None)
        if gy is not None and cell in gy:
            return int(gy[cell])
        return f.b

    def _set_door_leaf(self, cell, face: str, block: str, hinge: str, b: int):
        """Write one door leaf (lower+upper); clear outside stoop and inward vestibule.

        The inward clear (#143) stops ``door_to_wall`` when a storey floor sits at the
        door opening: air through the leaf height, floor underfoot left alone. Opening
        air/stoop cells are always added to ``keep`` so the earlier-build clash filter
        cannot drop them when ground already owns the column (live jubilee/west fail).
        Missing jamb solids at ``b+1``/``b+2`` are built and kept (#164) so stepped
        rotated walls stay on the register cell instead of jumping to a framed cell.
        Neither clear reaches a carpet-finished deck row (#455) on a low shell.

        @param cell: wall ``(x, z)``
        @param face: facing letter
        @param block: door block id (e.g. ``iron_door``)
        @param hinge: ``left`` or ``right``
        @param b: floor y
        """
        dx, dz = DIRS[face]
        face_name = FACE[face]
        for y, part in ((b + 1, 'lower'), (b + 2, 'upper')):
            self.set(cell[0], y, cell[1],
                     f'{block}[facing={face_name},half={part},hinge={hinge},open=false]')
        # Lintel on the wall column (shell may have left air at door height on stepped faces).
        self.keep.add((cell[0], b + 3, cell[1]))
        self.set(cell[0], b + 3, cell[1], self.wall)
        self._ensure_door_jambs(cell, face, b)
        st = (cell[0] + dx, cell[1] + dz)
        self.keep.add((st[0], b, st[1]))
        self.set(st[0], b, st[1], 'smooth_stone')
        # a carpet-finished deck row (#455) and its carpet are roof, not opening: the clear stops under them
        decks = {c: y for f in getattr(self, 'features', ()) for c, y in getattr(f, 'carpet_cells', {}).items()}
        for y in range(b + 1, b + 4):
            if decks.get(st) in (y, y - 1):
                continue
            self.keep.add((st[0], y, st[1]))
            self.set(st[0], y, st[1], 'air')
        inn = (cell[0] - dx, cell[1] - dz)
        for y in range(b + 1, b + 4):
            if decks.get(inn) in (y, y - 1):
                continue
            self.keep.add((inn[0], y, inn[1]))
            self.set(inn[0], y, inn[1], 'air')

    def _pair_door_leaf(
        self, f: Feature, face: str, c0: tuple[int, int], placed: set, at,
    ) -> tuple[int, int] | None:
        """Lateral coplanar wall neighbour for a second leaf (#165).

        Only same-plane offsets (east/west on an N/S face, north/south on an E/W
        face). Diagonal cells across a step are never considered — they put the
        leaves in different planes and fail the frame guard. Step-corner walls
        that lack an ``inner`` cell behind still count: they are wall + exposed
        to ``face``. Jamb / step solids at ``b+1``/``b+2`` are built when the
        leaf is placed (``_ensure_door_jambs``).

        @param f: feature mass
        @param face: entrance face
        @param c0: primary leaf wall cell
        @param placed: wall cells already used by a door
        @param at: optional register ``[x, z]`` (tie-break toward it)
        @returns: pair ``(x, z)`` or ``None``
        """
        lat = (1, 0) if face in 'NS' else (0, 1)
        ax = int(at[0]) if isinstance(at, (list, tuple)) and len(at) == 2 else c0[0]
        az = int(at[1]) if isinstance(at, (list, tuple)) and len(at) == 2 else c0[1]
        opts = [
            (c0[0] + lat[0], c0[1] + lat[1]),
            (c0[0] - lat[0], c0[1] - lat[1]),
        ]
        opts.sort(key=lambda c: (c[0] - ax) ** 2 + (c[1] - az) ** 2)
        for c in opts:
            if c in placed or c not in f.walls:
                continue
            if not self.exposed(c, face):
                continue
            return c
        return None

    def plan_doors(self, f: Feature) -> None:
        """Place doors and queue entrance covers; skip unresolved entrances.

        @param f: feature mass
        """
        body = (self.spec.get('facade_plan') or {}).get(f.no) or {}
        rows = body.get('entrances') or []
        placed: set = set()
        f.door = None
        f.rear = None
        f.door_cells = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            eid = str(row.get('id') or '?')
            if provenance_mod.is_unresolved(row.get(provenance_mod.PROVENANCE_KEY)):
                reason = (row.get(provenance_mod.PROVENANCE_KEY) or {}).get(
                    'unresolved_reason',
                ) or ''
                print(f'door {eid}: unresolved ({reason})')
                continue
            face = row.get('face')
            leaves = row.get('leaves')
            block = row.get('block')
            if face not in DIRS or leaves not in (1, 2) or not isinstance(block, str):
                print(f'door {eid}: incomplete placed entrance: skipped')
                continue
            at = row.get('at')
            c0 = self._pick_entrance_wall(f, face, at, placed)
            if c0 is None:
                print(f'door {eid}: no wall on face {face}: skipped')
                continue
            if at is not None and isinstance(at, (list, tuple)) and len(at) == 2:
                ax, az = int(at[0]), int(at[1])
                moved = abs(c0[0] - ax) + abs(c0[1] - az)
                if moved:
                    print(
                        f'door {eid}: moved {moved} blocks from register '
                        f'{ax},{az} to {c0[0]},{c0[1]}'
                    )
            lat = (1, 0) if face in 'NS' else (0, 1)
            cells = [c0]
            if leaves == 2:
                pair = self._pair_door_leaf(f, face, c0, placed, at)
                if pair is None:
                    print(f'door {eid}: no pair cell; using 1-leaf')
                    leaves = 1
                else:
                    cells = sorted([c0, pair], key=lambda c: c[0] * lat[0] + c[1] * lat[1])
            offset = row.get('floor_offset')
            if not isinstance(offset, int) or isinstance(offset, bool):
                offset = 0
            try:
                floors = [self._outside_door_floor(f, c, face, offset) for c in cells]
            except ValueError as exc:
                print(f'door {eid}: refused ({exc})')
                continue
            b = floors[0]
            cover_y = max(floors) + 3
            hinges = ('left',) if leaves == 1 else ('left', 'right')
            for cell, hinge, leaf_b in zip(cells, hinges, floors):
                self._set_door_leaf(cell, face, block, hinge, leaf_b)
                placed.add(cell)
                f.door_cells.add(cell)
            cover = row.get('awning_like') or {}
            if cover.get('kind') in ('awning', 'covered_area', 'porch_roof'):
                self._entrance_covers.append((f, eid, cover, face, cells, cover_y))
            dx, dz = DIRS[face]
            self.step_out((cells[0][0] + dx, cells[0][1] + dz), face, b)
            if f.door is None:
                f.door = cells[0]
            print(
                f'door {eid} {leaves}-leaf {block} '
                f'at {cells[0][0]},{b + 1},{cells[0][1]} facing {FACE[face]}'
            )

    def entrance_covers(self) -> None:
        """Place covers queued by ``plan_doors`` after roof and detail passes.

        Slab roofs extend one block beyond each leaf edge. Porch posts stand
        at the two outer front corners, leaving the door approach walkable.
        A catalogue material with no slab (terracotta, concrete) places its
        full block instead and records a warning. It does not raise.

        @returns: None
        """
        for f, eid, cover, face, cells, roof_y in self._entrance_covers:
            depth = cover.get('depth') or 1
            kind = cover['kind']
            dx, dz = DIRS[face]
            lx, lz = (1, 0) if face in 'NS' else (0, 1)
            start = cells[0]
            width = len(cells)
            raw = f.roof.get('material') or 'smooth_stone'
            material = family_variants(raw)
            if 'slab' in material:
                cover_state = f"{material['slab']}[type=bottom]"
            else:
                cover_state = material['full']
                self._roof_warnings.append(
                    f"roof material {raw!r} has no slab; using full block {material['full']}"
                )
            for lateral in range(-1, width + 1):
                for outward in range(1, depth + 1):
                    x = start[0] + lx * lateral + dx * outward
                    z = start[1] + lz * lateral + dz * outward
                    if (x, z) in self.by_no_all_cells:
                        continue
                    self.set(x, roof_y, z, cover_state)
                    if kind == 'porch_roof' and outward == depth and lateral in (-1, width):
                        ground_y = self.top_solid(x, z, roof_y)
                        if ground_y is not None:
                            for y in range(ground_y + 1, roof_y):
                                self.set(x, y, z, self.wall)
            print(f'door {eid} cover {kind} depth {depth}')

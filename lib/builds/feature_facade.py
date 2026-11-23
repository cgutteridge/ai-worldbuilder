"""Feature facade glazing, street faces, shopfronts and their diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING
import hashlib
import math
import statistics
import textwrap

from cellmath import DIRS, N4, N8, segment_distance
from feature_spec import STOREY_H
from site_kit import AIR_IDS, bid
from stoop import FACE
import facade_plan as facade_plan_mod
import land_use
import sign_options


if TYPE_CHECKING:
    from feature_build import Feature


FASCIA_COLOURS = ('blue', 'green', 'red', 'cyan', 'orange', 'purple')


# Light concrete bands. Name lettering is chosen against the sign board.
LIGHT_FASCIA = frozenset({'cyan', 'orange', 'lime', 'yellow', 'white', 'pink'})


# Facade exposure intentionally excludes void_air; volume checks include it.
_OUTSIDE_AIR_EXCLUSIONS = frozenset({'void_air'})


_OUTSIDE_AIRS = tuple('minecraft:' + name for name in AIR_IDS if name not in _OUTSIDE_AIR_EXCLUSIONS)


_AIRS = tuple('minecraft:' + name for name in AIR_IDS)


def fascia_colour(key: str) -> str:
    """Stable fascia colour from a mapped name or feature label.

    @param key: name, brand, unit or feature label
    @returns: a concrete colour name
    """
    digest = hashlib.sha256(key.encode()).digest()
    return FASCIA_COLOURS[int.from_bytes(digest[:4], 'big') % len(FASCIA_COLOURS)]


def contrasting_sign_colour(fascia: str = '', *, wood: str = 'oak') -> str:
    """Dye colour that reads on the sign board (map-colour contrast).

    Text is judged against the board, not the fascia band. ``fascia`` is
    accepted for older call sites and ignored.

    @param fascia: unused (legacy)
    @param wood: sign wood family (default oak)
    @returns: dye name with enough contrast on that board
    """
    return sign_options.resolve_text_colour(wood or sign_options.default_wood(), None)


# A one-step notch is filled; deeper recesses begin a separate facade run.
MAX_SHOP_STEP = 2


# Square a shop row only when it lies this close to a cardinal axis.
# Measured 2026-11-23 from the Portswood Road parade (trial #257, cell 51,-37),
# British National Grid to the world frame (easting 436248, northing 109608):
# shop long edges sit 36.7°–44.9° off the nearest axis (median 42.5°); the long
# road run through the cell is 42.7°. 15° is under half that shallowest front.
# A 1-in-5 drift measures about 11° and still squares; a 1-in-3 stair measures
# about 18° and keeps its steps. A one-block notch on a straight row is about 0.4°.
# A grid-aligned corner is not judged by that row angle: each arm is measured
# on its own, so an L still squares when the whole front is about 45°, and a
# one-block notch stays on the arm it steps off.
SHOP_SQUARE_MAX_ANGLE = 15


# Cells in a contiguous cardinal run of this length form a wall arm.
# Two cells is a corner return. One cell is a step, not an arm.
_CORNER_ARM = 2


def front_row_angle(cells) -> float:
    """Degrees of these columns from the nearest cardinal axis.

    One column, or none, is 0. Otherwise this is the major axis of the column
    centres, folded into 0–45 (0 along the grid, 45 on a diagonal).

    @param cells: ``(x, z)`` columns
    @returns: angle in degrees
    """
    pts = list(cells)
    if len(pts) < 2:
        return 0.0
    mx = sum(p[0] for p in pts) / len(pts)
    mz = sum(p[1] for p in pts) / len(pts)
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    szz = sum((p[1] - mz) ** 2 for p in pts)
    sxz = sum((p[0] - mx) * (p[1] - mz) for p in pts)
    angle = abs(math.degrees(0.5 * math.atan2(2 * sxz, sxx - szz))) % 180
    angle = min(angle, 180 - angle)
    return min(angle, 90 - angle)


def _contiguous_runs(values: list[int]) -> list[list[int]]:
    """Split sorted unique coordinates into adjacent runs.

    @param values: coordinates along one axis
    @returns: runs of values that differ by 1
    """
    ordered = sorted(set(values))
    if not ordered:
        return []
    runs = [[ordered[0]]]
    for value in ordered[1:]:
        if value == runs[-1][-1] + 1:
            runs[-1].append(value)
        else:
            runs.append([value])
    return runs


def _corner_arms(cells: set) -> tuple[set, set]:
    """East-west and north-south runs of at least :data:`_CORNER_ARM` cells.

    @param cells: front columns
    @returns: ``(horizontal, vertical)`` columns on those runs
    """
    by_z: dict[int, list[int]] = {}
    by_x: dict[int, list[int]] = {}
    for x, z in cells:
        by_z.setdefault(z, []).append(x)
        by_x.setdefault(x, []).append(z)
    horizontal = {
        (x, z)
        for z, xs in by_z.items()
        for run in _contiguous_runs(xs)
        if len(run) >= _CORNER_ARM
        for x in run
    }
    vertical = {
        (x, z)
        for x, zs in by_x.items()
        for run in _contiguous_runs(zs)
        if len(run) >= _CORNER_ARM
        for z in run
    }
    return horizontal, vertical


def _cells_touching(arm: set, spare: set) -> set:
    """Spare columns that share a side or a corner with ``arm``.

    A one-block notch on a straight arm touches that arm only at the
    corners beside the gap. The front component is 8-connected, so the
    notch is still part of the arm.

    @param arm: columns already on a wall arm
    @param spare: columns on neither arm
    @returns: spare columns one block off the arm
    """
    if not arm or not spare:
        return set()
    return {
        (x + dx, z + dz)
        for x, z in arm
        for dx, dz in N8
        if (x + dx, z + dz) in spare
    }


def _axis_aligned_corner(cells: set) -> bool:
    """True when the columns are two grid-aligned arms, one east-west and one north-south.

    An L measures about 45° on its principal axis, so the row angle cannot
    see the arms. Each arm is measured on its own. A one-block notch stays
    with the arm it steps off, and an arm may be only two cells. A diagonal
    stair has no second arm, or that arm is itself steep, so the row angle
    decides.

    @param cells: one connected component of front columns
    @returns: whether the component is a grid-aligned corner
    """
    horizontal, vertical = _corner_arms(cells)
    if not horizontal or not vertical:
        return False
    spare = cells - horizontal - vertical
    horizontal = horizontal | _cells_touching(horizontal, spare)
    vertical = vertical | _cells_touching(vertical, spare)
    if not cells <= horizontal | vertical:
        return False
    limit = SHOP_SQUARE_MAX_ANGLE
    return front_row_angle(horizontal) <= limit and front_row_angle(vertical) <= limit


def _square_shop_row(cells: set) -> bool:
    """True when this connected front should snap onto straight runs.

    @param cells: one connected component of front columns
    @returns: whether the row is near a cardinal axis, or is a grid-aligned corner
    """
    if _axis_aligned_corner(cells):
        return True
    return front_row_angle(cells) <= SHOP_SQUARE_MAX_ANGLE


def fit_straight_runs(fronts: dict) -> list[dict]:
    """Square near-axis shop fronts and leave a steep row as wall steps.

    A connected front within :data:`SHOP_SQUARE_MAX_ANGLE` of a cardinal axis,
    and a grid-aligned corner, become straight runs. One run per stretch whose
    recess is at most :data:`MAX_SHOP_STEP` blocks. An already straight front is
    unchanged. Each stretch snaps to its own streetward edge, and every block
    between a recessed column and that edge is fill, so a later awning or sign
    meets wall rather than a gap.

    A steeper row is not squared. Each column stays a step with its own face
    and no fill. The awning, the sign and the door then sit on that step, one
    block outward along its face, so they still touch the wall.

    @param fronts: ``{(x, z): 'N'|'S'|'E'|'W'}``
    @returns: list of ``{'face', 'line', 'fill', 'sources'}``; ``line`` is ordered
        along the run, ``fill`` is the cells to build that are not already front
        columns, ``sources`` is the front columns the run came from
    """
    if not fronts:
        return []
    remaining = set(fronts)
    runs = []
    while remaining:
        start = min(remaining)
        comp = {start}
        remaining.remove(start)
        todo = [start]
        while todo:
            x, z = todo.pop()
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    if not dx and not dz:
                        continue
                    nxt = (x + dx, z + dz)
                    if nxt in remaining:
                        remaining.remove(nxt)
                        comp.add(nxt)
                        todo.append(nxt)
        if _square_shop_row(comp):
            runs.extend(_runs_within_step(comp, fronts))
        else:
            runs.extend(_stepped_shop_runs(comp, fronts))
    return runs


def _runs_within_step(cells: set, fronts: dict) -> list[dict]:
    """Split one connected front wherever the recess exceeds :data:`MAX_SHOP_STEP`.

    @param cells: front columns in one component
    @param fronts: face of every front column
    @returns: straight runs, each no deeper than the step limit
    """
    horizontal = sum(fronts[c] in 'NS' for c in cells) >= sum(fronts[c] in 'EW' for c in cells)

    def cross(cell):
        return cell[1] if horizontal else cell[0]

    def along(cell):
        return cell[0] if horizontal else cell[1]

    ordered = sorted(cells, key=lambda cell: (along(cell), cross(cell)))
    groups = []
    current = []
    for cell in ordered:
        trial = current + [cell]
        span = max(cross(c) for c in trial) - min(cross(c) for c in trial)
        if current and span > MAX_SHOP_STEP:
            groups.append(current)
            current = [cell]
        else:
            current.append(cell)
    if current:
        groups.append(current)
    return [_straight_run(set(group), fronts) for group in groups]


def _straight_run(cells: set, fronts: dict) -> dict:
    """Snap one connected front onto its streetward edge.

    @param cells: front columns in one component
    @param fronts: face of every front column
    @returns: ``{'face', 'line', 'fill', 'sources'}``
    """
    horizontal = sum(fronts[c] in 'NS' for c in cells) >= sum(fronts[c] in 'EW' for c in cells)
    if horizontal:
        face = 'N' if sum(fronts[c] == 'N' for c in cells) >= sum(fronts[c] == 'S' for c in cells) else 'S'
        street_low = face == 'N'
        alongs = sorted({c[0] for c in cells})
        outer = min(c[1] for c in cells) if street_low else max(c[1] for c in cells)
        line = [(along, outer) for along in alongs]
    else:
        face = 'W' if sum(fronts[c] == 'W' for c in cells) >= sum(fronts[c] == 'E' for c in cells) else 'E'
        street_low = face == 'W'
        alongs = sorted({c[1] for c in cells})
        outer = min(c[0] for c in cells) if street_low else max(c[0] for c in cells)
        line = [(outer, along) for along in alongs]
    step = -1 if street_low else 1
    fill = set()
    for cell in cells:
        cursor = cell[1] if horizontal else cell[0]
        along = cell[0] if horizontal else cell[1]
        while cursor != outer:
            cursor += step
            fill.add((along, cursor) if horizontal else (cursor, along))
    fill -= set(cells)
    return {'face': face, 'line': line, 'fill': fill, 'sources': set(cells)}


def _stepped_shop_runs(cells: set, fronts: dict) -> list[dict]:
    """Leave a steep shop row as one run per wall step.

    Nothing is filled and nothing is snapped to a streetward line. Each column
    keeps the face it was given. An awning and a sign go one block outward
    along that face, and a door stays on the step.

    @param cells: front columns in one component
    @param fronts: face of every front column
    @returns: runs shaped like :func:`_straight_run`, with empty fill
    """
    return [
        {'face': fronts[cell], 'line': [cell], 'fill': set(), 'sources': {cell}}
        for cell in sorted(cells)
    ]


def _shop_door_cell(runs: list) -> tuple[int, int]:
    """Column for a shop door when the plan did not register one.

    A squared run uses its middle cell. A stepped front has no straight run,
    so the door sits on the wall step nearest the middle of the row.

    @param runs: dressed runs with at least one line cell
    @returns: ``(x, z)``
    """
    longest = max(runs, key=lambda run: (len(run['line']), run['line']))
    if len(longest['line']) > 1:
        return longest['line'][len(longest['line']) // 2]
    lines = [cell for run in runs for cell in run['line']]
    cx = statistics.mean(cell[0] for cell in lines)
    cz = statistics.mean(cell[1] for cell in lines)
    return min(lines, key=lambda cell: (abs(cell[0] - cx) + abs(cell[1] - cz), cell))


class FacadePlanning:
    """Feature facade glazing, street faces, shopfronts and their diagnostics.

    Used by feature_build.Build; methods share its feature-plan state.
    """

    def face_runs(self, f: Feature, face: str) -> list[list]:
        """Contiguous straight wall runs exposed to ``face``, each sorted along the face.

        An L-shaped or notched mass yields several runs (one per constant edge
        coordinate), so pier rhythm applies per straight stretch — not across a
        courtyard notch. Corner cells stay in their run (craftsmanship end piers).

        @param f: feature mass
        @param face: ``N``/``S``/``E``/``W``
        @returns: list of ordered ``(x, z)`` runs
        """
        cand = [c for c in f.walls if self.exposed(c, face)]
        if not cand:
            return []
        by_edge: dict = {}
        if face in 'NS':
            for c in cand:
                by_edge.setdefault(c[1], []).append(c)
            along = 0
        else:
            for c in cand:
                by_edge.setdefault(c[0], []).append(c)
            along = 1
        runs = []
        for _edge, cells in sorted(by_edge.items()):
            cells = sorted(cells, key=lambda c: c[along])
            run = [cells[0]]
            for c in cells[1:]:
                prev = run[-1]
                if c[along] == prev[along] + 1:
                    run.append(c)
                else:
                    runs.append(run)
                    run = [c]
            runs.append(run)
        return runs

    def face_run(self, f: Feature, face: str) -> list:
        """All wall cells exposed to ``face`` (concat of ``face_runs``), for share totals.

        @param f: feature mass
        @param face: ``N``/``S``/``E``/``W``
        @returns: ordered ``(x, z)`` cells
        """
        out = []
        for run in self.face_runs(f, face):
            out.extend(run)
        return out

    @staticmethod
    def facade_roles(row: dict, n: int) -> list[str]:
        """Expand a facade row into per-cell roles (``W`` glaze, ``B`` balcony, ``_`` pier).

        Without ``rhythm``, uses 2-wide windows and ``pier`` solids; end cells are piers.

        @param row: facade table row
        @param n: face length in cells
        @returns: list of length ``n``
        """
        if n <= 0:
            return []
        rhythm = row.get('rhythm')
        if rhythm:
            chars = list(rhythm)
            roles = (chars * ((n // len(chars)) + 1))[:n]
        else:
            pier = row['pier']
            win_w = 2
            period = win_w + pier
            roles = []
            for i in range(n):
                if period > 0 and (i % period) < win_w:
                    roles.append('W')
                else:
                    roles.append('_')
            roles[0] = '_'
            if n > 1:
                roles[-1] = '_'
        return roles

    def _door_keepout(self, f: Feature) -> set:
        """Wall cells that must not become glass (every placed door leaf).

        @param f: feature mass
        @returns: set of ``(x, z)``
        """
        cells = set(getattr(f, 'door_cells', ()) or ())
        for q in (getattr(f, 'door', None), getattr(f, 'rear', None)):
            if q:
                cells.add(q)
        return cells

    def _column_roof_y(self, f: Feature, cell: tuple[int, int]) -> int | None:
        """Roof y for a column when zoned; ``None`` means uniform-storey shell.

        @param f: feature mass
        @param cell: ``(x, z)``
        @returns: roof block y or ``None``
        """
        roof = getattr(f, 'zone_roof', None)
        if roof is None:
            return None
        return roof.get(cell)

    def _band_top(self, f: Feature, cell: tuple[int, int]) -> int:
        """Highest row a street-face band may write on a column.

        The row under the roof, or the deck row itself when a carpet finish made it the
        wall top course (#455): there the fascia is that top course.

        @param f: feature mass
        @param cell: ``(x, z)``
        @returns: block y
        """
        roof_y = self._column_roof_y(f, cell)
        roof_y = roof_y if roof_y is not None else f.b + f.storeys * STOREY_H
        return roof_y if cell in (getattr(f, 'carpet_cells', None) or {}) else roof_y - 1

    def windows(self, f: Feature):
        """Resolve intended glazing as complete bounded openings before writing glass."""
        from floor_reference import outside_ground
        from window_openings import Window, choose_windows, report_lines
        facades = facade_plan_mod.glazing_by_face(self.spec.get('facade_plan'), f.no)
        if not facades and self._suppress_window_grid(f):
            return  # a dressed class street face replaces the unevidenced all-face storey grid
        door = self._door_keepout(f)
        candidates = []
        # Preserve actual door leaves and their lateral framing, including jambs
        # that were already solid before the door builder ran.
        protected = set(self.keep)
        for pos, state in self.p.cells.items():
            if '_door[' in state:
                x, y, z = pos
                protected.add(pos)
                for dx, dz in DIRS.values():
                    protected.add((x + dx, y, z + dz))
        for face in sorted(facades or DIRS):
            runs = self.face_runs(f, face)
            edge = 1 if face in 'NS' else 0
            stepped = len({c[edge] for run in runs for c in run}) > 1
            rows = {r['storey']: r for r in facades.get(face, [])}
            for storey in range(f.storeys):
                row = rows.get(storey + 1)
                if facades and not row:
                    continue
                base = f.b + storey * STOREY_H
                for run in runs:
                    roles = self.facade_roles(row, len(run)) if row else [
                        'W' if (c[0] if face in 'NS' else c[1]) % 2 == 0 else '_' for c in run]
                    corner_cells = {tuple(v['at']) for v in (row or {}).get('corner_glazing', [])}
                    for c, role in zip(run, roles):
                        if c in door:
                            continue
                        if not row and any(abs(c[0] - q[0]) + abs(c[1] - q[1]) < 3 for q in door):
                            continue
                        ry = self._column_roof_y(f, c)
                        for dy in range(STOREY_H):
                            y = base + dy
                            if ry is not None and y >= ry:
                                continue
                            pos = (c[0], y, c[1])
                            if row and row.get('wall') and pos not in protected and c not in door:
                                self.set(*pos, row['wall'])
                            wanted = role == 'W' and (dy >= STOREY_H - row['glazed_h'] if row else dy == 2)
                            if wanted:
                                candidates.append(Window(pos, face, stepped, c in corner_cells))
        positions = {(w.pos[0] + DIRS[w.face][0], w.pos[2] + DIRS[w.face][1])
                     for w in candidates}
        ground = outside_ground(positions, self.zone_data.get(f.no))
        def block(x, y, z):
            return self.p.cells.get((x, y, z), 'minecraft:air')
        writes, report = choose_windows(candidates, block=block, walls=f.walls,
            exposed=self.exposed, outside_ground=ground, protected=protected)
        for pos, state in writes.items():
            self.set(*pos, state)
        self._window_lines.extend(report_lines(f.no, report))

    def _street_face_rule(self, f: Feature) -> dict | None:
        tags = self.building_tags.get(f.no, {})
        cls = self.land_uses.get(f.no, land_use.classify_building(tags)['class'])
        roof = getattr(f, 'zone_roof', None)
        height = max(roof[c] - self._column_floor(f, c) for c in f.cells) if roof else f.storeys * STOREY_H
        return land_use.street_face_defaults(cls, tags, columns=len(f.cells), height=height)

    def _fronts_for(self, f: Feature) -> dict:
        """Street-face columns for this feature, using the mapped address street when set.

        @param f: feature mass
        @returns: ``{(x, z): face}`` from :func:`facade_plan.front_columns`
        """
        tags = self.building_tags.get(f.no, {})
        return facade_plan_mod.front_columns(
            f.cells, self.streets, occupied=self.by_no_all_cells,
            street_name=tags.get('addr:street'))

    def _shop_band_fits(
        self, f: Feature, cell: tuple[int, int], glass_height: int, floor: int | None = None,
    ) -> bool:
        """True when a column has room for glass, the awning row and the sign row under the roof.

        On a carpet-finished deck (#455) the deck row is the band's top course, and one fascia
        row (awning and sign together) is enough.

        @param f: feature mass
        @param cell: ``(x, z)``
        @param glass_height: opening height in blocks from the shop recipe
        @param floor: band floor y; defaults to the column floor
        @returns: whether the fascia band fits below the roof
        """
        floor = self._column_floor(f, cell) if floor is None else floor
        rows = 1 if cell in (getattr(f, 'carpet_cells', None) or {}) else 2
        return floor + glass_height + rows <= self._band_top(f, cell)

    def _shop_front_fits(self, f: Feature, fronts: dict, rule: dict) -> bool:
        """True when at least one front column can take the shop fascia band.

        @param f: feature mass
        @param fronts: street-face columns
        @param rule: shop recipe
        @returns: whether the shell is tall enough to dress
        """
        return any(self._shop_band_fits(f, cell, rule['glass_height']) for cell in fronts)

    def _suppress_window_grid(self, f: Feature) -> bool:
        """Whether the unevidenced storey grid should stay off this shell.

        Industrial and big-box rules always replace that grid. A shop replaces it
        only when a street front is actually dressed; a shop with no mapped street,
        or a shell too low for the fascia, keeps the grid.

        @param f: feature mass
        @returns: True when the grid must not run
        """
        rule = self._street_face_rule(f)
        if not rule:
            return False
        if rule['kind'] != 'shop':
            return True
        if (self.spec.get('facade_plan') or {}).get(f.no, {}).get('faces'):
            return False
        fronts = self._fronts_for(f)
        return bool(fronts) and self._shop_front_fits(f, fronts, rule)

    def _shop_column_ok(self, f: Feature, cell: tuple[int, int]) -> bool:
        """False when a squared-front cell belongs to another building or the room inside.

        @param f: feature mass
        @param cell: ``(x, z)``
        @returns: whether the shop front may build here
        """
        if cell in getattr(f, 'inner', ()):
            return False
        if cell in f.cells:
            return True
        return cell not in self.by_no_all_cells

    def _placed_door(self, cell: tuple[int, int]):
        """Block id, floor y and face of a door leaf already written on ``cell``.

        @param cell: wall ``(x, z)``
        @returns: ``(block, floor_y, face)`` or ``None``
        """
        facing = {'north': 'N', 'south': 'S', 'east': 'E', 'west': 'W'}
        for (x, y, z), state in self.p.cells.items():
            if (x, z) != cell or '_door[' not in state or 'half=lower' not in state:
                continue
            block = state.split('[', 1)[0].removeprefix('minecraft:')
            face = next((letter for name, letter in facing.items() if f'facing={name}' in state), 'N')
            return block, y - 1, face
        return None

    def _release_recessed_door(self, cell: tuple[int, int], face: str, floor: int) -> None:
        """Take a door off a recessed column so the straight run can hold the one door.

        Drops the keep marks on the leaf, its stoop and its inward clear, and turns
        the leaf back into wall. The stoop is often the run line, and a kept air
        block there would stop the fascia.

        @param cell: recessed door ``(x, z)``
        @param face: door facing letter
        @param floor: floor y under the leaf
        """
        dx, dz = DIRS[face]
        for y in range(floor, floor + 4):
            for ox, oz in ((0, 0), (dx, dz), (-dx, -dz)):
                pos = (cell[0] + ox, y, cell[1] + oz)
                self.keep.discard(pos)
                if '_door[' in self.p.cells.get(pos, ''):
                    self.p.cells[pos] = self.wall

    def _shop_floor(self, f: Feature, cell: tuple[int, int], face: str, glass_height: int) -> int:
        """Floor under a shopfront column: the pavement outside when it is above the column floor.

        A shop's floor is the median lidar ground of its footprint, so on a site that falls
        away from the street the front stands one block under the pavement and the door
        opens below it (#436). The front band (glass, door, fascia) stands on the outside
        lidar ground instead, the same reference plan doors use (``_outside_door_floor``,
        #204), but never below the column floor and only while the band still fits under
        the roof. A refused outside reading keeps the column floor.

        @param f: feature mass
        @param cell: front line ``(x, z)``
        @param face: street-facing letter
        @param glass_height: opening height in blocks from the shop recipe
        @returns: floor block y for the band and door on this column
        """
        floor = self._column_floor(f, cell)
        try:
            outside = self._outside_door_floor(f, cell, face, 0)
        except ValueError:
            return floor
        if outside > floor and self._shop_band_fits(f, cell, glass_height, outside):
            return outside
        return floor

    def _shop_line_for_door(self, runs: list, door: tuple[int, int]):
        """Line cell in front of a door that sits on a squared run.

        @param runs: dressed runs with ``sources`` and ``line``
        @param door: registered door ``(x, z)``
        @returns: ``(line_cell, face)`` or ``None`` when the door is not on this front
        """
        for run in runs:
            owned = set(run['sources']) | set(run['line']) | set(run['fill'])
            if door not in owned:
                continue
            horizontal = run['face'] in 'NS'
            along = door[0] if horizontal else door[1]
            for cell in run['line']:
                if (cell[0] if horizontal else cell[1]) == along:
                    return cell, run['face']
        return None

    def _dress_shop_front(self, f: Feature, fronts: dict, rule: dict, name: str) -> None:
        """Glass, a pavement-level door, fascia, a contrasting sign, and a continuous awning.

        A near-axis row is squared onto straight runs first (:func:`fit_straight_runs`).
        Glass, the door, the fascia, the sign and the awning sit on that line, and
        the stair-step between a recessed column and the line is filled solid. A
        steeper row keeps each wall step: the same pieces sit on the step, one
        block outward along its face. Either way the awning, the sign and the door
        touch the wall. Side, alley and party walls are not in ``fronts`` and stay
        shell. The band and the door stand on the pavement or the column floor, whichever
        is higher (:meth:`_shop_floor`). On a straight run its jambs are
        the line's neighbours; on a step they are that step's neighbours.

        @param f: feature mass
        @param fronts: ``{(x, z): 'N'|'S'|'E'|'W'}`` from :func:`facade_plan.front_columns`
        @param rule: shop recipe from :func:`land_use.street_face_defaults`
        @param name: mapped name; empty skips the sign
        """
        runs = []
        for run in fit_straight_runs(fronts):
            line = [cell for cell in run['line'] if self._shop_column_ok(f, cell)]
            if not line:
                continue
            runs.append({
                'face': run['face'],
                'line': line,
                'fill': {cell for cell in run['fill'] if self._shop_column_ok(f, cell)},
                'sources': set(run['sources']),
            })
        if not runs:
            return
        face_of = {cell: run['face'] for run in runs for cell in run['line']}
        line_cells = set(face_of)
        door_line = []
        for door in list(getattr(f, 'door_cells', ()) or ()):
            hit = self._shop_line_for_door(runs, door)
            if hit is None:
                continue
            line_cell, face = hit
            if line_cell != door:
                placed = self._placed_door(door)
                block = placed[0] if placed else rule['door']
                old_floor = placed[1] if placed else self._column_floor(f, door)
                old_face = placed[2] if placed else fronts.get(door, face)
                self._release_recessed_door(door, old_face, old_floor)
                f.door_cells.discard(door)
                if getattr(f, 'door', None) == door:
                    f.door = line_cell
                self._set_door_leaf(
                    line_cell, face, block, 'left',
                    self._shop_floor(f, line_cell, face, rule['glass_height']),
                )
                f.door_cells.add(line_cell)
            door_line.append(line_cell)
        place_centre = not door_line
        door_cell = _shop_door_cell(runs) if place_centre else None
        if door_cell is not None:
            door_line.append(door_cell)
        jambs = set()
        for cell in door_line:
            face = face_of[cell]
            lateral = (1, 0) if face in 'NS' else (0, 1)
            jambs.update(
                (cell[0] + lateral[0] * side, cell[1] + lateral[1] * side) for side in (1, -1)
            )
        sign = sign_options.resolve_label_sign(
            self.spec, f.no, hash_key=name or f.no, hash_colour=fascia_colour,
        )
        colour = sign['fascia_colour']
        fascia = f'{colour}_concrete'
        glass_h = rule['glass_height']
        rows = {}
        for cell in line_cells:
            if not self._shop_band_fits(f, cell, glass_h):
                continue
            floor = self._shop_floor(f, cell, face_of[cell], glass_h)
            awning_y = floor + glass_h + 1
            rows[cell] = (floor, awning_y, min(awning_y + 1, self._band_top(f, cell)))
        if door_cell not in rows:
            door_cell = None
        doors = self._door_keepout(f)
        protected = set(self.keep)
        for (x, y, z), state in self.p.cells.items():
            if '_door[' in state:
                protected.update((x + dx, y, z + dz) for dx, dz in ((0, 0), *N4))

        def write(cell, y, state):
            pos = (cell[0], y, cell[1])
            if y <= self._band_top(f, cell) and pos not in protected and cell not in doors:
                self.set(*pos, state)

        for run in runs:
            sample = next((cell for cell in run['line'] if cell in rows), None)
            if sample is None:
                continue
            floor, _awning_y, sign_y = rows[sample]
            for cell in run['fill']:
                if cell in line_cells:
                    write(cell, floor, self.wall)
                    continue
                for y in range(floor, sign_y + 1):
                    write(cell, y, self.wall)
        for cell, (floor, awning_y, _sign_y) in rows.items():
            if cell != door_cell and cell not in jambs:
                for y in range(floor + 1, awning_y):
                    write(cell, y, 'glass')
        if place_centre and door_cell is not None:
            floor = rows[door_cell][0]
            self._set_door_leaf(door_cell, face_of[door_cell], rule['door'], 'left', floor)
            f.door_cells.add(door_cell)
            if getattr(f, 'door', None) is None:
                f.door = door_cell
        for cell, (_floor, awning_y, sign_y) in rows.items():
            write(cell, awning_y, fascia)
            write(cell, sign_y, fascia)
        if name and rows:
            cx = statistics.mean(x for x, _z in rows)
            cz = statistics.mean(z for _x, z in rows)
            for cell in sorted(rows, key=lambda c: (abs(c[0] - cx) + abs(c[1] - cz), c)):
                _floor, _awning_y, sign_y = rows[cell]
                dx, dz = DIRS[face_of[cell]]
                pos = (cell[0] + dx, sign_y, cell[1] + dz)
                outside = self.p.cells.get(pos)
                if outside is None:
                    outside = self.live.block(*pos)
                wall = self.p.cells.get((cell[0], sign_y, cell[1]))
                if wall == bid(fascia) and outside in _OUTSIDE_AIRS:
                    self.set(
                        *pos,
                        f"{sign['wall_sign']}[facing={FACE[face_of[cell]]}]",
                    )
                    self.p.sign(
                        pos, textwrap.wrap(name, width=rule['sign_wrap'])[:4],
                        colour=sign['text_colour'], glow=False,
                    )
                    break
        if rule['awning']:
            hinge_for = {'N': 'south', 'S': 'north', 'E': 'west', 'W': 'east'}
            for cell, (_floor, awning_y, _sign_y) in rows.items():
                dx, dz = DIRS[face_of[cell]]
                outside_col = (cell[0] + dx, cell[1] + dz)
                if outside_col in self.by_no_all_cells:
                    continue
                pos = (outside_col[0], awning_y, outside_col[1])
                outside = self.p.cells.get(pos)
                if outside is None:
                    outside = self.live.block(*pos)
                if outside not in _OUTSIDE_AIRS:
                    continue
                hinge = hinge_for[face_of[cell]]
                self.set(*pos, f'oak_trapdoor[facing={hinge},half=top,open=false]')

    def street_face(self, f: Feature) -> None:
        """Dress an inferred industrial, big-box or shop street face.

        Evidenced facade rows win. Uses mapped names, street lines and the class table
        only. Shop kinds select the awning. Full blocks support diagonal frontages; writes
        stay below each column's roof. Loading marks replace yard surfaces, never buildings
        or an unevidenced lawn.
        """
        rule = self._street_face_rule(f)
        if not rule or (self.spec.get('facade_plan') or {}).get(f.no, {}).get('faces'):
            return
        tags = self.building_tags.get(f.no, {})
        fronts = self._fronts_for(f)
        if not fronts:
            print(f'{f.no} street face {rule["kind"]}: no mapped street front')
            return
        name = tags.get('name') or tags.get('brand') or tags.get('addr:unit') or ''
        if rule['kind'] == 'shop':
            if not self._shop_front_fits(f, fronts, rule):
                print(f'{f.no} street face shop: shell too low for a fascia band; left the window grid')
                return
            self._dress_shop_front(f, fronts, rule, name)
            print(
                f'{f.no} street face shop: {len(fronts)} columns; '
                f'kind={rule.get("shop_kind")}; awning={rule["awning"]}; name={name}'
            )
            return
        sign = sign_options.resolve_label_sign(
            self.spec, f.no, hash_key=name or f.no, hash_colour=fascia_colour,
        )
        colour = sign['fascia_colour']
        fascia = f'{colour}_concrete'
        doors = self._door_keepout(f)
        protected = set(self.keep)
        for (x, y, z), state in self.p.cells.items():
            if '_door[' in state:
                protected.update((x + dx, y, z + dz) for dx, dz in ((0, 0), *N4))

        def write(c, y, state):
            pos = (c[0], y, c[1])
            if y <= self._band_top(f, c) and pos not in protected and c not in doors:
                self.set(*pos, state)

        fascia_h = (rule['shutter_height'] + 1 if rule['kind'] == 'industrial' else
                    rule['glazed_storeys'] * STOREY_H)
        fascia_y = {}
        for c in fronts:
            floor = self._column_floor(f, c)
            fascia_y[c] = min(floor + fascia_h, self._band_top(f, c))
            if rule['kind'] == 'big_box':
                for y in range(floor + 1, fascia_y[c]):
                    write(c, y, 'light_blue_stained_glass')
            write(c, fascia_y[c], fascia)
        if rule['kind'] == 'industrial':
            # The longest connected frontage gives each mapped unit one central shutter.
            remaining = set(fronts) - doors
            runs = []
            while remaining:
                run = {remaining.pop()}
                todo = list(run)
                while todo:
                    x, z = todo.pop()
                    for dx, dz in N8:
                        c = (x + dx, z + dz)
                        if c in remaining:
                            remaining.remove(c)
                            run.add(c)
                            todo.append(c)
                runs.append(run)
            if runs:
                run = sorted(max(runs, key=lambda r: (len(r), sorted(r))))
                if max(z for x, z in run) - min(z for x, z in run) > max(x for x, z in run) - min(x for x, z in run):
                    run.sort(key=lambda c: (c[1], c[0]))
                width = min(rule['shutter_width'], max(0, len(run) - 2))
                start = (len(run) - width) // 2
                opening = run[start:start + width]
                for c in opening:
                    for y in range(self._column_floor(f, c) + 1, fascia_y[c]):
                        write(c, y, 'gray_concrete')
                if opening:
                    self._loading_bay(f, opening, fronts, rule['bay_depth'])
        elif name:
            # A wall sign is attached to a real fascia cell, including on stepped frontages.
            cx = statistics.mean(x for x, z in fronts)
            cz = statistics.mean(z for x, z in fronts)
            for c in sorted(fronts, key=lambda c: (abs(c[0] - cx) + abs(c[1] - cz), c)):
                y = fascia_y[c]
                dx, dz = DIRS[fronts[c]]
                pos = (c[0] + dx, y, c[1] + dz)
                outside = self.p.cells.get(pos)
                if outside is None:
                    outside = self.live.block(*pos)
                if (self.p.cells.get((c[0], y, c[1])) == bid(fascia) and
                        outside in _OUTSIDE_AIRS):
                    self.set(
                        *pos,
                        f"{sign['wall_sign']}[facing={FACE[fronts[c]]}]",
                    )
                    self.p.sign(
                        pos, textwrap.wrap(name, width=15)[:4],
                        colour=sign['text_colour'], glow=True,
                    )
                    break
        print(f'{f.no} street face {rule["kind"]}: {len(fronts)} columns; name={name}')

    def _loading_bay(self, f: Feature, opening: list, fronts: dict, depth: int) -> None:
        """Paint a bay on continuous forecourt, stopping before roads and obstacles."""
        from ground_sources import ROAD_CLASSES, road_width
        paving = {'gray_concrete', 'black_concrete', 'light_gray_concrete', 'stone',
                  'smooth_stone', 'andesite', 'gravel', 'gray_terracotta'}
        rays = []
        for c in opening:
            dx, dz = DIRS[fronts[c]]
            rays.append([(c[0] + dx * step, c[1] + dz * step) for step in range(1, depth + 1)])
        if not rays:
            return
        cells = {c for ray in rays for c in ray}
        x0, x1 = min(x for x, z in cells), max(x for x, z in cells)
        z0, z1 = min(z for x, z in cells), max(z for x, z in cells)
        roads = []
        for highway, points, tags in self.streets:
            if highway.removesuffix('_link') not in ROAD_CLASSES and highway not in (
                    'service', 'living_street', 'track', 'road'):
                continue
            half = road_width(dict(tags, highway=highway)) / 2
            for a, b in zip(points, points[1:]):
                if (max(a[0], b[0]) + half >= x0 + 0.5 and min(a[0], b[0]) - half <= x1 + 0.5 and
                        max(a[1], b[1]) + half >= z0 + 0.5 and min(a[1], b[1]) - half <= z1 + 0.5):
                    roads.append((a, b, half))
        surfaces = []
        for c, ray in zip(opening, rays):
            available = []
            for x, z in ray:
                if ((x, z) in self.by_no_all_cells or any(
                        segment_distance((x + 0.5, z + 0.5), a, b)[0] <= half
                        for a, b, half in roads)):
                    break
                # Ground is beneath the opening; use local surface height on slopes.
                floor = self._column_floor(f, c)
                surface = None
                for y in range(floor + 1, floor - 5, -1):
                    state = self.p.cells.get((x, y, z))
                    if state is None:
                        state = self.live.block(x, y, z)
                    state = state or 'minecraft:air'
                    if state not in _AIRS:
                        if (bid(state)[10:] in paving and
                                not any(self._is_solid(x, y + dy, z) for dy in (1, 2))):
                            surface = (x, y, z)
                        break
                if surface is None:
                    break
                available.append(surface)
            surfaces.append(available)
        # One short ray limits the whole bay, including its far edge and unpainted interior.
        available_depth = min(map(len, surfaces))
        for i, ray in enumerate(surfaces):
            for step, pos in enumerate(ray[:available_depth], 1):
                if i in (0, len(opening) - 1) or step == available_depth:
                    self.set(*pos, 'yellow_concrete')
                    self.keep.add(pos)

    def glazed_share_report(self) -> list[str]:
        """Glazed share of each storey on every exposed face (dry-run photo compare).

        Lists every face with wall cells exposed that way, including faces
        with no facade table rows. Those are marked ``no evidence read``
        (report only — not a gate).

        @returns: human-readable lines
            ``<label> face <F> storey <n>: glazed a/b (p%)``
            or the same with `` — no evidence read`` when that face has no
            facade rows in the spec
        """
        lines = []
        for f in self.features:
            facades = facade_plan_mod.glazing_by_face(self.spec.get('facade_plan'), f.no) or {}
            for face in sorted('NSEW'):
                run = self.face_run(f, face)
                if not run:
                    continue
                has_rows = bool(facades.get(face))
                for s in range(f.storeys):
                    base = f.b + s * STOREY_H
                    total = glazed = 0
                    for c in run:
                        for dy in range(STOREY_H):
                            st = self.p.cells.get((c[0], base + dy, c[1]), '')
                            name = st.split('[')[0]
                            if name.endswith('_door') or name == 'air':
                                continue
                            total += 1
                            if 'glass' in name:
                                glazed += 1
                    if total == 0:
                        continue
                    pct = 100 * glazed // total
                    rule = self._street_face_rule(f)
                    suffix = (f' — {rule["kind"]} class default' if rule and not facades else
                              ' — no evidence read' if not has_rows else '')
                    lines.append(
                        f'{f.no} face {face} storey {s + 1}: '
                        f'glazed {glazed}/{total} ({pct}%){suffix}')
        return lines

"""Single owner of coverage-cell maths for the active world.

Owns cell size, home cell, bounds, cell→region mapping, ring order, directions,
connected components, ``parse_cell``,
canonical id format ``tx,tz``, and sparse MC tile ids. Home cell and cell size
come from the active world's ``config/world-frame.json`` (``cell.size``,
``cell.home``); defaults are 128 and home (48, -44) when the frame omits them.

A cell ``(tx, tz)`` covers blocks x in ``[tx*size, tx*size+size-1]``,
z in ``[tz*size, tz*size+size-1]``. North is −Z. Pure functions aside from
reading the world frame for size/home.
"""
from __future__ import annotations

import math
from collections import Counter, deque
from pathlib import Path
from typing import Any

# Minecraft Anvil region edge in blocks (32 chunks × 16).
REGION_BLOCKS = 512

_DEFAULT_CELL_SIZE = 128
_DEFAULT_HOME: tuple[int, int] = (48, -44)

_SIZE: int = _DEFAULT_CELL_SIZE
_HOME: tuple[int, int] = _DEFAULT_HOME
_LOADED = False

N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
N8 = tuple((dx, dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if dx or dz)
DIRS = {'N': (0, -1), 'S': (0, 1), 'W': (-1, 0), 'E': (1, 0)}
# Outward block-facing vectors. Keep Minecraft word names and E/W/S/N tie order.
FACING = {word: DIRS[letter] for word, letter in
          (('east', 'E'), ('west', 'W'), ('south', 'S'), ('north', 'N'))}
OPP = {'N': 'S', 'S': 'N', 'W': 'E', 'E': 'W'}


def _frame_path() -> Path | None:
    try:
        from script_env import ROOT  # noqa: WPS433
        path = Path(ROOT) / 'config' / 'world-frame.json'
        return path if path.is_file() else None
    except Exception:
        return None


def reload_grid(frame: dict[str, Any] | None = None, *, path: Path | None = None) -> None:
    """Reload cell size and home cell from a frame dict or world-frame.json.

    @param frame: optional already-loaded world-frame object
    @param path: optional path to world-frame.json (ignored when ``frame`` set)
    """
    global _SIZE, _HOME, _LOADED, CELL, HOME_CELL
    data = frame
    if data is None:
        p = path or _frame_path()
        if p is not None and p.is_file():
            import json
            data = json.loads(p.read_text(encoding='utf-8'))
        else:
            data = {}
    cell = (data or {}).get('cell') or {}
    size = int(cell.get('size', _DEFAULT_CELL_SIZE))
    if size <= 0 or (size & (size - 1)) != 0:
        raise ValueError(f'cell.size must be a positive power of two, got {size}')
    if 'origin' in cell:
        raise ValueError('cell.origin has been renamed to cell.home; update config/world-frame.json')
    home = cell.get('home', list(_DEFAULT_HOME))
    if not (isinstance(home, (list, tuple)) and len(home) == 2):
        raise ValueError(f'cell.home must be [tx, tz], got {home!r}')
    _SIZE = size
    _HOME = (int(home[0]), int(home[1]))
    CELL = _SIZE
    HOME_CELL = _HOME
    _LOADED = True


def _ensure_loaded() -> None:
    if not _LOADED:
        reload_grid()


def cell_size() -> int:
    """Coverage cell edge length in blocks.

    Honours test patches of module-level ``CELL`` after :func:`reload_grid`.

    @returns: cell size (power of two)
    """
    _ensure_loaded()
    return CELL


def home_cell() -> tuple[int, int]:
    """Configured radial-focus cell (ring 0).

    Honours test patches of module-level ``HOME_CELL``.

    @returns: (tx, tz)
    """
    _ensure_loaded()
    return HOME_CELL


# Back-compat module constants (updated by reload_grid).
CELL = _DEFAULT_CELL_SIZE
HOME_CELL = _DEFAULT_HOME


def parse_cell(spec: str) -> tuple[int, int]:
    """Parse ``tx,tz``.

    @param spec: comma-separated cell indices
    @returns: (tx, tz)
    @raises SystemExit: on bad input
    """
    parts = [p.strip() for p in str(spec).split(',')]
    if len(parts) != 2:
        raise SystemExit(f'expected tx,tz got {spec!r}')
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        raise SystemExit(f'expected tx,tz got {spec!r}') from None


def cell_id(cell: tuple[int, int] | str) -> str:
    """Canonical cell id string ``tx,tz``.

    @param cell: (tx, tz) or already-canonical string
    @returns: ``\"tx,tz\"``
    """
    if isinstance(cell, str):
        tx, tz = parse_cell(cell)
        return f'{tx},{tz}'
    return f'{cell[0]},{cell[1]}'


def cell_of(x: int, z: int, size: int | None = None) -> tuple[int, int]:
    """Cell key of a block column: (floor(x / size), floor(z / size)).

    @param x: Minecraft X
    @param z: Minecraft Z
    @param size: optional edge override (default: configured cell size)
    @returns: (tx, tz)
    """
    s = cell_size() if size is None else size
    return x // s, z // s


def cells_by_column_count(columns, size: int | None = None) -> list[tuple[int, int]]:
    """Cells a set of columns touches, most columns first.

    Each ``(x, z)`` counts once, so a tall stack does not outweigh a wider
    footprint. A tie goes to the lowest ``(tx, tz)`` (task 288).

    @param columns: ``(x, z)`` columns, or ``(x, y, z)`` positions (``y`` ignored)
    @param size: optional cell edge
    @returns: ``[(tx, tz), ...]``; empty when ``columns`` is empty
    """
    seen: set[tuple[int, int]] = set()
    for col in columns:
        if len(col) >= 3:
            x, z = col[0], col[2]
        else:
            x, z = col[0], col[1]
        seen.add((int(x), int(z)))
    if not seen:
        return []
    counts = Counter(cell_of(x, z, size) for x, z in seen)
    return sorted(counts, key=lambda c: (-counts[c], c))


def majority_cell(columns, size: int | None = None) -> tuple[int, int] | None:
    """The cell holding most of ``columns``; ties go to the lowest ``(tx, tz)``.

    @param columns: ``(x, z)`` columns, or ``(x, y, z)`` positions (``y`` ignored)
    @param size: optional cell edge
    @returns: ``(tx, tz)``, or None when ``columns`` is empty
    """
    cells = cells_by_column_count(columns, size)
    return cells[0] if cells else None


def bounds(cell: tuple[int, int], margin: int = 0, size: int | None = None) -> tuple[int, int, int, int]:
    """Inclusive (x0, z0, x1, z1) of a cell, grown by ``margin`` blocks on every side.

    @param cell: (tx, tz)
    @param margin: blocks grown on every side
    @param size: optional edge override
    @returns: inclusive bbox
    """
    s = cell_size() if size is None else size
    x0, z0 = cell[0] * s, cell[1] * s
    return x0 - margin, z0 - margin, x0 + s - 1 + margin, z0 + s - 1 + margin


def cell_bbox(tx0: int, tz0: int, tx1: int, tz1: int, size: int | None = None) -> tuple[int, int, int, int]:
    """Inclusive block bbox of a rectangle of cells.

    @param tx0: first cell x
    @param tz0: first cell z
    @param tx1: last cell x (inclusive)
    @param tz1: last cell z (inclusive)
    @param size: optional edge override
    @returns: (x0, z0, x1, z1)
    """
    s = cell_size() if size is None else size
    return tx0 * s, tz0 * s, (tx1 + 1) * s - 1, (tz1 + 1) * s - 1


def components(cells, connect=N4, *, traversal='stable') -> list[set]:
    """Connected components of a column set, in stable order by default.

    @param cells: iterable of ``(x, z)`` columns
    @param connect: neighbour offsets (:data:`N4` default, or :data:`N8`)
    @param traversal: ``stable`` uses minimum seeds and BFS, sorted largest first;
        ``set-pop-dfs`` preserves set-pop seeds, DFS neighbour discovery and component
        discovery order for the ground noise pass's border ties and in-place relabelling
    @returns: component sets, ordered by the selected traversal
    """
    if traversal == 'set-pop-dfs':
        left, out = set(cells), []
        while left:
            seed = left.pop()
            comp, todo = {seed}, [seed]
            while todo:
                x, z = todo.pop()
                for dx, dz in connect:
                    q = (x + dx, z + dz)
                    if q in left:
                        left.discard(q)
                        comp.add(q)
                        todo.append(q)
            out.append(comp)
        return out
    if traversal != 'stable':
        raise ValueError(f'unknown component traversal: {traversal!r}')
    left, out = set(cells), []
    while left:
        seed = min(left)
        comp, todo = {seed}, deque([seed])
        left.discard(seed)
        while todo:
            x, z = todo.popleft()
            for dx, dz in connect:
                n = (x + dx, z + dz)
                if n in left:
                    left.discard(n)
                    comp.add(n)
                    todo.append(n)
        out.append(comp)
    return sorted(out, key=lambda c: (-len(c), min(c)))


def ring_distance(cell: tuple[int, int], home: tuple[int, int] | None = None) -> int:
    """Chebyshev ring number of a cell around ``home``.

    @param cell: (tx, tz)
    @param home: focus cell (default: configured home cell)
    @returns: ring index
    """
    ox, oz = home if home is not None else home_cell()
    return max(abs(cell[0] - ox), abs(cell[1] - oz))


def ring_cells(r: int, home: tuple[int, int] | None = None) -> list[tuple[int, int]]:
    """Cells of Chebyshev ring r: north centre first, then clockwise.

    Order: top edge from the centre column eastward, down the east side, back along
    the bottom, up the west side, finishing the top edge.

    @param r: ring radius
    @param home: focus cell (default: configured home cell)
    @returns: cells on that ring
    """
    ox, oz = (home if home is not None else home_cell())
    if r == 0:
        return [(ox, oz)]
    return ([(ox + dx, oz - r) for dx in range(0, r + 1)]
            + [(ox + r, oz + dz) for dz in range(-r + 1, r + 1)]
            + [(ox + dx, oz + r) for dx in range(r - 1, -r - 1, -1)]
            + [(ox - r, oz + dz) for dz in range(r - 1, -r - 1, -1)]
            + [(ox + dx, oz - r) for dx in range(-r + 1, 0)])


def regions_for_bbox(
    x0: int, z0: int, x1: int, z1: int, margin: int = 0,
) -> list[tuple[int, int]]:
    """Region indices covering an inclusive XZ bbox.

    @param x0: min X
    @param z0: min Z
    @param x1: max X
    @param z1: max Z
    @param margin: extra regions on each side
    @returns: sorted unique (rx, rz)
    """
    if x0 > x1:
        x0, x1 = x1, x0
    if z0 > z1:
        z0, z1 = z1, z0
    rx0 = (x0 >> 9) - margin
    rx1 = (x1 >> 9) + margin
    rz0 = (z0 >> 9) - margin
    rz1 = (z1 >> 9) + margin
    return [(rx, rz) for rx in range(rx0, rx1 + 1) for rz in range(rz0, rz1 + 1)]


def regions_for_cell(cell: tuple[int, int], margin: int = 0) -> list[tuple[int, int]]:
    """Region indices a coverage cell occupies (usually one).

    @param cell: (tx, tz)
    @param margin: extra regions on each side
    @returns: (rx, rz) list
    """
    x0, z0, x1, z1 = bounds(cell)
    return regions_for_bbox(x0, z0, x1, z1, margin=margin)


def mc_tile_id(x: int, z: int, size: int | None = None) -> str:
    """Sparse MC tile id covering block (x, z).

    @param x: Minecraft X
    @param z: Minecraft Z
    @param size: tile edge (default: cell size)
    @returns: e.g. ``\"128:54:-42\"``
    """
    s = cell_size() if size is None else size
    tx = math.floor(x / s)
    tz = math.floor(z / s)
    return f'{s}:{tx}:{tz}'


def mc_tile_for_cell(tx: int, tz: int, size: int | None = None) -> str:
    """Sparse manifest tile id for a coverage cell index.

    @param tx: cell X
    @param tz: cell Z
    @param size: tile edge (default: cell size)
    @returns: e.g. ``\"128:48:-44\"``
    """
    s = cell_size() if size is None else size
    x0 = tx * s
    z0 = tz * s
    return mc_tile_id(x0 + s // 2, z0 + s // 2, s)


# Load configured grid on first import when a world frame is available.
try:
    reload_grid()
except Exception:
    CELL = _DEFAULT_CELL_SIZE
    HOME_CELL = _DEFAULT_HOME


def segment_distance(point, a, b, *, along: bool = False):
    """Distance to segment a-b, plus the nearest point (or distance along it).

    ``along=True`` retains the ground rasterizer's hypot arithmetic and its tiny
    denominator for zero-length segments. The default retains footprint callers'
    Euclidean distance to the projected foot, including their rounding behaviour.
    """
    px, pz = point
    ax, az = a
    bx, bz = b
    dx, dz = bx - ax, bz - az
    length_sq = dx * dx + dz * dz
    if along:
        length_sq = dx ** 2 + dz ** 2 or 1e-9
        t = max(0.0, min(1.0, ((px - ax) * dx + (pz - az) * dz) / length_sq))
        return math.hypot(px - ax - t * dx, pz - az - t * dz), t * math.sqrt(length_sq)
    t = 0 if dx == dz == 0 else max(0, min(1, ((px - ax) * dx + (pz - az) * dz) / length_sq))
    foot = (ax + t * dx, az + t * dz)
    return math.dist(point, foot), foot

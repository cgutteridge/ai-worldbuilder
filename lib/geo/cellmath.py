"""Single owner of coverage-cell maths for the active world.

Owns cell size, home cell, bounds, cell→region mapping, ring order, ``parse_cell``,
canonical id format ``tx,tz``, and sparse MC tile ids. Home cell and cell size
come from the active world's ``config/world-frame.json`` (``cell.size``,
``cell.home``); defaults match Southampton (128 / 48,-44) when absent.

A cell ``(tx, tz)`` covers blocks x in ``[tx*size, tx*size+size-1]``,
z in ``[tz*size, tz*size+size-1]``. North is −Z. Pure functions aside from
reading the world frame for size/home.
"""
from __future__ import annotations

import math
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


def cell_shift() -> int:
    """Bit shift equal to log2(cell size); ``x >> shift`` is the cell index.

    @returns: shift amount
    """
    return int(math.log2(cell_size()))


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


def filename_cell_token(cell: tuple[int, int]) -> str:
    """File-name spelling for a cell (``tx_tz``; negative tz keeps the minus).

    Use only inside file names. Prefer :func:`cell_id` everywhere else.

    @param cell: (tx, tz)
    @returns: e.g. ``48_-44``
    """
    return f'{cell[0]}_{cell[1]}'


def cell_of(x: int, z: int, size: int | None = None) -> tuple[int, int]:
    """Cell key of a block column: (floor(x / size), floor(z / size)).

    @param x: Minecraft X
    @param z: Minecraft Z
    @param size: optional edge override (default: configured cell size)
    @returns: (tx, tz)
    """
    s = cell_size() if size is None else size
    return x // s, z // s


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


def cell_bounds(cell: tuple[int, int], margin: int = 0, size: int | None = None) -> tuple[int, int, int, int]:
    """Alias of :func:`bounds` (legacy name used by world_frame / pass_mask callers)."""
    return bounds(cell, margin=margin, size=size)


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


def region_of_block(x: int, z: int) -> tuple[int, int]:
    """Anvil region index covering block (x, z).

    @param x: Minecraft X
    @param z: Minecraft Z
    @returns: (rx, rz)
    """
    return x >> 9, z >> 9


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

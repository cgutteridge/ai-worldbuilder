"""Shared layer ownership, masks, air policy and surface probes.

Ground, trees, decor, mask undo and the ground audit read these helpers directly;
none needs to import the ground builder. The air policy includes plants so the
passes can replace stems and flowers while preserving solid grass blocks.
"""
from __future__ import annotations

from pathlib import Path

import cellmath
from block_store import block_id
from pass_mask import MASK_DIR, mask_placements, masked_overlaps, read_mask
from place_config import grounds_prefix, plan_name, retired_ground_passes
from site_kit import AIR_IDS, World, full
from surface_probe import GRASS_LIGHT_PATTERNS, PLANTS, is_water, probe_floor

SCAN_TOP = 120          # highest y the clear step looks at (survey tops out near 110)


AIRS = frozenset(full(name) for name in AIR_IDS)


STEM_OR_FLOWER = frozenset(full(p) for p in PLANTS)


BLOCK_KIND = {'gray_concrete': 'tarmac', 'light_gray_concrete': 'paving', 'polished_granite': 'brick',
              'grass_block': 'grass', 'gravel': 'gravel', 'andesite': 'forecourt'}


def shades_grass(above: str | None) -> bool:
    """True when the block directly above a grass top is opaque, so grass would decay to dirt (#242).

    Air, plants, leaves, glass and other see-through blocks let grass live; logs, planks, stone and the like do not.
    """
    b = block_id(above, policy='read')
    if not above or b in AIRS or b in STEM_OR_FLOWER or is_air(above):
        return False
    name = b.split(':')[-1]
    return not any(c in name for c in GRASS_LIGHT_PATTERNS)


def is_air(block: str | None) -> bool:
    """Air for the ground step: includes flowers and grass stems (ADR 010); grass BLOCK is not air."""
    return block_id(block, policy='read') in AIRS or block_id(block, policy='read') in STEM_OR_FLOWER


def cell_contains(cell: tuple[int, int], x: int, z: int) -> bool:
    """True if column (x, z) is inside the 128² cell."""
    x0, z0, x1, z1 = cellmath.bounds(cell)
    return x0 <= x <= x1 and z0 <= z <= z1


def ignored_builds(cell: tuple[int, int]) -> frozenset:
    """Builds that do not count as owners for this cell's ground pass.

    Earlier ground passes (retired), plus this cell's own layer plans: the old foliage pass and
    the ground/trees/decor plans that are being redone, and cell marker rings (wool ~30 m up, not
    ground: counting them left the cell's outer ring of columns unsurfaced). Lidar ghosts
    (ADR 004, ghost owners yield to real owners) are placeholders real ground overwrites. Another cell's
    trees stay owners.
    """
    from lidar_ghost import ghost_owners_for

    own = {plan_name(cell, layer) for layer in ('ground', 'trees', 'decor', 'foliage')}
    own |= set(ghost_owners_for(cell))
    # Earlier ground passes are place data (``retired_ground_passes``). Their blocks are what
    # this layer replaces, so they are not owners. Grounds markers are the same kind of name.
    return retired_ground_passes() | own | {n for n in _all_build_names()
                                            if n.startswith((grounds_prefix(), 'cell_marker_'))}


def _all_build_names() -> set:
    from cell_registry import view_registry
    return {e.plan for e in view_registry().entries()}


def neighbour_owned_states(cell: tuple[int, int], exclude=()) -> dict:
    """Positions other builds own in the cell and its 8 neighbours, with the owned block state.

    @param cell: (tx, tz)
    @param exclude: plan names that do not count (``ignored_builds``)
    @returns: {(x, y, z): state, ...}
    """
    from cell_registry import view_registry
    tx, tz = cell
    around = [(tx + dx, tz + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)]
    return view_registry().owner_states(around, exclude=exclude)


def neighbour_owned(cell: tuple[int, int], exclude=()) -> set:
    """Positions the cell journal says other builds own, in the cell and its 8 neighbours.

    The layer passes read this instead of the old whole-world ownership merge: their plans
    (with tree overhang) never reach past the neighbour cells.

    @param cell: (tx, tz)
    @param exclude: plan names that do not count (``ignored_builds``)
    @returns: {(x, y, z), ...}
    """
    return set(neighbour_owned_states(cell, exclude))


def owned_air_ceilings(neighbour_owned: set, owned_air: set) -> tuple[set, dict]:
    """Split owned columns into structure columns and air-only (feature margin) columns.

    A feature's margin clear owns only air beside its walls. Features now land before the cell's
    ground (task 286), so ground must still surface those columns, below the lowest owned air;
    a column with any owned solid block stays the structure's.

    @param neighbour_owned: owned positions
    @param owned_air: the subset whose owned state is air
    @returns: (structure columns, {(x, z): lowest owned air y} for air-only columns)
    """
    solid = {(x, z) for x, y, z in neighbour_owned if (x, y, z) not in owned_air}
    ceilings: dict = {}
    for x, y, z in owned_air:
        if (x, z) not in solid and y < ceilings.get((x, z), y + 1):
            ceilings[(x, z)] = y
    return solid, ceilings


def owned_floors(neighbour_owned_states: dict, structure: set) -> dict:
    """Highest owned y in each structure column, for the buried-feature test (task 297).

    Between the feature and the ground every column is accounted for: where every block a feature
    owns lies below the ground's surface, the column above the highest of them is the ground's.
    A column holding owned water is a pond or channel, never ground's to cover.

    @param neighbour_owned_states: ``{(x, y, z): state}`` owned positions
    @param structure: columns with an owned solid block (:func:`owned_air_ceilings`)
    @returns: ``{(x, z): highest owned y}`` (owned air counts: the ground never fills it)
    """
    floors: dict = {}
    wet = set()
    for (x, y, z), state in neighbour_owned_states.items():
        q = (x, z)
        if q not in structure:
            continue
        if is_water(full(block_id(state, policy='read'))):
            wet.add(q)
        if y > floors.get(q, y - 1):
            floors[q] = y
    return {q: y for q, y in floors.items() if q not in wet}


def load_masked_positions() -> dict:
    """Every position owned by a tree or decor mask: {pos: 'pass tx,tz'}."""
    owned: dict = {}
    if MASK_DIR.exists():
        for path in sorted(MASK_DIR.glob('*.mask.json')):
            mask = read_mask(path)
            for pos in mask_placements(mask):
                owned[pos] = f"{mask['pass']} {mask['cell'][0]},{mask['cell'][1]}"
    return owned


def mask_plan_overlaps(planned: dict, mask_dir: Path | None = None) -> list[tuple]:
    """Positions in a finished ground plan that a tree or decor mask owns.

    This is the check before apply (``pass_mask.masked_overlaps``). The planner
    also refuses these positions while it builds the plan; this catches any that
    still landed in the plan that would be written.

    @param planned: ``{(x, y, z): block}`` the plan would write
    @param mask_dir: directory of ``*.mask.json`` files (default ``MASK_DIR``)
    @returns: ``(pos, planned block, 'pass tx,tz')`` hits, in mask-file order
    """
    directory = MASK_DIR if mask_dir is None else mask_dir
    hits: list[tuple] = []
    if not directory.exists():
        return hits
    for path in sorted(directory.glob('*.mask.json')):
        mask = read_mask(path)
        why = f"{mask['pass']} {mask['cell'][0]},{mask['cell'][1]}"
        for pos in masked_overlaps(planned, mask):
            hits.append((pos, planned[pos], why))
    return hits


SUPPORT_RUN = 4                  # solid blocks in a row that count as ground under a surface (#296)


PROBE_STRIP = 16                 # x columns warmed per prefetch (keeps each request's position list small)


def probe_y_range() -> tuple[int, int]:
    """Inclusive Y levels the probes read: ``surface_y`` (110 down to the world's probe floor) and the clear step.

    The low end is one above :func:`surface_probe.probe_floor`, so a warm-up over this range covers
    every block ``surface_y`` reads, however low the world's ground lies.

    @returns: ``(lowest y read, SCAN_TOP)``
    """
    return probe_floor() + 1, SCAN_TOP


def warm_surface_probe(world: World, x0: int, z0: int, x1: int, z1: int) -> None:
    """Prefetch every block ``surface_y`` can read (:func:`probe_y_range`), in batched scans.

    Only worth it on a store that keeps what it read: a whole cell is far over the live store's
    cache cap (``block_store.MAX_CACHE_BLOCKS``), which evicts the warm-up as it goes, so the layer
    applies plan from saved region files (``fresh_planning_world``) instead of the live store.
    """
    warm = getattr(world, 'prefetch', None)
    if warm is None:
        return
    ylo, yhi = probe_y_range()
    from stage_timing import timed_stage
    with timed_stage('ground/tree surface warm-up'):
        for xs in range(x0, x1 + 1, PROBE_STRIP):
            xe = min(xs + PROBE_STRIP - 1, x1)
            warm((x, y, z) for x in range(xs, xe + 1) for z in range(z0, z1 + 1)
                 for y in range(ylo, yhi + 1))


def column_ring(columns) -> set:
    """``columns`` and their eight neighbours (the bank of a water set)."""
    return {(x + a, z + b) for x, z in columns for a in (-1, 0, 1) for b in (-1, 0, 1)}


def lidar_base_map(columns, maps=None) -> dict:
    """Lidar HOW-HIGH Y for free columns via ``lidar_heightmap.Maps`` (#172 / #167).

    Prefers bare-terrain ``terrain`` (DTM) so cars, planters and kiosks on the
    DSM surface do not raise road or paving. Falls back to ``base`` when a
    provider or live ``Maps`` has no ``terrain`` array. Footprint / roof
    columns keep using ``base`` elsewhere; this helper only serves free ground.

    Lookups load neighbouring cells when a column sits on a cell edge. Columns
    whose Y is 0 (empty / missing) are omitted so callers can fall back to
    the live surface when lidar is absent.

    @param columns: iterable of ``(x, z)``
    @param maps: optional ``Maps`` (tests / injected evidence); default is the
        shared lidar reference
    @returns: ``{(x, z): y}`` for columns with a positive lidar height
    """
    from lidar_heightmap import Maps
    m = maps if maps is not None else Maps()
    out = {}
    for x, z in columns:
        y = None
        for field in ('terrain', 'base'):
            try:
                y = int(m.get(x, z, field))
                break
            except (LookupError, KeyError, IndexError, TypeError, ValueError):
                continue
        if y is not None and y > 0:
            out[(x, z)] = y
    return out


def support_y(block, x: int, top: int, z: int, floor: int | None = None) -> int | None:
    """Highest y below ``top`` where solid ground starts (#296).

    Ground is ``SUPPORT_RUN`` non-air blocks in a row, or a run that reaches ``floor``. A shorter
    run over air is a stale remnant, not ground: a surface left floating when its support was
    undone, or ghost ground with holes where a feature once stood. It must not stop the fill.

    A surface at the probe floor has nothing readable below it: the probe never reads ``floor`` or
    lower, so the search range is empty and would report a float over solid stone. It rests on the
    baseline there, and ``floor`` is its support. The cost is that a genuine float at that lowest
    level is not detected. Only a call without ``floor`` gets this: a caller passing its own floor,
    such as the journal audit, still sees a lone top block as unsupported.

    @param block: ``callable(x, y, z) -> state | None``
    @param top: the surface y; the search starts one below it
    @param floor: highest y not read (``surface_y`` reads no deeper than ``floor + 1``); None means the
      probe floor, :func:`surface_probe.probe_floor`
    @returns: y of the support's top block, or None when nothing holds the column up
    """
    if floor is None:
        floor = probe_floor()
        if top - 1 <= floor:
            return floor
    for y in range(top - 1, floor, -1):
        if all(not is_air(block(x, y - i, z)) for i in range(SUPPORT_RUN) if y - i > floor):
            return y
    return None

"""Block reads behind one seam: ``BlockStore`` (ADR 004 decision 6, audit task 39 / #56 / #111).

``read`` / ``read_many`` / ``flush`` are everything the cell journal needs. Adapters:

- :class:`MemoryBlocks` is a dict, for tests and planned overlays.
- :class:`LiveBlocks` reads the open world over MCP ``block_scan_region`` (tiled to volume
  ≤ 65 536, ``limit`` raised to the tile size; sparse AABBs fall back to per-chunk scans).
  Region identity and stale-read guards stay; writes always go over MCP, never through this
  module. Live applies and their readback use this path.
- :class:`region_blocks.RegionBlocks` is a **read-only** ``.mca`` parser for planning and
  dry-run reads after a server save (#111; owner exception to the old "no region parse"
  line). It never writes region files.

``site_kit.World`` is a duck-typed facade over this seam (default LiveBlocks; planning /
dry-run helpers inject RegionBlocks). Live MCP lists air for far/missing chunks (see
audit §7); ``None`` only appears when a scan omits a position (FakeScanMcp / filters)
or when RegionBlocks finds no chunk slot.

States are canonical block strings: ``minecraft:`` prefix, properties sorted
(``minecraft:oak_stairs[facing=north,half=bottom]``). ``None`` means "chunk not loaded / unknown".
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping, Protocol, runtime_checkable

Pos = tuple[int, int, int]

# MCP block_scan_region caps (docs/audits/region-block-reads.md §7).
SCAN_VOLUME_CAP = 65536
# When a read_many AABB would need more tiles than this, LiveBlocks scans per chunk instead.
MAX_ENSURE_TILES = 64
# Bound retained reads across long-running multi-cell callers.
MAX_CACHE_BLOCKS = 65536
# A scan is "tiny" when its inclusive AABB volume is at most this. Intentional
# section scans from ``read()`` are 16³=4096; sparse per-chunk fallbacks can still
# be small. Matches the sparse-fallback tests' notion of a tiny scan (≤16).
TINY_SCAN_MAX_VOLUME = 16
# Inclusive size of one Minecraft section edge (``read()`` miss prefetch).
SECTION_EDGE = 16
# After this many tiny scans on one LiveBlocks instance, emit one stderr warning
# (never raise — a hard failure mid-apply is worse than slow). At the default
# 1.1 s MCP pace, 50 tiny scans already burn ~55 s of wall time.
TINY_SCAN_WARN_AFTER = 50


class BlockStoreError(RuntimeError):
    """A block read was refused or could not be served (never swallowed)."""


class StaleRead(BlockStoreError):
    """A read was attempted after a write and before ``flush()``."""


def canonical_state(state: str) -> str:
    """Namespace a block state and sort its properties.

    @param state: ``stone``, ``minecraft:stone`` or ``oak_stairs[half=bottom,facing=north]``
    @returns: ``minecraft:...`` with properties sorted by key
    """
    name, bracket, rest = state.partition('[')
    if ':' not in name:
        name = 'minecraft:' + name
    if not bracket:
        return name
    props = sorted(kv for kv in rest.rstrip(']').split(',') if kv)
    return f'{name}[{",".join(props)}]' if props else name


def block_id(state: str) -> str:
    """Namespaced block id without properties.

    @param state: block state string
    @returns: e.g. ``minecraft:stone``
    """
    return canonical_state(state).split('[')[0]


# Props the game re-derives after placement (skipped by same_state).
_CONNECTION_PROPS = frozenset({'north', 'south', 'east', 'west', 'up'})
_STAIR_SHAPE_PROPS = frozenset({'shape'})


def _game_derives_connections(block: str) -> frozenset[str]:
    """Properties the game rewrites for ``block`` after placement (empty if none).

    Panes, bars, fences and walls: neighbour connections. Stairs: ``shape``
    (pitched roofs plan outer_left/right; the game stores straight).
    """
    name = block.removeprefix('minecraft:')
    if name.endswith(('_pane', '_fence', '_wall', '_bars')):
        return _CONNECTION_PROPS
    if name.endswith('_stairs'):
        return _STAIR_SHAPE_PROPS
    return frozenset()


def same_state(planned: str, actual: str | None) -> bool:
    """Whether ``actual`` satisfies ``planned``: same id and every planned property
    (``waterlogged`` ignored; so are connection properties of panes, bars, fences and walls,
    and ``shape`` on ``*_stairs``, which the game rewrites from the neighbours).
    Mirrored by ``site_kit.same``.

    @param planned: planned block state
    @param actual: block state read back, or None (unloaded)
    @returns: True when it matches
    """
    if actual is None:
        return False
    want_state = canonical_state(planned)
    if block_id(want_state) != actual.split('[')[0]:
        return False
    if '[' not in want_state:
        return True
    want = dict(kv.split('=') for kv in want_state[want_state.index('[') + 1:-1].split(','))
    got = (dict(kv.split('=') for kv in actual[actual.index('[') + 1:-1].split(','))
           if '[' in actual else {})
    skip = {'waterlogged'} | _game_derives_connections(block_id(want_state))
    return all(got.get(k) == v for k, v in want.items() if k not in skip)


@runtime_checkable
class BlockStore(Protocol):
    """The one real seam under the cell journal."""

    def read(self, pos: Pos) -> str | None:
        """Block state at ``pos``, or None when its chunk is not loaded."""

    def read_many(self, positions: Iterable[Pos]) -> Mapping[Pos, str | None]:
        """``{pos: read(pos)}`` for every position, keeping the caller's keys."""

    def flush(self) -> None:
        """Make everything written so far visible to reads (save + drop caches)."""


class MemoryBlocks:
    """Dict-backed :class:`BlockStore` for tests and planned overlays.

    @param blocks: optional initial ``{pos: state}``
    @param default: state returned for positions never written (None = unloaded)
    @param strict_flush: emulate :class:`LiveBlocks`: refuse reads after a write until ``flush()``
    """

    def __init__(self, blocks: Mapping[Pos, str] | None = None, *, default: str | None = None,
                 strict_flush: bool = False) -> None:
        self._blocks: dict[Pos, str] = {}
        self.default = canonical_state(default) if default else None
        self.strict_flush = strict_flush
        self.flush_count = 0
        self._dirty = False
        if blocks:
            self._blocks.update({tuple(p): canonical_state(s) for p, s in blocks.items()})

    def write(self, pos: Pos, state: str) -> None:
        """Set one block (the stand-in for an MCP write).

        @param pos: (x, y, z)
        @param state: block state
        """
        self._blocks[tuple(pos)] = canonical_state(state)
        self._dirty = True

    def write_many(self, blocks: Mapping[Pos, str]) -> None:
        """Set many blocks.

        @param blocks: ``{pos: state}``
        """
        for pos, state in blocks.items():
            self.write(pos, state)

    def read(self, pos: Pos) -> str | None:
        """@returns: stored state, else ``default``
        @raises StaleRead: in strict mode after a write without ``flush()``"""
        if self.strict_flush and self._dirty:
            raise StaleRead('read after write without flush()')
        return self._blocks.get(tuple(pos), self.default)

    def read_many(self, positions: Iterable[Pos]) -> dict[Pos, str | None]:
        """@returns: ``{pos: state}`` for every requested position"""
        return {tuple(p): self.read(p) for p in positions}

    def flush(self) -> None:
        """Count a flush and clear the stale-read guard."""
        self.flush_count += 1
        self._dirty = False


_PROP_LINE = re.compile(r'^(\w+):\s*(?:"([^"]*)"|(\S+))\s*$')


def parse_block_scan_text(text: str) -> dict[Pos, str]:
    """Parse MCP ``block_scan_region`` verbose text into canonical states.

    @param text: tool text content (``[N]:`` header + YAML-ish records, or ``[]``)
    @returns: ``{(x, y, z): canonical state}``
    """
    text = text.strip()
    if not text or text == '[]':
        return {}
    out: dict[Pos, str] = {}
    pos: dict[str, int] = {}
    block_id_s: str | None = None
    props: dict[str, str] = {}
    in_props = False

    def commit() -> None:
        nonlocal block_id_s, props, pos, in_props
        if block_id_s is not None and len(pos) == 3:
            state = block_id_s
            if props:
                state += '[' + ','.join(f'{k}={props[k]}' for k in sorted(props)) + ']'
            out[(pos['x'], pos['y'], pos['z'])] = canonical_state(state)
        pos, block_id_s, props, in_props = {}, None, {}, False

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('['):
            continue
        if line.startswith('- position:'):
            commit()
            continue
        if line.startswith('state:'):
            in_props = False
            continue
        if line == 'properties:':
            in_props = True
            continue
        if in_props:
            m = _PROP_LINE.match(line)
            if m:
                props[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
            continue
        if line.startswith('id:'):
            block_id_s = line.split(':', 1)[1].strip().strip('"')
            continue
        if line.startswith(('x:', 'y:', 'z:')) and not in_props:
            key, _, val = line.partition(':')
            # Ignore nested ``position:`` under get_state; scan records put x/y/z under position.
            if key.strip() in 'xyz':
                pos[key.strip()] = int(val.strip())
            continue
        # lightLevel / hardness / hasBlockEntity — ignore
    commit()
    return out


def format_block_scan_text(blocks: Mapping[Pos, str]) -> str:
    """Build ``block_scan_region``-shaped text (for FakeScanMcp / recorded fixtures).

    @param blocks: ``{pos: state}``
    @returns: verbose scan text matching the live MCP shape
    """
    if not blocks:
        return '[]'
    lines = [f'[{len(blocks)}]:']
    for (x, y, z), state in blocks.items():
        st = canonical_state(state)
        name, _, rest = st.partition('[')
        lines.append('  - position:')
        lines.append(f'    x: {x}')
        lines.append(f'    y: {y}')
        lines.append(f'    z: {z}')
        lines.append('    state:')
        lines.append(f'      id: "{name}"')
        lines.append('      lightLevel: 0')
        lines.append('      hardness: 0')
        lines.append('      hasBlockEntity: false')
        lines.append('      properties:')
        if rest:
            for kv in rest.rstrip(']').split(','):
                if not kv:
                    continue
                k, _, v = kv.partition('=')
                # Match live quirks: booleans often quoted, enums often bare.
                if v in ('true', 'false'):
                    lines.append(f'        {k}: "{v}"')
                else:
                    lines.append(f'        {k}: {v}')
    return '\n'.join(lines)


def scan_tiles(x0: int, y0: int, z0: int, x1: int, y1: int, z1: int,
               *, cap: int = SCAN_VOLUME_CAP) -> Iterator[tuple[Pos, Pos]]:
    """Yield inclusive ``(lo, hi)`` boxes covering the AABB with volume ≤ ``cap``.

    Tall volumes are sliced on Y (cell-shaped 128×N×128 tiles), matching the §7 probe.
    Wide footprints slice on X when that alone fits; when ``dz > cap`` (so a 1-wide
    strip still exceeds), slice Z as well so recursion always makes progress.

    @param x0,y0,z0: inclusive min corner
    @param x1,y1,z1: inclusive max corner
    @param cap: max blocks per tile (MCP volume limit)
    @returns: iterator of ``((x0,y0,z0), (x1,y1,z1))`` tiles
    """
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0
    if z0 > z1:
        z0, z1 = z1, z0
    dx, dz = x1 - x0 + 1, z1 - z0 + 1
    footprint = dx * dz
    if footprint > cap:
        # Wide AABB — slice on X when a strip fits; otherwise slice Z (dz > cap).
        x_span = max(1, cap // max(1, dz))
        if x_span * dz <= cap:
            for xa in range(x0, x1 + 1, x_span):
                xb = min(xa + x_span - 1, x1)
                yield from scan_tiles(xa, y0, z0, xb, y1, z1, cap=cap)
            return
        z_span = min(dz, cap)
        for za in range(z0, z1 + 1, z_span):
            zb = min(za + z_span - 1, z1)
            yield from scan_tiles(x0, y0, za, x1, y1, zb, cap=cap)
        return
    max_h = max(1, cap // footprint)
    for ya in range(y0, y1 + 1, max_h):
        yb = min(ya + max_h - 1, y1)
        yield (x0, ya, z0), (x1, yb, z1)


def aabb_tile_lower_bound(x0: int, y0: int, z0: int, x1: int, y1: int, z1: int,
                          *, cap: int = SCAN_VOLUME_CAP) -> int:
    """Lower bound on how many ``scan_tiles`` boxes an AABB needs (volume / cap).

    Used to decide sparse fallback without materialising millions of tiles.

    @param x0,y0,z0: inclusive min corner
    @param x1,y1,z1: inclusive max corner
    @param cap: max blocks per tile
    @returns: at least 1; ``ceil(volume / cap)``
    """
    dx = abs(x1 - x0) + 1
    dy = abs(y1 - y0) + 1
    dz = abs(z1 - z0) + 1
    vol = dx * dy * dz
    return max(1, (vol + cap - 1) // cap)


def _default_identity_check(region: Path) -> None:
    """Refuse when the region is not the save MCP has open (``site_kit`` rule).

    @param region: region directory
    @raises BlockStoreError: on mismatch
    """
    import site_kit
    try:
        site_kit.ensure_region_matches_open_world(region)
    except SystemExit as exc:
        raise BlockStoreError(str(exc)) from exc


def _default_flusher() -> None:
    """Save the open world over MCP."""
    from mc_paced import Paced
    Paced().call('server_save_all_worlds', {})


def _default_mcp():
    """Live paced MCP client (``text`` + ``call``)."""
    from mc_paced import Paced
    return Paced()


class LiveBlocks:
    """Read-only :class:`BlockStore` over MCP ``block_scan_region`` for the active or bake-off world.

    Guards (audit task 39): only the active or bake-off region is accepted; a region/open-world
    identity check runs at construction; a read after a write is refused until :meth:`flush`.
    Reads tile tall AABBs and raise ``limit`` to each tile's volume so results are exact.

    @param region: ``…/overworld/region`` directory (identity / allow-list only; not parsed)
    @param allowed: regions this store may open (default: the resolved active region and the
        bake-off ``ghost_region_path`` from ``config/world.json``)
    @param identity_check: ``callable(region)`` raising when the region is not the MCP-open save
    @param flusher: ``callable()`` that saves the world (default: MCP ``server_save_all_worlds``)
    @param mcp: client with ``text(name, args)`` (default: ``mc_paced.Paced``); tests inject fakes
    @param dimension: MCP dimension id
    @param settle_seconds: pause after the save so the server has flushed
    @param sleep: sleep function (tests)
    @param tiny_scan_max_volume: inclusive AABB volume at or below which a scan counts as tiny
        (default :data:`TINY_SCAN_MAX_VOLUME`)
    @param tiny_scan_warn_after: emit one stderr warning after this many tiny scans in the
        store's lifetime (default :data:`TINY_SCAN_WARN_AFTER`); never raises
    @raises BlockStoreError: when the region is not allowed or the identity check fails
    """

    def __init__(self, region: Path | str, *, allowed: Iterable[Path | str] | None = None,
                 identity_check: Callable[[Path], None] | None = None,
                 flusher: Callable[[], None] | None = None,
                 mcp=None,
                 dimension: str = 'minecraft:overworld',
                 settle_seconds: float = 3.0, sleep: Callable[[float], None] = time.sleep,
                 tiny_scan_max_volume: int = TINY_SCAN_MAX_VOLUME,
                 tiny_scan_warn_after: int = TINY_SCAN_WARN_AFTER) -> None:
        self.region = Path(region)
        targets = list(allowed) if allowed is not None else self._configured_regions()
        if self.region.resolve() not in {Path(t).resolve() for t in targets}:
            raise BlockStoreError(
                f'LiveBlocks refuses {self.region}: not the active or bake-off region')
        (identity_check or _default_identity_check)(self.region)
        self._flusher = flusher or _default_flusher
        self._mcp = mcp
        self._mcp_factory = _default_mcp
        self.dimension = dimension
        self._settle = settle_seconds
        self._sleep = sleep
        self._cache: dict[Pos, str | None] = {}
        self._dirty = False
        self._tiny_scan_max_volume = tiny_scan_max_volume
        self._tiny_scan_warn_after = tiny_scan_warn_after
        self.tiny_scan_count = 0
        self._tiny_scan_warned = False

    def _client(self):
        if self._mcp is None:
            self._mcp = self._mcp_factory()
        return self._mcp

    @staticmethod
    def _configured_regions() -> list[Path]:
        import site_kit
        out = [site_kit.resolve_region()]
        extra = site_kit._CFG.get('ghost_region_path')
        if extra:
            out.append(Path(extra))
        return out

    @classmethod
    def from_world_config(cls, **kwargs) -> 'LiveBlocks':
        """Store for the active world's region (from ``config/world.json``).

        @returns: LiveBlocks
        """
        import site_kit
        return cls(site_kit.resolve_region(), **kwargs)

    def mark_written(self) -> None:
        """Record that blocks were written over MCP; reads are refused until ``flush()``."""
        self._dirty = True

    def _guard(self) -> None:
        if self._dirty:
            raise StaleRead('blocks were written since the last flush(); call flush() before reading')

    def _note_tiny_scan(self, volume: int) -> None:
        """Count a tiny scan; warn once on stderr after the threshold (never raise).

        @param volume: inclusive AABB volume of the scan about to run
        """
        if volume > self._tiny_scan_max_volume:
            return
        self.tiny_scan_count += 1
        if self._tiny_scan_warned or self.tiny_scan_count < self._tiny_scan_warn_after:
            return
        self._tiny_scan_warned = True
        print(
            f'LiveBlocks: {self.tiny_scan_count} tiny block_scan_region scans '
            f'(AABB volume ≤ {self._tiny_scan_max_volume}) on this store — '
            f'batch with prefetch/read_many so MCP is not paced one block at a time. '
            f'Not raising; further tiny scans will not warn again.',
            file=sys.stderr,
        )

    def _scan_box(self, lo: Pos, hi: Pos, requested: Iterable[Pos] | None = None) -> None:
        """Fetch one tile; bulk reads retain only requested positions, single reads prefetch a section."""
        x0, y0, z0 = lo
        x1, y1, z1 = hi
        vol = (x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1)
        if vol > SCAN_VOLUME_CAP:
            raise BlockStoreError(f'scan tile volume {vol} exceeds MCP cap {SCAN_VOLUME_CAP}')
        self._note_tiny_scan(vol)
        from mc_paced import box
        text = self._client().text('block_scan_region', {
            'dimension': self.dimension,
            'box': box(list(lo), list(hi)),
            'limit': vol,
        })
        found = parse_block_scan_text(text)
        if len(found) > vol:
            raise BlockStoreError('block_scan_region returned more blocks than the tile volume')
        positions = (requested if requested is not None else
                     ((x, y, z) for x in range(x0, x1 + 1)
                      for y in range(y0, y1 + 1) for z in range(z0, z1 + 1)))
        for p in positions:
            # Listed → state (incl. air). Omitted → None (unloaded / filtered void).
            self._cache[p] = found.get(p)

    def _ensure_chunk_groups(self, missing: list[Pos]) -> None:
        """Scan each chunk's needed AABB (sparse far-apart fallback).

        @param missing: positions not yet in the cache
        """
        groups: dict[tuple[int, int], list[Pos]] = {}
        for p in missing:
            groups.setdefault((p[0] >> 4, p[2] >> 4), []).append(p)
        for group in groups.values():
            still = [p for p in group if p not in self._cache]
            if not still:
                continue
            nxs, nys, nzs = zip(*still)
            for lo, hi in scan_tiles(min(nxs), min(nys), min(nzs),
                                     max(nxs), max(nys), max(nzs)):
                self._scan_box(lo, hi, [p for p in still
                                       if all(lo[i] <= p[i] <= hi[i] for i in range(3))])

    def _ensure(self, positions: Iterable[Pos]) -> None:
        missing = [tuple(p) for p in positions if tuple(p) not in self._cache]
        if not missing:
            return
        xs = [p[0] for p in missing]
        ys = [p[1] for p in missing]
        zs = [p[2] for p in missing]
        x0, y0, z0 = min(xs), min(ys), min(zs)
        x1, y1, z1 = max(xs), max(ys), max(zs)
        # Huge / sparse AABB: do not tile the parent box (would be millions of
        # empty scans). Fall back to per-chunk groups instead.
        if aabb_tile_lower_bound(x0, y0, z0, x1, y1, z1) > MAX_ENSURE_TILES:
            self._ensure_chunk_groups(missing)
            return
        for lo, hi in scan_tiles(x0, y0, z0, x1, y1, z1):
            need = [p for p in missing
                    if lo[0] <= p[0] <= hi[0] and lo[1] <= p[1] <= hi[1] and lo[2] <= p[2] <= hi[2]
                    and p not in self._cache]
            if not need:
                continue
            # Shrink to positions still needed so sparse outliers do not scan a
            # whole empty slab of the parent AABB.
            nxs, nys, nzs = zip(*need)
            for slo, shi in scan_tiles(min(nxs), min(nys), min(nzs),
                                       max(nxs), max(nys), max(nzs)):
                self._scan_box(slo, shi, [p for p in need
                                           if all(slo[i] <= p[i] <= shi[i] for i in range(3))])

    def _section_box(self, pos: Pos) -> tuple[Pos, Pos]:
        """Inclusive AABB of the 16×16×16 section containing ``pos``.

        @param pos: world block
        @returns: ``(lo, hi)`` corners of the section
        """
        x, y, z = pos
        e = SECTION_EDGE
        x0, y0, z0 = x - (x % e), y - (y % e), z - (z % e)
        return (x0, y0, z0), (x0 + e - 1, y0 + e - 1, z0 + e - 1)

    def read(self, pos: Pos) -> str | None:
        """@returns: block state, None when the chunk is absent / omitted from the scan

        On a cache miss, scans the containing 16×16×16 section (not one block) so
        unbatched callers do not pace MCP per cell (#117).

        @raises StaleRead: after ``mark_written()`` until ``flush()``
        """
        self._guard()
        p = tuple(pos)
        if p not in self._cache:
            if len(self._cache) + SECTION_EDGE ** 3 > MAX_CACHE_BLOCKS:
                self._cache.clear()
            lo, hi = self._section_box(p)
            self._scan_box(lo, hi)
        return self._cache[p]

    def read_many(self, positions: Iterable[Pos]) -> dict[Pos, str | None]:
        """@returns: ``{pos: state}`` keyed as passed (AABB tiled into MCP scans)
        @raises StaleRead: after ``mark_written()`` until ``flush()``"""
        self._guard()
        pos_list = [tuple(p) for p in positions]
        out = {}
        for start in range(0, len(pos_list), MAX_CACHE_BLOCKS):
            batch = pos_list[start:start + MAX_CACHE_BLOCKS]
            if len(self._cache) + len(batch) > MAX_CACHE_BLOCKS:
                self._cache.clear()
            self._ensure(batch)
            out.update((p, self._cache[p]) for p in batch)
        return out

    def light(self, pos: Pos) -> int | None:
        """Block light at ``pos`` via MCP ``block_get_state`` (one call; not for bulk scans).

        The tool exposes a single ``lightLevel`` (not separate BlockLight / SkyLight).

        @param pos: (x, y, z)
        @returns: 0–15, or None when the response has no lightLevel
        @raises StaleRead: after ``mark_written()`` until ``flush()``
        """
        self._guard()
        from mc_paced import xyz
        text = self._client().text('block_get_state', {
            'dimension': self.dimension,
            'position': xyz(list(pos)),
        })
        m = re.search(r'lightLevel:\s*(\d+)', text or '')
        return int(m.group(1)) if m else None

    def forget(self, positions: Iterable[Pos]) -> None:
        """Drop ``positions`` from the scan cache without saving or clearing the rest.

        Use after a bounded strip warm when later strips must not keep growing
        retained memory. Does not touch the world on disk.

        @param positions: ``(x, y, z)`` tuples previously read or prefetched
        """
        for p in positions:
            self._cache.pop(tuple(p), None)

    def flush(self) -> None:
        """Save the world, wait, drop the scan cache, clear the stale-read guard."""
        self._flusher()
        self._sleep(self._settle)
        self._cache.clear()
        self._dirty = False

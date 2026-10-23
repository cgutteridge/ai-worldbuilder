"""Block reads behind one seam: ``BlockStore`` (ADR 004 decision 6, audit task 39).

``read`` / ``read_many`` / ``flush`` are everything the cell journal needs. Two adapters:

- :class:`MemoryBlocks` is a dict, for tests and planned overlays.
- :class:`LiveBlocks` wraps the read-only region parser ``site_kit.World`` with the audit guards:
  files are only ever read (``rb``), only the active or bake-off region is accepted, a read after
  a write is refused until :meth:`LiveBlocks.flush`, and a region/open-world identity check runs at
  construction (injectable). Writes always go over MCP, never through this module.

States are canonical block strings: ``minecraft:`` prefix, properties sorted
(``minecraft:oak_stairs[facing=north,half=bottom]``). ``None`` means "chunk not loaded / unknown".
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Iterable, Mapping, Protocol, runtime_checkable

Pos = tuple[int, int, int]


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


# Blocks whose connection properties the game re-derives from neighbours after placement.
_CONNECTION_PROPS = frozenset({'north', 'south', 'east', 'west', 'up'})


def _game_derives_connections(block: str) -> bool:
    name = block.removeprefix('minecraft:')
    return name.endswith(('_pane', '_fence', '_wall')) or name == 'iron_bars'


def same_state(planned: str, actual: str | None) -> bool:
    """Whether ``actual`` satisfies ``planned``: same id and every planned property
    (``waterlogged`` ignored; so are connection properties of panes, bars, fences and walls,
    which the game rewrites from the neighbours). Mirrored by ``site_kit.same``.

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
    skip = {'waterlogged'}
    if _game_derives_connections(block_id(want_state)):
        skip |= _CONNECTION_PROPS
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


class LiveBlocks:
    """Read-only :class:`BlockStore` over the saved region files of the active or bake-off world.

    @param region: ``…/overworld/region`` directory
    @param allowed: regions this store may open (default: the resolved active region and the
        bake-off ``ghost_region_path`` from ``config/world.json``)
    @param identity_check: ``callable(region)`` raising when the region is not the MCP-open save
        (default: ``site_kit.ensure_region_matches_open_world``); runs at construction
    @param flusher: ``callable()`` that saves the world (default: MCP ``server_save_all_worlds``)
    @param world_factory: ``callable(region) -> object with block(x, y, z)`` (default ``site_kit.World``)
    @param settle_seconds: pause after the save so files are on disk
    @param sleep: sleep function (tests)
    @raises BlockStoreError: when the region is not allowed or the identity check fails
    """

    def __init__(self, region: Path | str, *, allowed: Iterable[Path | str] | None = None,
                 identity_check: Callable[[Path], None] | None = None,
                 flusher: Callable[[], None] | None = None,
                 world_factory: Callable[[Path], object] | None = None,
                 settle_seconds: float = 3.0, sleep: Callable[[float], None] = time.sleep) -> None:
        self.region = Path(region)
        targets = list(allowed) if allowed is not None else self._configured_regions()
        if self.region.resolve() not in {Path(t).resolve() for t in targets}:
            raise BlockStoreError(
                f'LiveBlocks refuses {self.region}: not the active or bake-off region')
        (identity_check or _default_identity_check)(self.region)
        self._flusher = flusher or _default_flusher
        self._factory = world_factory or self._site_kit_world
        self._settle = settle_seconds
        self._sleep = sleep
        self._world = self._factory(self.region)
        self._dirty = False

    @staticmethod
    def _configured_regions() -> list[Path]:
        import site_kit
        out = [site_kit.resolve_region()]
        extra = site_kit._CFG.get('ghost_region_path')
        if extra:
            out.append(Path(extra))
        return out

    @staticmethod
    def _site_kit_world(region: Path) -> object:
        import site_kit
        return site_kit.World(region)

    @classmethod
    def from_world_config(cls, **kwargs) -> 'LiveBlocks':
        """Store for the resolved active (or ``AI_WORLDBUILDER_REGION``) region.

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

    def read(self, pos: Pos) -> str | None:
        """@returns: block state, None when the chunk is absent
        @raises StaleRead: after ``mark_written()`` until ``flush()``"""
        self._guard()
        return self._world.block(*pos)

    def read_many(self, positions: Iterable[Pos]) -> dict[Pos, str | None]:
        """@returns: ``{pos: state}`` keyed as passed (each chunk is parsed once)
        @raises StaleRead: after ``mark_written()`` until ``flush()``"""
        self._guard()
        return {tuple(p): self._world.block(*p) for p in positions}

    def flush(self) -> None:
        """Save the world, wait, drop the chunk cache, clear the stale-read guard."""
        self._flusher()
        self._sleep(self._settle)
        self._world = self._factory(self.region)
        self._dirty = False

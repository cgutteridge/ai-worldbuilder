"""Test helpers: a throwaway cell journal (MemoryBlocks, tmp world root) and seed builds.

Not a test module (no ``test_`` prefix). Tests use it to put a world into a known journal
state through the real writer, ``CellRegistry.change``, instead of hand-writing records.

    with JournalWorld() as jw:            # sets AI_WORLDBUILDER_REGISTRY_ROOT to a tmp dir
        jw.seed((48, -44), ground=True, trees=True, decor=True, features=('q001_x',))
        cell_registry.view_registry().cell((48, -44)).done

Layer rebuild fixtures for undo tests (#133)::

    info = case.setup_layer_rebuilds(cell=(48, -44), origin=(6150, 60, -5600))
    # ghost base on stage/pass ``other`` (not ``ghost-ground``) + same/wider/narrower
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cellmath
from block_store import MemoryBlocks
from cell_journal import AIR, Build, CellRegistry, Note, Writes, ghost_name


class JournalWorld:
    """A temp world root with a journal-backed registry over :class:`MemoryBlocks`.

    @param root: existing directory to use (default: a new temp dir, removed on exit)
    @param env: also point ``AI_WORLDBUILDER_REGISTRY_ROOT`` at the root while active
    """

    def __init__(self, root: Path | str | None = None, *, env: bool = True) -> None:
        self._tmp = None if root is not None else tempfile.TemporaryDirectory()
        self.root = Path(root) if root is not None else Path(self._tmp.name)
        self.blocks = MemoryBlocks(default=AIR)
        self.reg = CellRegistry(self.root, self.blocks, None)
        self._env = mock.patch.dict(os.environ, {'AI_WORLDBUILDER_REGISTRY_ROOT': str(self.root)}) if env else None

    def __enter__(self) -> 'JournalWorld':
        if self._env is not None:
            self._env.start()
        return self

    def __exit__(self, *exc) -> None:
        if self._env is not None:
            self._env.stop()
        if self._tmp is not None:
            self._tmp.cleanup()

    def apply(self, w: Writes) -> None:
        """Writer: put the planned blocks into the memory world."""
        self.blocks.write_many(w.cells)

    def build(self, name: str, pass_: str, cells: dict, **kw):
        """Journal and land one Build.

        @returns: Receipt
        """
        return self.reg.change(Build(name, pass_, cells, **kw), self.apply)

    def seed(self, cell: tuple[int, int], *, ground: bool = False, trees: bool = False, decor: bool = False,
             features: tuple[str, ...] = (), ghost_ground: bool = False, ghost_features: bool = False,
             marker: bool = False, y: int = 60) -> None:
        """Land small layer / feature plans in a cell (one block row each).

        @param cell: (tx, tz)
        @param ground: generated ground (layer-style names are not needed)
        @param trees: trees pass (needs ground)
        @param decor: decor pass (needs ground)
        @param features: feature plan ids to land in the cell
        @param ghost_ground: ghost ground (before any real ground)
        @param ghost_features: withdrawn — must stay False (task #68)
        @param marker: raise the marker note
        @param y: base Y of the seeded rows
        """
        if ghost_features:
            raise ValueError('ghost_features seeding is withdrawn (task #68); use coloured ghost ground')
        x0, z0, _x1, _z1 = cellmath.bounds(cell)
        tag = f'{cell[0]}_{cell[1]}'.replace('-', 'm')
        if ghost_ground:
            self.build(ghost_name(cell, 'ground'), 'ghost-ground',
                       {(x0, y - 2, z0): 'minecraft:light_gray_concrete'}, cell=cell)
        if ground:
            self.build(f'seed_{tag}_ground', 'ground', {(x0 + i, y, z0): 'minecraft:grass_block' for i in range(3)},
                       cell=cell)
        for k, fid in enumerate(features):
            self.build(fid, 'feature', {(x0 + 4 + k, y + 1, z0 + 4): 'minecraft:bricks'}, cell=cell)
        if trees:
            self.build(f'seed_{tag}_trees', 'trees', {(x0 + 1, y + 1, z0 + 8): 'minecraft:oak_log'}, cell=cell)
        if decor:
            self.build(f'seed_{tag}_decor', 'decor', {(x0 + 2, y + 1, z0 + 9): 'minecraft:poppy'}, cell=cell)
        if marker:
            self.reg.change(Note(tuple(cell), 'marker', 'up'))


class RegistryEvidence:
    """Synthetic readiness adapter for existing map-candidate/registry fixtures.

    Test cases supply clip fractions and candidate groups via their existing doubles.
    It never consults local provider caches or the selected world's evidence.
    """

    revision = 'synthetic-registry-evidence'

    def facts(self, cell):
        from cell_features import clip_coverage
        return {'revision': self.revision, 'clip_coverage': clip_coverage(cell),
                'aerial_coverage': 1., 'lidar_missing': []}

    def candidates(self, reg, cell):
        from registry_next import unplanned_footprint_candidates
        return unplanned_footprint_candidates(reg, f'{cell[0]},{cell[1]}')


# --- layer rebuild fixtures for undo tests (#133) -----------------------------------------------
REBUILD_SHAPES = ('same', 'wider', 'narrower')
_DEFAULT_REBUILD_BLOCKS = (
    'minecraft:grass_block',
    'minecraft:stone',
    'minecraft:dirt',
    'minecraft:cobblestone',
    'minecraft:bricks',
)


def layer_rebuild_cells(origin: tuple[int, int, int], shape: str, block: str, *,
                        far_dx: int = 20) -> dict:
    """Cells for one rebuild shape anchored at ``origin``.

    @param origin: (x, y, z) core column
    @param shape: ``same`` / ``wider`` / ``narrower`` (narrower writes the core only)
    @param block: block state for every cell in the footprint
    @param far_dx: +x offset for the extra column of ``wider``
    @returns: ``{(x, y, z): block, ...}``
    @raises ValueError: unknown shape
    """
    x, y, z = origin
    core = {(x, y, z): block}
    if shape in ('same', 'narrower'):
        return core
    if shape == 'wider':
        return {**core, (x + far_dx, y, z): block}
    raise ValueError(f'unknown rebuild shape {shape!r}; want same, wider, or narrower')


def setup_layer_rebuilds(
    land,
    *,
    cell: tuple[int, int],
    origin: tuple[int, int, int],
    shapes: tuple[str, ...] = REBUILD_SHAPES,
    plan_name: str = 'g',
    layer_pass: str = 'ground',
    base_name: str = 'ghost',
    base_pass: str = 'other',
    with_base: bool = True,
    blocks=None,
    start_block: str = 'minecraft:light_gray_concrete',
    rebuild_blocks: tuple[str, ...] | None = None,
    far_dx: int = 20,
    interleaved: dict | None = None,
) -> dict:
    """Land an optional ghost base plus N rebuilds of one layer plan.

    Bakes in the traps that bent earlier undo fixtures: the base uses pass/stage
    ``other`` (``ghost-ground`` is not an apply stage), and every rebuild lists the
    base in ``modifies``; later rebuilds of the same plan also list the plan.
    ``interleaved`` lands another plan between rebuilds and is named in later
    ``modifies`` (the interleaved-build case the before_<plan> check must handle).

    @param land: ``land(name, pass_, cells, *, cell=None, modifies=())`` apply hook
    @param cell: cell for layer (and base) applies
    @param origin: core (x, y, z) column
    @param shapes: rebuild shapes in order (length = N); default same, wider, narrower
    @param plan_name: layer plan name reused across rebuilds
    @param layer_pass: journal pass for rebuilds (``ground`` / ``trees`` / ``decor``)
    @param base_name: base plan name when ``with_base``
    @param base_pass: base pass — keep ``other`` for live apply (not ``ghost-ground``)
    @param with_base: when False, seed ``blocks`` with ``start_block`` and skip a base owner
    @param blocks: MemoryBlocks (required when ``with_base`` is False)
    @param start_block: world/base fill for every column the fixture touches
    @param rebuild_blocks: block state per rebuild (cycled if shorter than ``shapes``)
    @param far_dx: +x offset for ``wider``
    @param interleaved: optional ``{name, pos, block, after}`` — after rebuild index
        ``after``, land plan ``name`` at ``pos``; later layer rebuilds list it in modifies
    @returns: ``{start, base_name, plan_name, shapes, positions}``
    @raises ValueError: empty shapes, or ``with_base`` False without ``blocks``
    """
    if not shapes:
        raise ValueError('shapes must be non-empty')
    materials = rebuild_blocks or _DEFAULT_REBUILD_BLOCKS
    footprints = [
        layer_rebuild_cells(origin, shape, materials[i % len(materials)], far_dx=far_dx)
        for i, shape in enumerate(shapes)
    ]
    positions: set = set()
    for fp in footprints:
        positions.update(fp)
    inter = None
    if interleaved is not None:
        inter = {
            'name': interleaved['name'],
            'pos': tuple(interleaved['pos']),
            'block': interleaved['block'],
            'after': int(interleaved.get('after', 0)),
        }
        positions.add(inter['pos'])
    start = {p: start_block for p in positions}
    landed_base = None
    if with_base:
        land(base_name, base_pass, dict(start), cell=cell, modifies=())
        landed_base = base_name
    else:
        if blocks is None:
            raise ValueError('with_base=False requires blocks to seed the start world')
        for p, s in start.items():
            blocks.write(p, s)
        blocks.flush()
    for i, fp in enumerate(footprints):
        modifies = []
        if landed_base is not None:
            modifies.append(landed_base)
        if i:
            modifies.append(plan_name)
        if inter is not None and i > inter['after']:
            modifies.append(inter['name'])
        # de-dupe, keep order
        seen = set()
        mod_tuple = tuple(m for m in modifies if m not in seen and not seen.add(m))
        land(plan_name, layer_pass, fp, cell=cell, modifies=mod_tuple)
        if inter is not None and i == inter['after']:
            inter_mod = []
            if landed_base is not None:
                inter_mod.append(landed_base)
            if inter['pos'] in fp:
                inter_mod.append(plan_name)
            seen_i = set()
            inter_mods = tuple(m for m in inter_mod if m not in seen_i and not seen_i.add(m))
            land(inter['name'], 'feature', {inter['pos']: inter['block']}, modifies=inter_mods)
    return {
        'start': start,
        'base_name': landed_base,
        'plan_name': plan_name,
        'shapes': tuple(shapes),
        'positions': frozenset(positions),
    }

# --- live apply harness: site_kit.apply / undo against a fake MCP over MemoryBlocks --------------
class View:
    """``World``-like reader over a BlockStore (air for unknown positions)."""

    def __init__(self, blocks):
        self.blocks = blocks

    def prefetch(self, positions) -> None:
        """Warm the underlying store cache (no-op-compatible with ``World.prefetch``).

        @param positions: iterable of ``(x, y, z)``
        """
        self.blocks.read_many(positions)

    def block(self, x, y, z):
        return self.blocks.read((x, y, z)) or AIR

    def ground(self, x, z, top=140, bottom=40):
        column = [(x, y, z) for y in range(top, bottom - 1, -1)]
        self.prefetch(column)
        for y in range(top, bottom, -1):
            if self.block(x, y, z) != AIR:
                return y
        return bottom


class SavedBlocks(MemoryBlocks):
    """MemoryBlocks whose ``flush`` saves the world over (fake) MCP, like ``LiveBlocks``."""

    def mark_written(self) -> None:
        self._dirty = True

    def flush(self) -> None:
        FakePaced().call('server_save_all_worlds', {})
        super().flush()


class FakePaced:
    """Fake MCP client writing into ``FakePaced.blocks``. Class attributes are reset per test."""
    blocks: MemoryBlocks = MemoryBlocks(default=AIR)
    structures: dict = {}
    calls: list = []
    seen_at_fill: list = []
    fill_reader = None       # callable run at each block_fill_batch (e.g. read the journal)
    fill_limit = None        # abort (raise) after this many fill boxes have been applied
    fills_done = 0
    skip_fills = False       # fills silently do nothing (readback mismatch)
    abort_on_save = False    # raise on server_save_all_worlds (after the writes, before the readback)

    def __init__(self, *a, **k):
        pass

    @classmethod
    def reset(cls, blocks: MemoryBlocks) -> None:
        cls.blocks, cls.structures, cls.calls, cls.seen_at_fill = blocks, {}, [], []
        cls.fill_reader, cls.fill_limit, cls.fills_done = None, None, 0
        cls.skip_fills = cls.abort_on_save = False

    def call(self, name, args):
        FakePaced.calls.append(name)
        if name == 'server_save_all_worlds' and FakePaced.abort_on_save:
            raise RuntimeError('server died')
        return {'content': []}

    def tell(self, msg):
        FakePaced.calls.append('tell')

    def text(self, name, args):
        FakePaced.calls.append(name)
        b = FakePaced.blocks
        if name == 'structure_save_from_world':
            FakePaced.structures[args['name']] = dict(b._blocks)
            return 'saved'
        if name == 'structure_load_to_world':
            b._blocks.clear()
            b._blocks.update(FakePaced.structures[args['name']])
            return 'loaded'
        if name == 'block_fill_batch':
            if FakePaced.fill_reader:
                FakePaced.seen_at_fill.append(FakePaced.fill_reader())
            for f in args['fills']:
                if FakePaced.fill_limit is not None and FakePaced.fills_done >= FakePaced.fill_limit:
                    raise RuntimeError('server died mid-apply')
                lo, hi = f['from'], f['to']
                if not FakePaced.skip_fills:
                    for x in range(lo[0], hi[0] + 1):
                        for y in range(lo[1], hi[1] + 1):
                            for z in range(lo[2], hi[2] + 1):
                                b.write((x, y, z), f['block'])
                FakePaced.fills_done += 1
            return 'filled'
        return ''


class LiveApplyCase(unittest.TestCase):
    """unittest base: ``site_kit.apply`` runs live against FakePaced + MemoryBlocks in a tmp world.

    Patched: site_kit.OUT, the registry root, site_lock.LOCK, World, live_blocks, the player hop,
    MCP, the footprint / roof guards, the region gate, sleeps, and the ghost planner
    (``self.ghosts``, default None). ``self.blocks`` is the world; ``self.reg()`` a view registry.
    """

    ghosts = None

    def setUp(self):
        import site_kit
        import site_lock
        import cell_registry
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        t = self.t = Path(self._tmp.name)
        for d in ('out', 'masks'):
            (t / d).mkdir()
        self.blocks = SavedBlocks(default=AIR)
        FakePaced.reset(self.blocks)
        patches = [
            mock.patch.object(site_kit, 'OUT', t / 'out'),
            mock.patch.object(site_lock, 'LOCK', t / 'LOCK.json'),
            mock.patch.object(site_kit, 'World',
                              lambda region=None, store=None: View(self.blocks)),
            mock.patch.object(site_kit, 'planning_world',
                              lambda region=None: View(self.blocks)),
            mock.patch.object(site_kit, 'fresh_planning_world',
                              lambda region=None, strict=False: View(self.blocks)),
            mock.patch.object(site_kit, 'live_blocks', lambda: self.blocks),
            mock.patch.object(site_kit, 'disk_blocks', lambda: self.blocks),
            mock.patch.object(site_kit, '_bring_player', lambda *a, **k: None),
            mock.patch.object(site_kit, 'ensure_region_matches_open_world', lambda region=None: None),
            mock.patch.object(cell_registry, 'default_ghosts', lambda: self.ghosts),
            # Existing block-safety fixtures explicitly opt out of provider readiness.
            # Readiness integration tests replace this with a synthetic guarded policy.
            mock.patch('readiness_evidence.enforce_live', lambda reg, build, touched: {}),
            mock.patch('mc_paced.Paced', FakePaced),
            mock.patch('osm_footprints.guard', lambda cells: {'outside': {}, 'blind': []}),
            mock.patch('roof_lint.ridge_jogs', lambda cells: []),
            mock.patch('time.sleep', lambda s: None),
            mock.patch.dict(os.environ, {'SITE_OWNER': 'alice', 'AI_WORLDBUILDER_REGISTRY_ROOT': str(t),
                                         'LIDAR_GHOST_RING': '1'}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def reg(self) -> CellRegistry:
        import cell_registry
        return cell_registry.view_registry(self.t)

    def entries(self, plan=None):
        return self.reg().entries(plan=plan)

    def run_apply(self, plan, **kw):
        import site_kit
        import site_lock
        with mock.patch.object(site_lock, 'check', lambda owner=None: None):
            return site_kit.apply(plan, **kw)

    def run_undo(self, target, **kw):
        import site_kit
        import site_lock
        with mock.patch.object(site_lock, 'check', lambda owner=None: None):
            return site_kit.undo(target, **kw)

    def setup_layer_rebuilds(self, *, cell, origin, **kw):
        """Apply :func:`setup_layer_rebuilds` through :meth:`run_apply`.

        Maps journal passes to live stages (``other`` / ``ground-layer`` / …).
        ``ghost-ground`` is not an apply stage — the helper keeps the base on ``other``.

        @param cell: cell tuple
        @param origin: core (x, y, z)
        @param kw: forwarded to :func:`setup_layer_rebuilds` (except ``land``)
        @returns: fixture info dict from :func:`setup_layer_rebuilds`
        """
        import site_kit

        def land(name, pass_, cells, *, cell=None, modifies=()):
            plan = site_kit.Plan(name)
            for p, s in cells.items():
                plan.set(*p, s)
            args = {}
            if pass_ == 'other':
                args.update(stage='other', cell=cell)
            elif pass_ == 'ground':
                args.update(stage='ground-layer', cell=cell)
            elif pass_ in ('trees', 'decor'):
                args.update(stage=pass_, cell=cell)
            if modifies:
                args['modifies'] = list(modifies)
            return self.run_apply(plan, **args)

        kw.setdefault('blocks', self.blocks)
        return setup_layer_rebuilds(land, cell=cell, origin=origin, **kw)




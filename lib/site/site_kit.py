"""Southampton Refine build kit: plan cells offline, preview, back up, batch-fill, read back.

A Plan maps (x, y, z) -> block state string ('minecraft:' optional). Every planned
cell is written, including air (so excavation is explicit). apply():
  1. runs the guards (floating posts, vector-map footprint, door frames, roof jogs, clashes
     with earlier owners in the cell journal, protect),
  2. saves one structure template of the plan's bounding box (+1 margin)
     as southampton_refine:before_<name> (the first time; timestamped afterwards),
  3. journals the plan 'begun' in the cell journal (registry/journal.jsonl, ADR 004),
  4. writes merged boxes with block_fill_batch (<= 8000 entries/call),
     solid blocks first and attachable blocks (doors, signs, torches...) after,
     then sign text and entities,
  5. saves the world, reads every planned cell back through the BlockStore and finishes the
     journal entry, then runs the journal follow-ups (frontier ghost ground).
Guard and readback reports land in verification/site/<name>-*.json.
"""
import json, math, time
from pathlib import Path

import script_env  # noqa: E402

_CFG = None
REGION = None
STRUCTURE_NS = None
OUT = None
D = 'minecraft:overworld'


def clear_world_config() -> None:
    """Drop cached ``world.json`` fields so the next access reloads."""
    global _CFG, REGION, STRUCTURE_NS, OUT
    _CFG = None
    REGION = None
    STRUCTURE_NS = None
    OUT = None


def reload_world_config() -> None:
    """Load ``REGION`` / ``STRUCTURE_NS`` / ``OUT`` from the active world.

    @raises RuntimeError: when no world is selected
    """
    global _CFG, REGION, STRUCTURE_NS, OUT
    if not script_env.world_bound():
        raise RuntimeError(
            'no world selected; pass --world <name> (a directory under worlds/)'
        )
    root = Path(script_env.ROOT)
    _CFG = json.loads((root / 'config/world.json').read_text())
    import active_world
    save = active_world.resolve_save(_CFG.get('save_path'), False)
    REGION = (save / 'dimensions' / 'minecraft' / 'overworld' / 'region') if save else (root / 'save' / 'region')
    STRUCTURE_NS = _CFG.get('structure_namespace', 'southampton_refine')
    OUT = root / 'verification/site'


def _ensure_world_config() -> None:
    """Reload world config when it has not been loaded yet."""
    if _CFG is None:
        reload_world_config()


if script_env.world_bound():
    reload_world_config()
ATTACH = ('door', 'sign', 'torch', 'lantern', 'pane', 'bars', 'bed', 'button', 'lever',
          'trapdoor', 'end_rod', 'chain', 'carpet', 'rail', 'ladder', 'vine', 'banner',
          'flower_pot', 'potted', 'candle', 'rod', 'campfire', 'scaffolding', 'sapling')


def full(s):
    return s if ':' in s.split('[')[0] else 'minecraft:' + s


def bid(s):
    return full(s).split('[')[0]


# Wood slab short names → planks (stripping ``_slab`` alone yields invalid ids).
_WOOD_SLAB = frozenset({
    'oak', 'spruce', 'birch', 'jungle', 'acacia', 'dark_oak', 'mangrove',
    'cherry', 'bamboo', 'crimson', 'warped',
})


def is_bottom_half_slab(state: str) -> bool:
    """True for bottom-half slabs (including default states); false for top-half / double.

    ``block_fill_batch`` often leaves bottom-half slab cells unchanged (air stays
    air). Top-half slabs are left alone.

    @param state: block state string
    @returns: whether this is a bottom-half slab
    """
    st = full(state)
    name = bid(st)
    if not name.endswith('_slab'):
        return False
    if 'type=top' in st or 'type=double' in st:
        return False
    return True


def full_block_for_bottom_half_slab(state: str) -> str | None:
    """Full-block twin for a bottom-half slab, or None if not a bottom-half slab.

    @param state: block state string
    @returns: full block id, or None
    """
    if not is_bottom_half_slab(state):
        return None
    name = bid(state)[len('minecraft:'):-len('_slab')]
    if name in _WOOD_SLAB:
        return f'minecraft:{name}_planks'
    if name == 'deepslate_tile':
        return 'minecraft:deepslate_tiles'
    if name == 'brick':
        return 'minecraft:bricks'
    if name.endswith('_brick'):
        return f'minecraft:{name}s'
    return f'minecraft:{name}'


def rewrite_bottom_half_slabs(plan: 'Plan') -> int:
    """Replace bottom-half slabs with full blocks so ``block_fill`` can place them.

    Skipped when ``plan.keep_bottom_half_slabs`` is truthy (rare; ``McpWriter``
    then places each bottom-half slab with ``block_set_state``, slow). Top-half and double slabs are unchanged. Cells in
    ``plan.keep_slab_cells`` (pitched-roof odd-half-height steps, #207) keep their
    bottom-half slab; ``McpWriter`` places those with ``block_set_state`` after the fills.

    @param plan: plan to mutate
    @returns: number of cells rewritten
    """
    if getattr(plan, 'keep_bottom_half_slabs', False):
        return 0
    n = 0
    keep = plan.keep_slab_cells
    for pos, st in list(plan.cells.items()):
        if pos in keep:
            continue
        twin = full_block_for_bottom_half_slab(st)
        if twin is None or twin == st:
            continue
        plan.cells[pos] = twin
        n += 1
    return n


class Plan:
    def __init__(self, name):
        from readiness_evidence import planning_revision
        self.evidence_revision = planning_revision()
        self.name = name
        self.cells = {}
        self.signs = []      # (pos, [4 lines], colour, glowing)
        self.entities = []   # (entity_type, (x,y,z), snbt or None)
        self.keep_slab_cells = set()  # bottom-half slabs exempt from rewrite_bottom_half_slabs (roof steps)

    # --- primitives -------------------------------------------------------
    def set(self, x, y, z, s):
        """Record one block in the plan.

        @param x: block X
        @param y: block Y
        @param z: block Z
        @param s: block id or short name
        @raises SystemExit: if ``s`` is a sapling (project rule: never build saplings)
        """
        st = full(s)
        if 'sapling' in st and not getattr(self, 'allow_saplings', False):
            raise SystemExit(
                f'{self.name}: sapling blocks are forbidden ({st} at {x},{y},{z}); '
                'they grow on random tick (not idempotent) — use static log+leaves or clear only',
            )
        self.cells[(x, y, z)] = st
        self.keep_slab_cells.discard((x, y, z))  # a later write replaces the kept slab

    def fill(self, a, b, s):
        """Fill an axis-aligned box with one block state.

        @param a: corner [x,y,z]
        @param b: opposite corner
        @param s: block id or short name
        @raises SystemExit: if ``s`` is a sapling
        """
        (x0, x1), (y0, y1), (z0, z1) = (sorted((a[i], b[i])) for i in range(3))
        st = full(s)
        if 'sapling' in st and not getattr(self, 'allow_saplings', False):
            raise SystemExit(f'{self.name}: sapling blocks are forbidden in fill ({st})')
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    self.cells[(x, y, z)] = st
                    self.keep_slab_cells.discard((x, y, z))

    def get(self, x, y, z):
        return self.cells.get((x, y, z))

    def sign(self, pos, lines, colour='black', glow=False):
        self.signs.append((tuple(pos), (list(lines) + ['', '', '', ''])[:4], colour, glow))

    def entity(self, kind, pos, snbt=None):
        self.entities.append((kind, tuple(pos), snbt))

    def bounds(self):
        xs, ys, zs = zip(*self.cells)
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    # --- compression into boxes ------------------------------------------
    def boxes(self):
        """Greedy merge: x-runs, then along z, then along y. Returns [(lo, hi, state)]."""
        rows = {}
        for (x, y, z), s in self.cells.items():
            rows.setdefault((y, z), []).append((x, s))
        runs = []
        for (y, z), xs in rows.items():
            xs.sort()
            start, prev, cur = xs[0][0], xs[0][0], xs[0][1]
            for x, s in xs[1:]:
                if x == prev + 1 and s == cur:
                    prev = x
                    continue
                runs.append((start, prev, y, z, cur))
                start, prev, cur = x, x, s
            runs.append((start, prev, y, z, cur))
        # merge along z
        byk = {}
        for x0, x1, y, z, s in runs:
            byk.setdefault((x0, x1, y, s), []).append(z)
        slabs = []
        for (x0, x1, y, s), zs in byk.items():
            zs.sort()
            a = p = zs[0]
            for z in zs[1:]:
                if z == p + 1:
                    p = z
                    continue
                slabs.append((x0, x1, a, p, y, s)); a = p = z
            slabs.append((x0, x1, a, p, y, s))
        # merge along y
        byk = {}
        for x0, x1, z0, z1, y, s in slabs:
            byk.setdefault((x0, x1, z0, z1, s), []).append(y)
        out = []
        for (x0, x1, z0, z1, s), ys in byk.items():
            ys.sort()
            a = p = ys[0]
            for y in ys[1:]:
                if y == p + 1:
                    p = y
                    continue
                out.append(((x0, a, z0), (x1, p, z1), s)); a = p = y
            out.append(((x0, a, z0), (x1, p, z1), s))
        attach = lambda s: any(k in bid(s) for k in ATTACH)
        # clear (air) first, then solids bottom-up, then attachables: surfaces such as
        # dirt_path or crops are never placed under terrain that is still to be cleared
        out.sort(key=lambda b: (attach(b[2]), bid(b[2]) != 'minecraft:air', b[0][1], b[0][0], b[0][2]))
        return out

    def summary(self):
        from collections import Counter
        c = Counter(bid(s) for s in self.cells.values())
        lo, hi = self.bounds()
        return {'name': self.name, 'cells': len(self.cells), 'boxes': len(self.boxes()),
                'bounds': [lo, hi], 'materials': dict(c.most_common()),
                'signs': len(self.signs), 'entities': len(self.entities)}


# --- offline preview ------------------------------------------------------
# Named colours for top-down preview and for roof-colour suggestions (#126).
PREVIEW_COLOURS: list[tuple[str, tuple[int, int, int]]] = [
    ('black_concrete', (25, 25, 30)), ('gray_concrete', (60, 64, 68)),
    ('light_gray_concrete', (125, 125, 115)), ('white_concrete', (210, 214, 215)),
    ('deepslate', (70, 70, 75)), ('blackstone', (45, 40, 48)), ('tinted', (45, 35, 55)),
    ('magenta', (200, 60, 190)), ('pink', (235, 140, 170)), ('cyan', (30, 150, 160)),
    ('light_blue', (90, 170, 220)), ('lime', (120, 200, 30)), ('purple', (120, 40, 170)),
    ('yellow', (240, 200, 40)), ('orange', (230, 110, 20)), ('red', (160, 40, 35)),
    ('sea_lantern', (200, 230, 225)), ('froglight', (240, 220, 200)), ('glowstone', (250, 210, 120)),
    ('iron_chain', (60, 60, 70)), ('end_rod', (240, 240, 240)), ('iron', (200, 200, 200)),
    ('copper', (90, 160, 140)), ('stone_brick', (120, 120, 120)), ('glass', (170, 200, 210)),
    ('andesite', (135, 135, 135)), ('smooth_stone', (160, 160, 160)), ('stone', (125, 125, 125)),
    ('spruce', (100, 75, 45)), ('oak', (160, 130, 80)), ('dark_oak', (65, 45, 25)),
    ('bed', (180, 40, 40)), ('barrel', (120, 90, 50)), ('lantern', (250, 200, 100)),
]


def nearest_preview_colour(rgb: tuple[int, int, int]) -> tuple[str, tuple[int, int, int]]:
    """Nearest named entry in ``PREVIEW_COLOURS`` to an 0..255 RGB triple.

    Used by feature_build dry-run to suggest a roof block from aerial mean colour
    (suggestion only — not a gate).

    @param rgb: target colour (R, G, B) each 0..255
    @returns: ``(name, table_rgb)`` of the closest table entry
    """
    return min(
        PREVIEW_COLOURS,
        key=lambda t: sum((a - b) ** 2 for a, b in zip(t[1], rgb)),
    )


def colour(s):
    b = bid(s)[10:]
    if b in ('air', 'cave_air', 'light', 'void_air'):
        return None
    for k, c in PREVIEW_COLOURS:
        if k in b:
            return c
    return (150, 120, 150)


def preview(plan, path, scale=4):
    """Top view plus the four elevations, written as one PNG (needs matplotlib)."""
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    (x0, y0, z0), (x1, y1, z1) = plan.bounds()
    W, H, L = x1 - x0 + 1, y1 - y0 + 1, z1 - z0 + 1
    vox = {}
    for (x, y, z), s in plan.cells.items():
        c = colour(s)
        if c:
            vox[(x - x0, y - y0, z - z0)] = c

    def project(axes, depth_axis, rev, shape):
        img = np.ones(shape + (3,)) * 0.08
        dep = np.full(shape, -1e9)
        for p, c in vox.items():
            u, v = p[axes[0]], p[axes[1]]
            d = p[depth_axis] * (-1 if rev else 1)
            if d > dep[v, u]:
                dep[v, u] = d
                img[v, u] = np.array(c) / 255
        return img

    views = {
        'top (north up)': project((0, 2), 1, False, (L, W)),
        'east face (looking west)': project((2, 1), 0, False, (H, L))[::-1, ::-1],
        'south face (looking north)': project((0, 1), 2, False, (H, W))[::-1],
        'west face (looking east)': project((2, 1), 0, True, (H, L))[::-1],
        'north face (looking south)': project((0, 1), 2, True, (H, W))[::-1, ::-1],
    }
    fig, axs = plt.subplots(1, 5, figsize=(5 * 4, 6), dpi=110)
    for ax, (t, im) in zip(axs, views.items()):
        ax.imshow(im, interpolation='nearest')
        ax.set_title(t, fontsize=9)
        ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle(f'{plan.name}: X{x0}..{x1} Y{y0}..{y1} Z{z0}..{z1}')
    plt.tight_layout()
    plt.savefig(path)
    plt.close(fig)


# --- region-file readback --------------------------------------------------
AIR_IDS = ('air', 'cave_air', 'void_air')      # matched exactly: a substring test also hit *_stairs (Q-181)


class PlannedWorld:
    """Read a stable plan snapshot over a saved world, optionally clearing undone positions.

    Explicit planned air wins over saved blocks. Snapshotting keeps callers' subsequent
    edits from changing the geometry they are still reading.
    """
    def __init__(self, plan, world, ignore=()):
        self.cells = dict(plan.cells)
        self.world = world
        self.ignore = frozenset(ignore)

    def block(self, x, y, z):
        pos = (x, y, z)
        if pos in self.cells:
            return self.cells[pos]
        return 'minecraft:air' if pos in self.ignore else self.world.block(x, y, z)

    def prefetch(self, positions) -> None:
        """Warm the underlying world for positions not served from the plan overlay.

        Positions in ``self.cells`` (or ``ignore``) are answered locally and must not
        trigger live MCP scans.

        @param positions: iterable of ``(x, y, z)``
        """
        warm = getattr(self.world, 'prefetch', None)
        if warm is None:
            return
        warm(p for p in positions if p not in self.cells and p not in self.ignore)


def resolve_region(region=None) -> Path:
    """Region folder for World readback: explicit arg, else active world config.

    The save always comes from the active world's ``config/world.json``
    (selected via ``--world``). Pass ``region`` only for tests or
    one-off probes against another folder.

    @param region: optional region directory (wins over config)
    @returns: absolute path to the ``…/overworld/region`` folder
    """
    if region is not None:
        return Path(region)
    _ensure_world_config()
    return Path(REGION)


def gameworld_id_from_region(region: Path) -> str:
    """Save-folder identity for a region path (``…/saves/<id>/dimensions/…/region``).

    @param region: overworld region directory
    @returns: save folder name (e.g. ``bakeoff``, ``SouthamptonAI``)
    @raises ValueError: when the path does not contain a ``saves/<id>/`` segment
    """
    parts = Path(region).resolve().parts
    for i, part in enumerate(parts):
        if part == 'saves' and i + 1 < len(parts):
            return parts[i + 1]
    raise ValueError(f'cannot derive gameworld id from region path: {region}')


def mcp_reported_gameworld(*, region: Path | None = None) -> str | None:
    """Identity of the Minecraft save the MCP bridge is currently serving.

    MCP runs inside the open client world. That save holds ``session.lock`` under
    the same ``instance/saves/`` tree as ``region``. Returns None when MCP is
    down or no lock is found.

    @param region: region used to locate the instance ``saves/`` parent
    @returns: save folder name, or None when unknown
    """
    import mcp_probe

    if not mcp_probe.mcp_world_reachable():
        return None
    try:
        region_path = resolve_region(region).resolve()
    except OSError:
        return None
    saves_root = region_path
    while saves_root.name != 'saves' and saves_root != saves_root.parent:
        saves_root = saves_root.parent
    if saves_root.name != 'saves' or not saves_root.is_dir():
        return None
    open_names = [
        child.name
        for child in saves_root.iterdir()
        if child.is_dir() and (child / 'session.lock').is_file()
    ]
    if len(open_names) == 1:
        return open_names[0]
    return None


def ensure_region_matches_open_world(region=None) -> None:
    """Refuse when World region identity differs from the MCP-open save.

    @param region: optional region directory (same rules as :func:`resolve_region`)
    @raises SystemExit: when the open world and region target disagree
    """
    target = resolve_region(region)
    try:
        expected = gameworld_id_from_region(target)
    except ValueError:
        return
    reported = mcp_reported_gameworld(region=target)
    if reported is None:
        return
    if reported.casefold() == expected.casefold():
        return
    raise SystemExit(
        f'refuse apply: World region targets {expected!r} but MCP is serving '
        f'{reported!r}. Pass --world for the world whose config/world.json '
        f'matches the open save (or open the matching world).'
    )


def warm_reads(world, positions) -> None:
    """Prefetch ``positions`` when ``world`` supports it; no-op for stubs.

    Call before a loop of ``block(x, y, z)`` over a known set so LiveBlocks can batch
    MCP ``block_scan_region`` calls (one scan per tile, not one per block).

    @param world: :class:`World`, :class:`PlannedWorld`, or any duck with optional ``prefetch``
    @param positions: iterable of ``(x, y, z)``
    """
    warm = getattr(world, 'prefetch', None)
    if warm is not None:
        warm(positions)


def forget_reads(world, positions) -> None:
    """Drop ``positions`` from a live scan cache when ``world`` supports it; no-op for stubs.

    Pair with :func:`warm_reads` for strip-bounded probes so retained cache does not
    grow to the full bbox.

    @param world: :class:`World` or any duck with optional ``forget``
    @param positions: iterable of ``(x, y, z)``
    """
    cool = getattr(world, 'forget', None)
    if cool is not None:
        cool(positions)


def planning_world(region=None) -> 'World':
    """``World`` over :class:`region_blocks.RegionBlocks` for dry-run / planner reads.

    Prefer :func:`fresh_planning_world` so a server save runs first. Live applies keep
    :func:`World` / :class:`~block_store.LiveBlocks` (#111). Missing chunks return ``None``
    (unlike live MCP, which lists air).

    @param region: optional region directory (default: active world)
    @returns: World facade over a read-only region-file store
    """
    from region_blocks import RegionBlocks
    store = RegionBlocks(resolve_region(region)) if region is not None else RegionBlocks.from_world_config()
    return World(region=store.region, store=store)


def fresh_planning_world(region=None, *, strict: bool = False) -> 'World':
    """``server_save_all_worlds`` then :func:`planning_world` so disk matches the open world.

    @param region: optional region directory (default: active world)
    @param strict: when True, a failed save raises ``SystemExit``; otherwise print and continue
    @returns: World over on-disk region files
    """
    try:
        from mc_paced import Paced
        Paced().call('server_save_all_worlds', {})
    except Exception as exc:  # noqa: BLE001 — MCP may be down during offline dry runs
        if strict:
            raise SystemExit(f'could not flush the world before reading it: {exc}') from exc
        print(f'  (could not flush the world before reading: {exc})')
    return planning_world(region)


class World:
    """Duck-typed block access over a :class:`block_store.BlockStore`.

    Default store is :class:`block_store.LiveBlocks` (MCP ``block_scan_region``) for the
    active world (live apply / readback). Inject ``store=`` in tests, or use
    :func:`planning_world` for dry-run / planner reads over on-disk ``.mca`` after a save
    (#111). Unloaded / omitted chunks follow the BlockStore contract (``None`` under
    FakeScanMcp with ``loaded_chunks`` or a missing region chunk; live MCP lists
    ``minecraft:air`` — see ``docs/audits/region-block-reads.md`` §7).
    """

    def __init__(self, region=None, *, store=None):
        """
        @param region: region directory for identity / ``self.region`` (ignored when ``store``
            already carries a ``region``)
        @param store: BlockStore to read; default is lazy LiveBlocks for the active world
        """
        self._store = store
        self._region_arg = region
        if store is not None and getattr(store, 'region', None) is not None:
            self.region = Path(store.region)
        else:
            self.region = resolve_region(region)

    def _blocks(self):
        """Lazy LiveBlocks so ``World().region`` works in unit tests without MCP."""
        if self._store is None:
            from block_store import LiveBlocks
            if self._region_arg is not None:
                self._store = LiveBlocks(self.region)
            else:
                self._store = LiveBlocks.from_world_config()
        return self._store

    def prefetch(self, positions) -> None:
        """Warm the store cache for many positions in as few MCP scans as possible.

        Call this before a loop of :meth:`block` over a known set (plan cells, a column, …).

        @param positions: iterable of ``(x, y, z)``
        """
        self._blocks().read_many(positions)

    def forget(self, positions) -> None:
        """Drop ``positions`` from the store scan cache when the store supports it.

        No-op for :class:`~block_store.MemoryBlocks`. Used with strip-warm probes so
        retained LiveBlocks memory stays bounded.

        @param positions: iterable of ``(x, y, z)``
        """
        cool = getattr(self._blocks(), 'forget', None)
        if cool is not None:
            cool(positions)

    def block(self, x, y, z):
        """Block state at ``(x, y, z)``, or None when the store reports unloaded/unknown.

        Prefer :meth:`prefetch` or :meth:`ground` for columns / plan volumes so MCP is batched.

        @param x: block X
        @param y: block Y
        @param z: block Z
        @returns: canonical state string, or None
        """
        return self._blocks().read((x, y, z))

    def uniform_air(self, x, y, z, airs) -> bool:
        """True when every block in the 16-high section is unloaded or its id is in ``airs``.

        Replaces the old section-palette shortcut: samples the column via ``read_many``.

        @param x: block X
        @param y: any Y in the section
        @param z: block Z
        @param airs: set/tuple of namespaced air ids (e.g. ``minecraft:air``)
        @returns: True when the section may be skipped in a top-down scan
        """
        y0 = (y >> 4) << 4
        positions = [(x, yy, z) for yy in range(y0, y0 + 16)]
        for st in self._blocks().read_many(positions).values():
            if st is None:
                continue
            if st.split('[')[0] not in airs:
                return False
        return True

    def light(self, x, y, z, kind='BlockLight'):
        """Light level via the store when supported (LiveBlocks: MCP ``block_get_state``).

        SkyLight and BlockLight are not distinguished on the MCP tool (one ``lightLevel``).
        Stores without light support return None.

        @param x: block X
        @param y: block Y
        @param z: block Z
        @param kind: ignored (kept for call-site compatibility)
        @returns: 0–15, or None when unavailable
        """
        del kind  # MCP exposes a single lightLevel
        store = self._blocks()
        fn = getattr(store, 'light', None)
        if fn is None:
            return None
        return fn((x, y, z))

    def ground(self, x, z, top=140, bottom=40):
        """Highest non-air, non-plant Y in the column (same skip list as before).

        Batches the column with ``read_many`` (one MCP tile set, not one scan per Y).

        @param x: block X
        @param z: block Z
        @param top: scan start Y
        @param bottom: scan floor Y
        @returns: ground Y, or ``bottom`` when none found
        """
        skip = ('water', 'leaves', '_log', 'short_grass', 'tall_grass', 'fern', 'flower', 'tulip', 'poppy',
                'dandelion', 'kelp', 'seagrass', 'bush', 'litter', 'vine', 'daisy', 'bluet', 'orchid',
                'allium', 'cornflower', 'lily', 'sugar_cane', 'mushroom')
        column = [(x, y, z) for y in range(top, bottom - 1, -1)]
        # Batch via BlockStore when this is a real World (has ``_store`` from __init__).
        # Test fakes that only override ``block`` and skip __init__ stay on the per-Y path.
        if hasattr(self, '_store'):
            states = self._blocks().read_many(column)
            def at(yy):
                return states.get((x, yy, z))
        else:
            def at(yy):
                return self.block(x, yy, z)
        for y in range(top, bottom, -1):
            b = at(y)
            if b and b.split('[')[0].removeprefix('minecraft:') not in AIR_IDS and not any(k in b for k in skip):
                return y
        return bottom


def same(planned, actual):
    """Compare id and the planned properties (unplanned properties may differ;
    skips waterlogged, connectable neighbour props, and stair ``shape`` — see ``block_store.same_state``)."""
    from block_store import same_state
    return same_state(planned, actual)


# --- the cell journal (ADR 004) ----------------------------------------------
# Plan -> Build: Build(plan.name, pass, plan.cells, modifies, strict, mask summary, cell,
# payload=plan, meta={structure, pending_mask, caller meta}). Signs and entities on the Plan are
# journalled on the begin line (and on Writes) so reconcile/recover can rewrite them; block cells
# stay in the plan blob.
STAGE_PASS = {
    'ground-layer': 'ground', 'ground': 'ground', 'trees': 'trees', 'decor': 'decor', 'other': 'other',
}


def stage_pass(stage: str | None) -> str:
    """Journal pass for an ``apply(stage=...)`` key.

    @param stage: None / ``feature-<name>`` (feature), ``ground-layer`` / ``ground``, ``trees``,
        ``decor``, ``other`` (blocks with no progress meaning: markers, clean-ups)
    @returns: cell_journal pass name
    @raises ValueError: unknown stage (undo goes through :func:`undo`, ghosts through lidar_ghost)
    """
    if stage is None or stage.startswith('feature-'):
        return 'feature'
    if stage in STAGE_PASS:
        return STAGE_PASS[stage]
    raise ValueError(f'unknown apply stage {stage!r}; want feature-<name>, '
                     f'{", ".join(sorted(STAGE_PASS))} (undo: site_kit.undo)')


def primary_cell(positions) -> tuple[int, int]:
    """The cell holding most of the given (x, y, z) positions.

    @param positions: iterable of (x, y, z)
    @returns: (tx, tz)
    """
    from collections import Counter
    import cellmath
    return Counter(cellmath.cell_of(x, z) for x, _y, z in positions).most_common(1)[0][0]


def live_blocks():
    """BlockStore over the active world via MCP ``block_scan_region`` (tests patch this).

    @returns: ``block_store.LiveBlocks``
    """
    from block_store import LiveBlocks
    return LiveBlocks.from_world_config()


def disk_blocks():
    """Save and settle the active world, then return its read-only region store.

    Uses the live identity gate and flusher; save failures abort the dry run.

    @returns: ``region_blocks.RegionBlocks``
    """
    from region_blocks import RegionBlocks
    live = live_blocks()
    live.flush()
    return RegionBlocks(live.region)


def _block_arg(state: str) -> dict:
    """``block_set_state`` ``block`` argument from a block state string.

    @param state: e.g. ``minecraft:brick_slab[type=bottom,waterlogged=false]``
    @returns: ``{'id': ..., 'properties': {...}}``
    """
    st = full(state)
    props = {}
    if '[' in st:
        for kv in st[st.index('[') + 1:st.rindex(']')].split(','):
            k, _, v = kv.partition('=')
            props[k.strip()] = v.strip()
    return {'id': bid(st), 'properties': props}


class McpWriter:
    """``apply`` callable for :class:`cell_journal.CellRegistry`: writes ``Writes.cells`` over MCP.

    Boxes are merged with :meth:`Plan.boxes` (air first, attachables last) and sent with
    ``block_fill_batch`` (<= 8000 per call). Signs and entities come from ``Writes`` (journalled
    on begin) or, for a live apply, from a :class:`Plan` payload. The player is hopped over far
    builds so their chunks load (:func:`_bring_player`).

    @param m: MCP client (default: a new ``mc_paced.Paced``)
    """

    def __init__(self, m=None):
        self.m = m
        self.fill_results: list = []
        self.entities: list = []

    @staticmethod
    def _check_slab(pos, res) -> None:
        """Fail loudly when a ``block_set_state`` slab placement reports failure.

        ``Client.call`` already raises on ``isError``; this catches a success-shaped
        result whose text starts with ``error`` / ``failed``.

        @param pos: slab cell
        @param res: raw MCP tool result
        @raises RuntimeError: with the slab position when the text reports failure
        """
        text = '\n'.join(x.get('text', '') for x in (res or {}).get('content', []))
        if text.lstrip().lower().startswith(('error', 'failed')):
            raise RuntimeError(f'bottom-half slab at {pos} not placed: {text[:200]}')

    def __call__(self, writes) -> None:
        from mc_paced import Paced, xyz
        m = self.m = self.m or Paced()
        payload = getattr(writes.intent, 'payload', None)
        plan = payload if isinstance(payload, Plan) and writes.kind == 'build' else None
        if plan is None:
            plan = Plan(writes.plan)
            plan.cells = {tuple(p): full(s) for p, s in writes.cells.items()}
        if not plan.cells:
            return
        lo, hi = plan.bounds()
        label = getattr(plan, 'name', None) or writes.plan
        pass_ = getattr(writes, 'pass_', None) or 'build'
        why = f'Applying {label} ({pass_})'
        # fills into unloaded chunks silently do nothing: if the player is far from the build,
        # hop them (spectator) over it while filling, then put them back exactly
        restore = _bring_player(
            m, lo, hi,
            why=f'{why}: moving you over the build so chunks load; I will put you back.',
        )
        if restore is None:
            from mc_player import tell_player
            tell_player(why, m=m)
        try:
            boxes = plan.boxes()
            for i in range(0, len(boxes), 8000):
                chunk = boxes[i:i + 8000]
                res = m.text('block_fill_batch', {'dimension': D, 'fills': [
                    {'from': list(a), 'to': list(b), 'block': s} for a, b, s in chunk]})
                self.fill_results.append(res)
                print('fill:', res.replace('\n', '; ')[:300])
            # block_fill leaves bottom-half slab cells as air: any bottom-half slab that survived the
            # apply rewrite (pitched-roof steps, #207) is placed one by one
            for pos, st in sorted(plan.cells.items()):
                if is_bottom_half_slab(st):
                    res = m.call('block_set_state', {'dimension': D, 'position': xyz(pos),
                                                     'block': _block_arg(st)})
                    self._check_slab(pos, res)
            signs = writes.signs or (plan.signs if plan is payload else ())
            entities = writes.entities or (plan.entities if plan is payload else ())
            for pos, lines, col, glow in signs:
                nbt = ('{is_waxed:1b,front_text:{messages:[' + ','.join(json.dumps(t) for t in lines) +
                       f'],color:"{col}",has_glowing_text:{1 if glow else 0}b}},back_text:{{messages:[' +
                       ','.join(json.dumps(t) for t in lines) + f'],color:"{col}",has_glowing_text:{1 if glow else 0}b}}}}')
                m.call('block_entity_set_nbt', {'dimension': D, 'position': xyz(pos), 'nbt': nbt})
            for kind, pos, snbt in entities:
                args = {'dimension': D, 'entity_type': kind, 'position': xyz(pos)}
                if snbt:
                    args['nbt'] = snbt
                self.entities.append(m.text('entity_summon', args))
        finally:
            if restore:
                restore()


def live_session(*, ghosts='default', ring=None, root=None):
    """Registry over the live world plus an MCP writer, after the apply gates (region, lock).

    For journal changes outside :func:`apply` (ghost Builds, follow-ups).

    @param ghosts: ghost planner (``'default'``: lidar)
    @param ring: frontier depth override
    @param root: world root of the registry (default: the active world)
    @returns: (CellRegistry, McpWriter)
    @raises SystemExit: region / lock gate refused
    """
    import cell_registry
    import site_lock
    ensure_region_matches_open_world()
    site_lock.check()
    return cell_registry.open_registry(root, blocks=live_blocks(), ghosts=ghosts, ring=ring), McpWriter()


def _structure_backup(m, name, lo, hi):
    """Save a structure template of a box (+1 margin); the guard before every live write."""
    from mc_paced import box
    a = [lo[0] - 1, lo[1], lo[2] - 1]
    b = [hi[0] + 1, hi[1], hi[2] + 1]
    res = m.text('structure_save_from_world', {'name': name, 'dimension': D, 'box': box(a, b)})
    if 'saved' not in res:
        raise SystemExit('Backup failed: ' + res)
    return {'name': name, 'from': a, 'to': b}, res


def _journal_refusal(name, exc):
    """SystemExit for a journal refusal at begin (nothing journalled, no block written)."""
    from cell_journal import InFlight
    if isinstance(exc, InFlight):
        return SystemExit(f'{name}: {exc}; an earlier apply was aborted '
                          '(python scripts/site_recover.py status)')
    return SystemExit(f'{name}: cell journal refused the apply: {exc}')


def reset_to_base(build, records):
    """Plan that restores follow-up record cells from the base build's recorded plan.

    For every cell written by any name in ``records``, put back the state recorded for
    ``build`` in the cell journal (or air if that cell was not in the base). Never uses the
    merged ownership: an earlier step's air can cancel the roof there and leave holes
    (FEATURE FOLLOW-UP STEPS in rules/craftsmanship.md). Apply with
    ``modifies=(build, *records)``.

    @param build: base feature name (e.g. ``highfield_susu_west``)
    @param records: iterable of follow-up record names (e.g. ``highfield_susu_west-lower``)
    @returns: Plan named ``{build}-reset``
    @raises SystemExit: if the base build is not in the journal
    """
    import cell_registry
    reg = cell_registry.view_registry()
    base = reg.recorded(build)
    if base is None:
        raise SystemExit(f'reset_to_base: no recorded build {build!r} in the cell journal')
    plan = Plan(f'{build}-reset')
    for name in records:
        for q in reg.recorded(name) or {}:
            plan.set(*q, base.get(q, 'minecraft:air'))
    return plan


def door_frame_issues(plan, block):
    """Doors in a plan that are not framed: a lower door half whose two side cells (along the wall) or whose lintel
    (the cell above the upper half) is air at either door height (user, 2026-10-16: a door set in a stepped corner had no jamb).

    @param plan: Plan to check
    @param block: callable (x, y, z) -> block id/state or None, used for cells the plan does not set (the live world)
    @returns: [(x, y, z, 'jamb west' | 'jamb east' | ... | 'lintel')] one entry per missing piece
    """
    def solid(x, y, z):
        st = plan.cells.get((x, y, z))
        if st is None:
            st = block(x, y, z)
        return bool(st) and bid(st)[10:] not in ('air', 'cave_air', 'void_air')

    out = []
    for (x, y, z), st in plan.cells.items():
        n = bid(st)[10:]
        if not n.endswith('_door') or 'half=lower' not in st:
            continue
        face = st.split('facing=')[1].split(',')[0].rstrip(']')
        sides = ((1, 0), (-1, 0)) if face in ('north', 'south') else ((0, 1), (0, -1))
        for dx, dz in sides:
            for dy in (0, 1):
                if not solid(x + dx, y + dy, z + dz):
                    out.append((x, y + dy, z, f'jamb {"east" if dx > 0 else "west" if dx < 0 else "south" if dz > 0 else "north"}'))
        if not solid(x, y + 2, z):
            out.append((x, y + 2, z, 'lintel'))
    return out




def guard_door_frames(plan, block):
    """Refuse a plan with unframed doors: every door needs a jamb either side and a lintel (a door in air
    can be walked round). Shared by the live apply and the offline plan step so the refusal is identical.

    @param plan: Plan to check
    @param block: callable (x, y, z) -> block id/state or None for cells the plan does not set
    @raises SystemExit: when pieces are missing and ``plan.allow_unframed_doors`` is not set
    """
    bad = door_frame_issues(plan, block)
    if bad and not getattr(plan, 'allow_unframed_doors', False):
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f'{plan.name}-door-frames.json').write_text(json.dumps([list(b) for b in bad], indent=1))
        raise SystemExit(f'{len(bad)} door frame piece(s) missing, e.g. {bad[:3]}; see {plan.name}-door-frames.json. '
                         'Doors go in a wall with a jamb each side and a lintel above. '
                         'A door that is really free-standing: set plan.allow_unframed_doors = "<reason>".')


def guard_roof_jogs(plan):
    """Refuse a plan whose roof ridge shifts sideways between rows (I-034: it zig-zags every stair on the seam).
    Shared by the live apply and the offline plan step so the refusal is identical.

    @param plan: Plan to check
    @raises SystemExit: when ridges jog and ``plan.roof_jog_ok`` is not set
    """
    import roof_lint
    jogs = roof_lint.ridge_jogs(plan.cells)
    if jogs:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f'{plan.name}-roof-jogs.json').write_text(json.dumps(jogs, indent=1))
        why = getattr(plan, 'roof_jog_ok', None)
        if not why:
            raise SystemExit(
                f'{len(jogs)} roof ridge(s) jog sideways between rows; see {plan.name}-roof-jogs.json. '
                'Keep each roof on one straight ridge line (square the footprint, do not step it '
                'with the vector-map rotation). Real offset roofs (evidence in hand): set '
                'plan.roof_jog_ok = "<reason + evidence id>".')
        print(f'roof jog guard overridden ({len(jogs)} seams): {why}')


def apply(plan, protect=None, dry=False, modifies=(), stage=None, cell=None, meta=None, strict=False, mask=None):
    """protect: optional callable(pos, existing_state) -> bool; True = refuse to overwrite.
    Cells owned by earlier builds are always protected unless that build is in `modifies`.

    Recording (ADR 004): the cell journal (``cell_journal.CellRegistry``) is the only record.
    Before the first live block write the plan is journalled ``begun`` (fsynced) as a Build of
    the pass named by ``stage`` (default and 'feature-<plan>': feature; 'ground-layer' / 'ground',
    'trees', 'decor', 'other'). After the flush and readback it is finished ``landed`` (or
    ``mismatched``); a landed real apply then runs the journal follow-ups (ghost features under a
    feature stripped, the lidar ghost frontier ensured; real ground supersedes ghost ground in the
    view). An abort leaves the entry ``begun`` for site_recover.py. ``cell`` defaults to the cell
    holding most of the plan; ``meta`` is journalled with the entry. ``strict``: a readback with
    mismatches raises and leaves the entry 'begun'. ``mask``: optional pass mask, durably staged
    before writes and published only after a clean readback; publication failure leaves the
    entry 'begun' for recovery. A feature apply fails closed on unplanned ghost feature blocks
    left in its columns."""
    from mc_paced import Paced
    import cell_registry
    from cell_journal import Build, CellJournalError, Clash, InFlight, Refused
    _ensure_world_config()
    pass_ = stage_pass(stage)
    if not dry:
        ensure_region_matches_open_world()
        # Fail before live prefetch, lint scans, backup or begin. The live registry
        # repeats this under the lock immediately before it stages the build.
        import cellmath
        from readiness_evidence import enforce_live
        readiness_view = cell_registry.view_registry()
        touched = {cellmath.cell_of(x, z) for x, _y, z in plan.cells}
        if cell is not None:
            touched.add(tuple(cell))
        try:
            enforce_live(readiness_view, Build(plan.name, pass_, plan.cells, cell=cell,
                                               payload=plan, meta=meta), touched)
        except CellJournalError as exc:
            raise _journal_refusal(plan.name, exc) from exc
    OUT.mkdir(parents=True, exist_ok=True)
    empty = not plan.cells
    # Empty trees/decor plans need an explicit cell (journalled "nothing to place", #127).
    if empty and cell is None:
        raise SystemExit(f'{plan.name}: empty plan needs cell=')
    lo = hi = None
    if not empty:
        lo, hi = plan.bounds()
    # Live path: MCP LiveBlocks after the region/open-world gate. Dry path: read-only
    # region files after a server save (#111) — no MCP warm-up.
    if dry:
        w = fresh_planning_world()
    else:
        w = World()
        if not empty:
            warm = set(plan.cells)
            warm.update((x, y - 1, z) for x, y, z in plan.cells)
            for (x, y, z), st in plan.cells.items():
                n = bid(st)[10:]
                if n.endswith('_door') and 'half=lower' in st:
                    face = st.split('facing=')[1].split(',')[0].rstrip(']')
                    sides = ((1, 0), (-1, 0)) if face in ('north', 'south') else ((0, 1), (0, -1))
                    for dx, dz in sides:
                        for dy in (0, 1):
                            warm.add((x + dx, y + dy, z + dz))
                    warm.add((x, y + 2, z))
            w.prefetch(warm)
    # lint: posts (fences, walls, bars) over half-height blocks float by half a block
    floats = []
    for (x, y, z), st in plan.cells.items():
        n = bid(st)[10:]
        if (n.endswith(('_fence', '_wall')) or n == 'iron_bars') and 'gate' not in n:
            below = plan.cells.get((x, y - 1, z)) or w.block(x, y - 1, z) or ''
            if ('_slab' in below and 'type=bottom' in below) or bid(below).endswith('_carpet'):
                floats.append((x, y, z))
    if floats and not getattr(plan, 'allow_float', False):
        raise SystemExit(f'{len(floats)} posts would float over half-height blocks, e.g. {floats[:5]}; '
                         'extend them down or put a full block under them (or set plan.allow_float)')
    # guard (I-009): building-like blocks standing >2 above lidar ground outside every vector-map
    # footprint are almost always a façade built on the pavement / front garden
    import osm_footprints
    fp = osm_footprints.guard(plan.cells)
    if fp['outside'] or fp['blind']:
        (OUT / f'{plan.name}-footprint-hits.json').write_text(json.dumps(
            {'outside': {str(q): v for q, v in fp['outside'].items()},
             'blind': [list(q) for q in fp['blind']]}, indent=1))
        why = getattr(plan, 'outside_footprint_ok', None)
        if not why:
            raise SystemExit(
                f'{len(fp["outside"])} building-like cells stand outside vector-map footprints and '
                f'{len(fp["blind"])} have no map clip; see {plan.name}-footprint-hits.json. House fronts '
                'go on the vector-map footprint line (scripts/osm_footprints.py frontage), not at the kerb. '
                'Missing clip: shims/osm_extract.py --at x,z --cell (full CELL_EXTRACT_LAYERS). '
                'Genuinely unmapped structure '
                '(evidence in hand): set plan.outside_footprint_ok = "<reason + evidence id>".')
        print(f'footprint guard overridden ({len(fp["outside"]) + len(fp["blind"])} cells): {why}')
    guard_door_frames(plan, w.block)
    guard_roof_jogs(plan)
    # in-flight (begun) first: point at site_recover, not the ownership Clash wording (#54)
    import cellmath
    view = cell_registry.view_registry()
    touched = {cellmath.cell_of(x, z) for x, _y, z in plan.cells}
    if cell is not None:
        touched.add(tuple(cell))
    flight = [f for c in touched for f in view.cell(c).in_flight]
    if flight:
        plans = ', '.join(sorted({f.plan for f in flight}))
        raise SystemExit(f'{plan.name}: cells are in an aborted apply ({plans}); '
                         'python scripts/site_recover.py status')
    # clash report from the journal's ownership: ghost owners yield (never clash); begun excluded.
    # ``plan.keep`` (door openings / keep_edits stoops) may overwrite earlier builds (#143).
    keep = set(getattr(plan, 'keep', ()) or ())
    owned = view.owned(plan.cells, exclude=set(modifies) | {plan.name}, real_only=True)
    clash = {str(q): [owned[q][0], owned[q][1], st] for q, st in plan.cells.items()
             if q in owned and owned[q][1] != st and q not in keep}
    if clash:
        (OUT / f'{plan.name}-clashes.json').write_text(json.dumps(clash, indent=1))
        raise SystemExit(f'{len(clash)} cells belong to earlier builds; see {plan.name}-clashes.json')
    if protect:
        hits = {str(p): w.block(*p) for p in plan.cells if protect(p, w.block(*p))}
        if hits:
            (OUT / f'{plan.name}-protect-hits.json').write_text(json.dumps(hits, indent=1))
            raise SystemExit(f'{len(hits)} protected cells would change; see {plan.name}-protect-hits.json')
    # block_fill often cannot place bottom-half slabs (cell stays air → mass readback mismatch).
    bottom_n = sum(1 for p, st in plan.cells.items()
                   if is_bottom_half_slab(st) and p not in plan.keep_slab_cells)
    if bottom_n:
        if getattr(plan, 'keep_bottom_half_slabs', False):
            print(f'{plan.name}: {bottom_n} bottom-half slabs kept (keep_bottom_half_slabs); '
                  'placed one by one over MCP (about 1.1 s each)')
        else:
            rewritten = rewrite_bottom_half_slabs(plan)
            print(
                f'{plan.name}: rewrote {rewritten} bottom-half slabs → full blocks '
                '(block_fill cannot place them; set plan.keep_bottom_half_slabs to place them one by one)'
            )
    boxes = plan.boxes()
    if empty:
        print(f'{plan.name}: 0 cells (empty {pass_} pass); no clashes')
    else:
        print(f'{plan.name}: {len(plan.cells)} cells in {len(boxes)} boxes; bounds {lo}..{hi}; no clashes')
    if dry:
        return
    import site_lock
    site_lock.check()                       # single-builder rule: refuse without the world lock
    m = Paced()
    blocks = live_blocks()
    registry = cell_registry.open_registry(blocks=blocks)
    if empty:
        structure = None
    elif not registry.entries(plan=plan.name):
        structure, res = _structure_backup(m, f'{STRUCTURE_NS}:before_{plan.name}', lo, hi)
        print('backup:', res)
    else:
        # the first-time backup is the original state; recovery needs the state before THIS apply
        structure, _res = _structure_backup(
            m, f'{STRUCTURE_NS}:before_{plan.name}_{time.strftime("%Y%m%d%H%M%S")}', lo, hi)
    cell = tuple(cell) if cell else primary_cell(plan.cells)
    record = {**(meta or {}), 'structure': structure}

    mask_ref = None
    if mask is not None:
        from pass_mask import verify_integrity, mask_placements
        if tuple(mask['cell']) != cell or mask['plan'] != plan.name or not verify_integrity(mask):
            raise ValueError('mask does not belong to this plan or failed integrity check')
        if {p: full(b) for p, b in mask_placements(mask).items()} != plan.cells:
            raise ValueError('mask placements differ from the plan')
        from atomic_json import write_json
        pending_mask = OUT / f'{plan.name}-pending-mask.json'
        write_json(pending_mask, mask)
        record['pending_mask'] = str(pending_mask)
        mask_ref = {'pass': mask['pass'], 'cell': list(mask['cell']), 'plan': mask['plan'],
                    'count': mask.get('count'), 'sha256': mask.get('sha256')}
    build = Build(plan.name, pass_, dict(plan.cells), modifies=tuple(sorted(set(modifies))), strict=bool(strict),
                  mask=mask_ref, cell=cell, payload=plan, meta=record)
    try:
        ticket = registry.begin(build)       # journal 'begun' (fsynced) before the first block write
    except (InFlight, Clash, Refused) as exc:
        raise _journal_refusal(plan.name, exc) from exc
    writer = McpWriter(m)
    writer(ticket.writes)
    mark = getattr(blocks, 'mark_written', None)
    if mark is not None:
        mark()                              # LiveBlocks: refuse reads until the flush
    blocks.flush()                          # server_save_all_worlds, settle, fresh region reads
    actual = blocks.read_many(plan.cells)
    rep = _readback_report(plan, actual)
    rep.update({'fill_results': writer.fill_results, 'entities': writer.entities, 'journal_seq': ticket.seq})
    (OUT / f'{plan.name}-result.json').write_text(json.dumps(rep, indent=1))
    print(json.dumps({k: v for k, v in rep.items() if k != 'mismatch_sample'}, indent=1)[:1500])
    if rep['mismatches'] and (strict or mask is not None):
        raise SystemExit(f'{plan.name}: readback has {rep["mismatches"]} mismatches; journal entry '
                         f"{ticket.seq} left 'begun' (python scripts/site_recover.py status)")
    if mask is not None:
        import pass_mask
        pass_mask.write_mask(mask)          # publish only after a clean readback
    receipt = ticket.finish(actual)
    if receipt.status == 'landed':
        try:
            followed = registry.ensure(writer)
        except CellJournalError as exc:
            raise SystemExit(f'{plan.name}: landed, but the journal follow-ups (frontier ghosts) '
                             f'failed: {exc} (python scripts/site_recover.py status)') from exc
        if followed:
            print('journal follow-ups:', json.dumps([[r.seq, r.status, r.planned] for r in followed])[:800])
    return rep


def diff_against_before(expected, actual):
    """Compare a footprint to an expected before_<plan> map (#123).

    Independent of the undo's own erase plan: catches restores that match the
    journalled write list yet disagree with the pre-build world snapshot.

    @param expected: ``{pos: state}`` from the before_<plan> structure (or equivalent)
    @param actual: ``{pos: state | None}`` read from the world after undo
    @returns: ``{'checked', 'mismatches', 'mismatch_sample', 'checked_at'}``
    """
    bad = {}
    for p, want in expected.items():
        got = actual.get(p)
        if got is None:
            got = 'minecraft:air'
        if not same(want, got):
            bad[str(p)] = [want, got]
    return {
        'checked': len(expected),
        'mismatches': len(bad),
        'mismatch_sample': dict(list(bad.items())[:60]),
        'checked_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }


def load_before_structure_blocks(structure_meta, m=None, save_root=None):
    """Absolute world blocks from a site_kit ``before_<plan>`` structure save.

    Tests: FakePaced keeps an in-memory absolute map under ``structures[name]``.
    Live: gzip ``.nbt`` under ``<save>/generated/<ns>/structures/<local>.nbt``.

    @param structure_meta: ``{'name', 'from', 'to'}`` from a build entry's meta
    @param m: paced MCP client (or FakePaced); used to find in-memory structures
    @param save_root: Minecraft save directory; default ``world.json`` ``save_path``
    @returns: ``{(x, y, z): canonical state}``
    @raises SystemExit: structure cannot be loaded
    """
    name = structure_meta['name']
    origin = structure_meta['from']
    structs = getattr(type(m), 'structures', None) if m is not None else None
    if isinstance(structs, dict) and name in structs:
        return dict(structs[name])
    _ensure_world_config()
    import active_world
    root = save_root or active_world.save_path(required=False)
    if not root:
        raise SystemExit(f'cannot load structure {name}: no save_path in world.json')
    from structure_nbt import absolute_blocks, read_structure_file, structure_file_path
    path = structure_file_path(root, name)
    if not path.is_file():
        raise SystemExit(f'cannot load structure {name}: missing {path}')
    _size, rel = read_structure_file(path)
    return absolute_blocks(rel, origin)


def verify_erase_matches_before(footprints, erase, m, *, save_root=None, ghost_filled=frozenset()):
    """Fail closed when the undo erase plan disagrees with before_<plan> (#123).

    Compared against the journalled erase values *before* any block write, so a
    mismatch can abandon the begun entry instead of landing a pit.

    @param footprints: ``[(plan_name, structure_meta, positions), ...]`` captured
        while the builds were still live
    @param erase: ``{pos: state}`` the undo ticket will write
    @param m: paced client (for FakePaced structure maps)
    @param save_root: optional save directory override
    @param ghost_filled: erase positions journal undo deliberately restored to ghost ground
        (``before_<plan>`` has air there); exempt from the comparison
    @raises SystemExit: any footprint position in ``erase`` differs from the structure
    """
    for plan_name, structure_meta, positions in footprints:
        if not positions:
            continue
        expected_all = load_before_structure_blocks(structure_meta, m=m, save_root=save_root)
        # Air in before_<plan> where undo wrote ghost ground is deliberate (feature built before
        # its ghost); the guard is for any other disagreement, e.g. ground the erase would lose.
        expected = {p: expected_all.get(p, 'minecraft:air') for p in positions
                    if p in erase and p not in ghost_filled}
        if not expected:
            continue
        rep = diff_against_before(expected, erase)
        if rep['mismatches']:
            sample = json.dumps(rep['mismatch_sample'], indent=1)[:800]
            raise SystemExit(
                f'undo {plan_name}: erase plan differs from {structure_meta["name"]} '
                f'({rep["mismatches"]}/{rep["checked"]} mismatches); refusing before write — '
                f'see sample:\n{sample}'
            )


def _before_footprints(registry, names):
    """Capture each live plan's structure meta and cells before an undo voids them.

    Aligns with journal undo (#138): each position is checked against the
    ``before_<plan>`` of the earliest live build that *wrote* it (not merely whose
    structure box contains it). Positions with a non-targeted owner underneath are
    skipped — journal erase restores that under-owner state, not ``before_<plan>``,
    so binding them to the earliest writer's snapshot false-refuses (shared column
    written by g1 → other plan → g2). Positions no live writer covers are skipped.

    @param registry: open CellRegistry
    @param names: plan names about to be undone
    @returns: ``[(plan, structure_meta, positions), ...]``
    """
    out = []
    for n in names:
        latest = registry.recorded(n) or {}
        live = [e for e in registry.entries(plan=n)
                if e.kind == 'build' and not e.undone and e.status != 'begun'
                and e.meta.get('structure')]
        # Same under-owner set journal undo restores; do not check those against before_<plan>.
        under = registry.owned(latest, exclude=names) if latest else {}
        claimed = set()
        for e in live:
            written = registry.cells_at(e.seq) or {}
            mine = [p for p in latest
                    if p in written and p not in claimed and p not in under]
            if mine:
                out.append((n, dict(e.meta['structure']), mine))
                claimed.update(mine)
    return out


def undo(target, *, strict=True, meta=None):
    """Live Undo through the cell journal: erase a plan (name) or a cell pass ``((tx, tz), pass)``
    back to the earlier owners (air where none), with the same gates as :func:`apply`.

    The registry computes the erase from the recorded plans; the entry is journalled ``begun``
    before any write and finished after the readback. A structure template of the erased box is
    saved first (recorded in the entry's meta). Before writes, the erase plan is compared to each
    plan's apply-time ``before_<plan>`` structure (per position: the earliest live build that
    wrote it, skipping under-owner columns that journal restores to the owner below — #138);
    a difference abandons the begun entry (#123 — journal readback alone can pass while the
    world would become a pit).

    @param target: plan name or ``(cell, pass_)``
    @param strict: a readback mismatch raises and leaves the entry 'begun'
    @param meta: extra meta journalled with the entry
    @returns: Receipt
    @raises SystemExit: refused, nothing to undo, readback mismatch, or before_<plan> drift
    """
    _ensure_world_config()
    from mc_paced import Paced
    import cell_registry
    from cell_journal import CellJournalError, InFlight, Mismatch, Refused, Undo
    ensure_region_matches_open_world()
    import site_lock
    site_lock.check()
    m = Paced()
    blocks = live_blocks()
    registry = cell_registry.open_registry(blocks=blocks)
    label = target if isinstance(target, str) else f'{target[0][0]},{target[0][1]} {target[1]}'
    if isinstance(target, str):
        names = [target]
    else:
        cell, pass_ = tuple(target[0]), target[1]
        names = sorted({e.plan for e in registry.entries(cells=[cell])
                        if e.kind == 'build' and e.pass_ == pass_ and not e.undone and e.status != 'begun'
                        and (e.cell == cell or pass_ == 'feature')})
    footprints = _before_footprints(registry, names)
    positions = [p for n in names for p in (registry.recorded(n) or {})]
    record = dict(meta or {})
    if positions:
        box_plan = Plan('undo')
        box_plan.cells = dict.fromkeys(positions, 'minecraft:air')
        lo, hi = box_plan.bounds()
        name = f'{STRUCTURE_NS}:before_undo_{"_".join(names)[:80]}_{time.strftime("%Y%m%d%H%M%S")}'
        record['structure'], _res = _structure_backup(m, name, lo, hi)
    writer = McpWriter(m)
    try:
        ticket = registry.begin(Undo(target, strict=strict, meta=record))
    except (InFlight, Refused) as exc:
        raise _journal_refusal(f'undo {label}', exc) from exc
    try:
        verify_erase_matches_before(footprints, ticket.writes.cells, m,
                                    ghost_filled=ticket.writes.ghost_filled)
    except SystemExit:
        ticket.abandon(reason='erase plan differs from before_<plan> structure (#123)')
        raise
    try:
        writer(ticket.writes)
        mark = getattr(blocks, 'mark_written', None)
        if mark is not None:
            mark()
        blocks.flush()
        actual = blocks.read_many(ticket.writes.cells) if ticket.writes.cells else {}
        receipt = ticket.finish(actual)
    except Mismatch as exc:
        raise SystemExit(f'undo {label}: readback has {exc.mismatches} mismatches; journal entry '
                         f"{exc.seq} left 'begun' (python scripts/site_recover.py status)") from exc
    try:
        registry.ensure(writer)
    except CellJournalError as exc:
        raise SystemExit(f'undo {label}: landed, but the journal follow-ups failed: {exc}') from exc
    print(f'undo {label}: entry {receipt.seq} {receipt.status}, {receipt.planned} blocks')
    return receipt

def _readback_report(plan, actual):
    """Readback report from ``{pos: state}`` read after a flush.

    @param plan: applied plan
    @param actual: block states keyed by plan position
    @returns: {'planned', 'mismatches', 'mismatch_sample', 'checked'}
    """
    bad = {}
    for p, s in plan.cells.items():
        a = actual.get(p)
        if not same(s, a):
            bad[str(p)] = [s, a]
    return {'planned': len(plan.cells), 'mismatches': len(bad),
            'mismatch_sample': dict(list(bad.items())[:60]),
            'checked': time.strftime('%Y-%m-%d %H:%M:%S')}


def _bring_player(m, lo, hi, reach=80, why=None):
    """If the player is more than `reach` blocks (XZ) from the build's centre, teleport them in
    spectator mode above it and wait for chunks; return a function that restores them.

    @param m: paced MCP client
    @param lo: plan low corner
    @param hi: plan high corner
    @param reach: max XZ distance before a hop is needed
    @param why: required explanation when hopping (plan/pass context)
    @returns: restore callable, or None when no hop
    """
    import math, re
    from mc_paced import UUID
    from mc_player import move_player, restore_player
    info = m.text('player_get_info', {'uuid': UUID})
    num = lambda k: float(re.search(rf'{k}: (-?[\d.]+)', info).group(1))
    px, py, pz = num('x'), num('y'), num('z')
    yaw, pitch = num('yaw'), num('pitch')
    mode = re.search(r'gameMode: (\w+)', info).group(1)
    cx, cz = (lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2
    if math.hypot(px - cx, pz - cz) <= reach:
        return None
    hop_why = why or (
        f'Moving you to {cx:.0f},{cz:.0f} (spectator) so the build area loads; I will put you back.'
    )
    move_player(
        why=hop_why,
        position=[cx, hi[1] + 20, cz],
        gamemode='spectator',
        m=m,
    )
    time.sleep(6)
    print(f'  (moved player to {cx:.0f},{cz:.0f} to load chunks; will put them back)')

    def restore():
        # user (16 Oct 2026, after being moved repeatedly): never leave them somewhere new. Back to the exact spot,
        # facing the way they were (yaw/pitch turned into a look-at point), and their own game mode.
        yr, pr = math.radians(yaw), math.radians(pitch)
        look = [px - math.sin(yr) * math.cos(pr) * 10, py + 1.62 - math.sin(pr) * 10, pz + math.cos(yr) * math.cos(pr) * 10]
        restore_player(position=[px, py, pz], facing=look, gamemode=mode, m=m)
    return restore




def readback(plan, world=None):
    """Read every planned cell back (default: :class:`World` over LiveBlocks / MCP).

    Prefer :func:`live_blocks` + ``read_many`` for journal finish; this helper remains for
    one-off probes that pass a duck-typed ``world.block``.

    @param plan: Plan
    @param world: object with ``block(x, y, z)`` (default: :class:`World`)
    @returns: {'planned', 'mismatches', 'mismatch_sample', 'checked'}
    """
    w = world or World()
    return _readback_report(plan, {p: w.block(*p) for p in plan.cells})


def main(argv: list | None = None) -> int:
    """Thin CLI: ``undo <plan>`` (bake-off / recovery convenience).

    @param argv: optional arg list (defaults to ``sys.argv[1:]``)
    @returns: exit code
    """
    import argparse

    ap = argparse.ArgumentParser(
        description='site_kit CLI (undo a recorded plan through the cell journal)',
    )
    sub = ap.add_subparsers(dest='cmd', required=True)
    p_undo = sub.add_parser('undo', help='erase a plan name via site_kit.undo')
    p_undo.add_argument('plan', help='plan name as recorded in the journal')
    p_undo.add_argument(
        '--round',
        default=None,
        help='optional bake-off round id stored in undo meta',
    )
    p_undo.add_argument(
        '--no-strict',
        action='store_true',
        help='pass strict=False to undo (default: strict)',
    )
    args = ap.parse_args(argv)
    if args.cmd == 'undo':
        meta = {'round': args.round} if args.round else None
        receipt = undo(args.plan, strict=not args.no_strict, meta=meta)
        print(f'undo {args.plan}: seq={receipt.seq} status={receipt.status}')
        return 0
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

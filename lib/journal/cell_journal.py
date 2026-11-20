"""Cell journal: one append-only record of what is built, and the views folded from it (ADR 004).

``CellRegistry(world_root, blocks)`` is the only writer of ``<world>/registry/journal.jsonl``:

- ``change(intent)`` is the single entry point (intents :class:`Build`, :class:`Undo`, :class:`Note`).
  A block-writing change is journalled ``begun`` (fsynced) BEFORE the caller writes any block, then
  ``landed`` or ``mismatched`` after the blocks are read back. A strict/mask readback failure raises
  :class:`Mismatch` and leaves the entry ``begun`` for :meth:`CellRegistry.reconcile`.
- ``view(cells)`` returns frozen :class:`CellView` records (ground, ghost, trees, decor, features,
  in-flight, marker, hold, notes, done, left). They are folded from the journal, so they cannot disagree.
- ``reconcile(cells, fix=)`` compares the journal with the blocks and, with ``fix``, re-commits the
  recorded plans through a caller-supplied ``apply`` callable.

Ownership (later build wins, air cancels, ghost owners yield and never clash; real ground drops ghost
owners only above its per-column top) is a private projection rebuilt from the journal and the plan
blobs in ``registry/blobs/<hash>.json[.zst|.gz]``. Build begins may store a ``prior`` blob for undo.
Nothing here restores or snapshots regions (ADR 004 decision a).

Journal lines (JSON, one per line; ``seq`` strictly increasing)::

    {"op":"begin","seq":n,"kind":"build|undo","plan":..,"pass":..,"cell":[tx,tz],"cells":[..],"blob":h,
     "prior":h?,...}   # prior = optional world snapshot at planned positions (build only)
    {"op":"finish","seq":m,"ref":n,"status":"landed|mismatched|abandoned","mismatches":k}
    {"op":"note","seq":n,"cell":[tx,tz],"kind":..,"ref":..,"body":..}
    {"op":"repair","seq":n,...}     # informational: reconcile re-wrote drifted blocks
"""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import os
import sys
import time
import zlib
from collections import Counter, OrderedDict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

try:  # Python 3.14+: about 6x smaller than gzip on plan blobs, same load speed
    from compression import zstd as _zstd
except ImportError:
    _zstd = None

# Corrupt or truncated compressed data surfaces as these; re-typed to OSError so callers see JournalError.
_DECOMPRESS_ERRORS: tuple[type[Exception], ...] = (EOFError, zlib.error) + (
    (_zstd.ZstdError,) if _zstd is not None else ())

import cellmath
from block_store import BlockStore, Pos, block_id, same_state

Cell = tuple[int, int]

# Elements: lasting place-content kinds (GLOSSARY Element). Wire strings stay stable.
ELEMENTS = ('feature', 'ground', 'trees', 'decor')
REAL_PASSES = ELEMENTS  # alias: journal/API still say "real passes" vs ghost
# ghost-features remains readable for old journal lines (ownership still yields) but new
# ghost-features Builds are refused — volumetric feature ghosts are withdrawn (task #68).
GHOST_PASSES = ('ghost-ground', 'ghost-features')
NEUTRAL_PASSES = ('other',)        # owns blocks, no progress meaning (cell markers, clean-ups)
PASSES = ELEMENTS + GHOST_PASSES + NEUTRAL_PASSES
# plan_feature / plan_feature-dismiss: cell plan (#128). Undo clears the build only;
# dismiss is a separate deliberate note with a reason. readiness_evidence: a cross-cell
# neighbour's evidence check, which validates no plans (task 292).
NOTE_KINDS = ('marker', 'hold', 'coverage', 'coverage-dismiss', 'review', 'issue', 'close',
              'plan_feature', 'plan_feature-dismiss', 'clip_coverage-dismiss', 'readiness',
              'readiness_evidence', 'builder_registration', 'approval')
# Layers a cell approval can cover (#327). An approval says which of these the owner signed off;
# it never stands in for the pass itself (ground-layer work needs a landed ground pass).
APPROVAL_LAYERS = ('ghost', 'buildings', 'ground', 'trees', 'decor', 'dressing')
DRIFT_KINDS = ('in-flight', 'missing', 'foreign', 'mismatched', 'ghost-missing')
AIR = 'minecraft:air'
CANCEL = frozenset({'minecraft:air', 'minecraft:light'})   # same rule as the old ownership merge
AIR_IDS = frozenset({'minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'})
DEFAULT_RING = 2
# Begin-line schema: 1 = legacy (no ``schema`` key), 2 = adds ``schema`` and, on feature builds,
# optional provenance in meta (builder_name, builder_sha, spec_hash). Older lines load unchanged.
JOURNAL_SCHEMA = 2
SAMPLE = 10
# Large ghost plans must not multiply retained memory across cells.
MAX_CACHED_BLOB_BLOCKS = 262144


# --- errors (all loud; nothing is swallowed) -----------------------------------------------------
class CellJournalError(Exception):
    """Base class for every journal refusal or failure."""


class Refused(CellJournalError):
    """The change breaks a rule; nothing was journalled and no block was touched."""

    def __init__(self, reason: str, detail: Any = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class InFlight(CellJournalError):
    """A ``begun`` entry covers one of the cells; resolve it with ``reconcile(fix=True)`` first."""

    def __init__(self, cells: Iterable[Cell], seqs: Iterable[int]) -> None:
        self.cells = tuple(sorted(cells))
        self.seqs = tuple(sorted(seqs))
        super().__init__(f'cells {list(self.cells)} have unfinished entries {list(self.seqs)}')


class Clash(CellJournalError):
    """Planned blocks collide with a real earlier owner not listed in ``modifies``."""

    def __init__(self, clashes: Mapping[Pos, tuple[str, str, str]]) -> None:
        self.clashes = dict(clashes)
        sample = list(self.clashes.items())[:3]
        super().__init__(f'{len(self.clashes)} cells belong to earlier builds, e.g. {sample}')


class Mismatch(CellJournalError):
    """Strict/mask readback failed; the entry stays ``begun`` for reconcile."""

    def __init__(self, seq: int, mismatches: int, sample: Mapping[str, list]) -> None:
        self.seq = seq
        self.mismatches = mismatches
        self.sample = dict(sample)
        super().__init__(f'entry {seq}: readback has {mismatches} mismatches; left begun')


class JournalError(CellJournalError):
    """The journal or a plan blob cannot be read or written."""


# --- intents and results -------------------------------------------------------------------------
@dataclass(frozen=True, eq=False)
class Build:
    """Journal intent for one block-writing plan.

    @param plan: plan name (feature id, ``ground_48_m44`` ...); same name = same owner
    @param pass_: one of :data:`PASSES`
    @param cells: planned ``{(x, y, z): state}``
    @param modifies: earlier owners this plan may overwrite without a clash
    @param protect: ``callable(pos, existing_state) -> bool``; True refuses (never journalled)
    @param strict: a readback mismatch raises :class:`Mismatch` and leaves the entry ``begun``
    @param mask: opaque pass mask (JSON-able); a mask also makes mismatches strict
    @param cell: primary cell (default: the cell holding most planned columns)
    @param payload: opaque object for the caller's apply (e.g. the ``site_kit.Plan``); never journalled
    @param meta: small JSON-able dict journalled on the ``begin`` line (structure backup, notes ...)
    @param signs: ``((pos, lines, colour, glow), ...)`` journalled and replayed on reconcile/recover
    @param entities: ``((entity_type, pos, snbt|None), ...)`` journalled and replayed on reconcile/recover
    @param ground_layer: the plan writes ground-layer things (markings, surfaces); refused unless every
        cell it writes into has a landed ground pass (#327). Moot for the ground pass itself.
    """

    plan: str
    pass_: str
    cells: Mapping[Pos, str]
    modifies: tuple[str, ...] = ()
    protect: Callable[[Pos, str | None], bool] | None = None
    strict: bool = False
    mask: Any = None
    cell: Cell | None = None
    payload: Any = None
    meta: Mapping[str, Any] | None = None
    signs: tuple = ()
    entities: tuple = ()
    ground_layer: bool = False


@dataclass(frozen=True, eq=False)
class Undo:
    """Void earlier Build entries; the registry computes the blocks that put the world back.

    @param target: plan name, or ``(cell, pass_)``
    @param strict: leave the entry ``begun`` when the erase readback fails (default True)
    @param meta: small JSON-able dict journalled on the ``begin`` line
    """

    target: str | tuple[Cell, str]
    strict: bool = True
    meta: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class Note:
    """Journal-only fact: marker, hold, coverage warning, review, issue or cell-plan note.

    @param cell: (tx, tz)
    @param kind: one of :data:`NOTE_KINDS`
    @param body: ``up|none`` (marker), reason (hold, coverage-dismiss, plan_feature-dismiss),
        label (plan_feature), comma-separated :data:`APPROVAL_LAYERS` (approval), text otherwise
    @param ref: warning / feature / note id (coverage, coverage-dismiss, plan_feature,
        plan_feature-dismiss, review, issue, close)
    """

    cell: Cell
    kind: str
    body: str = ''
    ref: str = ''


@dataclass(frozen=True, eq=False)
class Writes:
    """What the caller's ``apply`` must write: the recorded plan of one entry.

    @param plan: owner name
    @param pass_: pass of the entry (``undo`` entries use the target's pass)
    @param cells: ``{pos: state}`` to write
    @param kind: ``build``, ``undo`` or ``repair``
    @param intent: the originating intent (``Build.payload`` rides on it), None for recovery
    @param seq: journal seq of the ``begun`` entry (None for repair)
    @param signs: recorded sign text to rewrite after the blocks (empty for undo/repair of cells only)
    @param entities: recorded summons to rewrite after the blocks
    @param ghost_filled: undo positions restored to ghost ground instead of the (air) prior
    """

    plan: str
    pass_: str
    cells: Mapping[Pos, str]
    kind: str = 'build'
    intent: Any = None
    seq: int | None = None
    signs: tuple = ()
    entities: tuple = ()
    ghost_filled: frozenset = frozenset()


@dataclass(frozen=True, eq=False)
class Receipt:
    """Result of a change.

    @param seq: journal seq of the entry
    @param status: ``landed`` or ``mismatched`` (notes: ``landed``)
    @param planned: number of planned positions
    @param mismatches: readback mismatches
    @param sample: up to 10 ``{"x,y,z": [planned, actual]}``
    @param follow_ups: Build intents still needed (strips, ghost ensure), for the caller to apply
    @param followed: receipts of follow-ups applied by ``change(..., cascade=True)``
    """

    seq: int
    status: str
    planned: int = 0
    mismatches: int = 0
    sample: Mapping[str, list] = field(default_factory=dict)
    follow_ups: tuple[Build, ...] = ()
    followed: tuple['Receipt', ...] = ()


@dataclass(frozen=True)
class GhostView:
    """Whether ghost ground is expected in the world now (DTM placeholder with coloured tops)."""

    ground: bool = False


@dataclass(frozen=True)
class FeatureState:
    """A feature on the cell plan: ``planned``, ``in-progress`` or ``built``.

    ``planned`` means the cell must have it (a ``plan_feature`` note, or a prior
    feature Build including an undone one) but no live Build owns it now.
    """

    id: str
    status: str


@dataclass(frozen=True)
class InFlightView:
    """An unfinished (``begun``) entry covering a cell."""

    seq: int
    plan: str
    pass_: str


@dataclass(frozen=True)
class EntryView:
    """One journalled build or undo entry (read-only; for recovery tools and plan listings).

    @param status: ``begun``, ``landed``, ``mismatched`` or ``abandoned`` (rolled back by a recovery tool)
    @param undone: voided by a landed Undo, or abandoned
    @param ts: UTC time of the ``begin`` line
    @param meta: the intent's ``meta`` dict
    """

    seq: int
    kind: str
    plan: str
    pass_: str
    cell: Cell
    cells: tuple[Cell, ...]
    status: str
    undone: bool
    n: int
    owner: str | None
    ts: str
    meta: Mapping[str, Any]
    targets: tuple[int, ...] = ()
    mismatches: int = 0
    finish_seq: int = 0
    schema: int = 1


@dataclass(frozen=True)
class NoteView:
    """An open note: coverage warning, review or issue."""

    kind: str
    ref: str
    body: str


@dataclass(frozen=True)
class CellView:
    """Frozen per-cell state folded from the journal (mirrors the old RegistryStore record).

    @param ground: ``none|ghost|generated``
    @param ghost: whether ghost ground is expected in the world now
    @param features: cell-plan features (``planned`` / ``in-progress`` / ``built``); undo leaves
        ``planned``, dismiss removes
    @param done: ground generated, trees, decor, every planned feature built, no marker, no warnings,
        nothing in flight
    @param left: short human reasons work remains (same wording as ``what_is_left``)
    """

    cell: Cell
    ground: str = 'none'
    ghost: GhostView = GhostView()
    trees: bool = False
    decor: bool = False
    features: tuple[FeatureState, ...] = ()
    in_flight: tuple[InFlightView, ...] = ()
    marker: bool = False
    hold: str | None = None
    notes: tuple[NoteView, ...] = ()
    done: bool = False
    left: tuple[str, ...] = ()


@dataclass(frozen=True)
class DriftItem:
    """One finding of :meth:`CellRegistry.reconcile`.

    @param cell: cell the finding belongs to
    @param kind: one of :data:`DRIFT_KINDS`
    @param seq: highest journal seq involved (0 when none)
    @param count: positions (or cells for ghost kinds) affected
    @param sample: up to 5 ``{"x,y,z": [expected, actual]}``
    @param plans: owner names involved
    @param fixed: re-committed or resolved by ``fix=True``
    @param cells: every cell of the entry (``mismatched`` only: repair needs all of them in scope)
    @param layer: journal pass of the entry (``mismatched`` only: names the re-apply command)
    @param owner: the entry's own cell (``mismatched`` only: a redo runs there)
    """

    cell: Cell
    kind: str
    seq: int = 0
    count: int = 0
    sample: Mapping[str, list] = field(default_factory=dict)
    plans: tuple[str, ...] = ()
    fixed: bool = False
    cells: tuple[Cell, ...] = ()
    layer: str = ''
    owner: Cell | None = None


@dataclass(frozen=True)
class Drift:
    """Result of ``reconcile``: findings plus the mismatched entries upgraded to landed.

    @param upgraded: how many mismatched entries were upgraded
    @param cleared: ``(plan, seq)`` of each upgraded entry (they no longer block preflight)
    """

    items: tuple[DriftItem, ...] = ()
    upgraded: int = 0
    cleared: tuple[tuple[str, int], ...] = ()

    def __iter__(self) -> Iterator[DriftItem]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    @property
    def clean(self) -> bool:
        """True when nothing is left unfixed."""
        return all(i.fixed for i in self.items)

    def by_kind(self, kind: str) -> tuple[DriftItem, ...]:
        """Findings of one kind."""
        return tuple(i for i in self.items if i.kind == kind)


GhostPlanner = Callable[[Cell, str], 'Mapping[Pos, str] | None']
"""``planner(cell, layer)`` for layer ``ground`` or ``features``: ``{pos: state}`` (empty = nothing to
place), or None when the cell cannot be planned (no data)."""

Apply = Callable[[Writes], None]
"""Caller-supplied writer: puts ``Writes.cells`` into the world (MCP), nothing else."""


# --- internals -----------------------------------------------------------------------------------
@dataclass
class _Entry:
    seq: int
    kind: str                   # build | undo
    plan: str
    pass_: str
    cell: Cell
    cells: tuple[Cell, ...]
    blob: str
    n: int
    modifies: tuple[str, ...] = ()
    strict: bool = False
    masked: bool = False
    strip: bool = False
    targets: tuple[int, ...] = ()
    status: str = 'begun'       # begun | landed | mismatched | abandoned
    finish_seq: int = 0
    mismatches: int = 0
    undone: bool = False
    reopened: tuple[Cell, ...] = ()
    owner: str | None = None
    ts: str = ''
    meta: dict = field(default_factory=dict)
    signs: tuple = ()
    entities: tuple = ()
    prior: str | None = None    # optional blob of world blocks at begin (undo fallback)
    schema: int = 1


@dataclass(frozen=True)
class _Owner:
    seq: int
    plan: str
    state: str
    ghost: bool
    pass_: str


@dataclass(frozen=True)
class _Prepared:
    record: dict
    writes: Writes
    strict: bool
    prior: Mapping[Pos, str] | None = None
    observed: Mapping[Pos, str | None] | None = None


def _token(tz: int) -> str:
    return f"{'m' if tz < 0 else ''}{abs(tz)}"


def ghost_name(cell: Cell, layer: str) -> str:
    """Owner name of a cell's ghost plan, e.g. ``ghost_48_m44_ground``.

    @param cell: (tx, tz)
    @param layer: ``ground`` (or legacy ``features`` when reading old journal lines)
    @returns: plan name
    """
    return f'ghost_{cell[0]}_{_token(cell[1])}_{layer}'


def announce_ghost_generation(n: int) -> None:
    """One non-interactive stderr notice before ghost ground is generated (never prompts).

    Silent when ``n`` is 0. Timing is the ground-pass figure in ``rules/slow-commands.md``.

    @param n: ghost cells about to be applied
    """
    if n <= 0:
        return
    noun = 'cell' if n == 1 else 'cells'
    print(f'ghosts: generating {n} ghost {noun} (placeholder ground); this will take a while '
          f'(ground passes alone run 25-55 s per cell).', file=sys.stderr, flush=True)


def _encode_runs(cells: Mapping[Pos, str]) -> list[list]:
    """Column run-length form of a plan: sorted ``[x, z, y0, y1, state]`` runs of identical vertical blocks."""
    runs: list[list] = []
    for x, z, y, state in sorted((p[0], p[2], p[1], st) for p, st in cells.items()):
        last = runs[-1] if runs else None
        if last and last[0] == x and last[1] == z and last[3] == y - 1 and last[4] == state:
            last[3] = y
        else:
            runs.append([x, z, y, y, state])
    return runs


def _decode_runs(runs: Iterable[list]) -> dict[Pos, str]:
    out: dict[Pos, str] = {}
    for x, z, y0, y1, state in runs:
        for y in range(y0, y1 + 1):
            out[(x, y, z)] = state
    return out


def _blob_key(cells: Mapping[Pos, str]) -> tuple[str, bytes]:
    body = json.dumps({'rle': _encode_runs(cells)}, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(body).hexdigest()[:32], body


def _norm_sign(item) -> tuple:
    """Canonical sign attachment: ``(pos, lines, colour, glow)``."""
    pos, lines, colour, glow = item
    return (tuple(pos), list(lines), colour, bool(glow))


def _norm_entity(item) -> tuple:
    """Canonical entity attachment: ``(kind, pos, snbt)``."""
    kind, pos, snbt = item
    return (kind, tuple(pos), snbt)


def _attachments(b: Build) -> tuple[tuple, tuple]:
    """Signs/entities from ``Build`` fields, or from a Plan-like ``payload`` when those are empty.

    @param b: build intent
    @returns: ``(signs, entities)`` as tuples of the shapes :class:`Writes` stores
    """
    signs = tuple(_norm_sign(s) for s in (b.signs or ()))
    entities = tuple(_norm_entity(e) for e in (b.entities or ()))
    payload = b.payload
    if payload is not None and not signs and not entities:
        signs = tuple(_norm_sign(s) for s in (getattr(payload, 'signs', ()) or ()))
        entities = tuple(_norm_entity(e) for e in (getattr(payload, 'entities', ()) or ()))
    return signs, entities


def _signs_json(signs: tuple) -> list:
    """JSON form of sign attachments for a begin line."""
    return [[list(pos), list(lines), colour, bool(glow)] for pos, lines, colour, glow in signs]


def _entities_json(entities: tuple) -> list:
    """JSON form of entity attachments for a begin line."""
    return [[kind, list(pos), snbt] for kind, pos, snbt in entities]


def _signs_from_json(raw: list | None) -> tuple:
    """Parse journalled signs back into Writes shape."""
    if not raw:
        return ()
    return tuple(_norm_sign(item) for item in raw)


def _entities_from_json(raw: list | None) -> tuple:
    """Parse journalled entities back into Writes shape."""
    if not raw:
        return ()
    return tuple(_norm_entity(item) for item in raw)


def _writes_for(e: _Entry, cells: Mapping[Pos, str], *, kind: str | None = None,
                intent: Any = None, seq: int | None = None, full_plan: bool = True) -> Writes:
    """Build a :class:`Writes` for an entry, including journalled attachments.

    Entities are only attached when ``full_plan`` is true so a partial cell repair does not
    re-summon duplicates; signs are always attached (``block_entity_set_nbt`` is idempotent).
    """
    return Writes(
        e.plan, e.pass_, cells, kind or e.kind, intent,
        e.seq if seq is None and e.status == 'begun' else seq,
        e.signs, e.entities if full_plan else ())


class Ticket:
    """A begun entry. Write the blocks, flush, read back, then ``finish(readback)`` exactly once.

    @param registry: owning registry
    @param seq: journal seq of the ``begun`` line
    @param writes: what the caller must write
    """

    def __init__(self, registry: 'CellRegistry', seq: int, writes: Writes, strict: bool,
                 before: Mapping[Pos, str | None] | None = None) -> None:
        self.registry = registry
        self.seq = seq
        self.writes = writes
        self.strict = strict
        self._before = before

    def prewrite(self) -> dict[Pos, str | None]:
        """Return the original begin's observed states without another block read.

        @returns: an independent position/state map, preserving unknown (None); empty for empty builds
        @raises Refused: the ticket has no original observations (undo or resumed entry)
        """
        if self._before is None:
            raise Refused('this ticket has no pre-write snapshot')
        return dict(self._before)

    def finish(self, readback: Mapping[Pos, str | None]) -> Receipt:
        """Compare ``readback`` with the plan and journal ``landed`` / ``mismatched``.

        @param readback: ``{pos: state}`` read from the block store after a flush
        @returns: Receipt
        @raises Mismatch: strict/mask entry with mismatches (entry stays ``begun``)
        @raises Refused: the entry was already finished
        @raises JournalError: the finish line could not be written (entry stays ``begun``)
        """
        return self.registry._finish(self, readback)

    def abandon(self, reason: str = '') -> None:
        """Void a ``begun`` entry whose blocks a recovery tool put back (it owns nothing afterwards).

        @param reason: short text journalled on the finish line
        @raises Refused: the entry was already finished
        @raises JournalError: the finish line could not be written
        """
        self.registry._abandon(self, reason)


# --- the registry --------------------------------------------------------------------------------
class CellRegistry:
    """Journal-backed cell registry.

    @param world_root: world checkout; the journal lives in ``<world_root>/registry/``
    @param blocks: :class:`block_store.BlockStore` used for readback and reconcile
    @param ghosts: optional :data:`GhostPlanner`; without it no ghost Builds are produced
    @param cell_size: cell edge in blocks (default: ``cellmath.cell_size()``)
    @param ring: Chebyshev depth of the ghost frontier (default 2)
    """

    def __init__(self, world_root: Path | str, blocks: BlockStore, ghosts: GhostPlanner | None = None,
                 *, cell_size: int | None = None, ring: int = DEFAULT_RING, build_policy=None) -> None:
        self.root = Path(world_root)
        self.dir = self.root / 'registry'
        self.journal_path = self.dir / 'journal.jsonl'
        self.blob_dir = self.dir / 'blobs'
        self.blocks = blocks
        self.ghosts = ghosts
        self.cell_size = cell_size
        self.ring = ring
        self.build_policy = build_policy
        self._blob_cache: OrderedDict[str, dict[Pos, str]] = OrderedDict()
        self._reset_state()

    # ---- folding ---------------------------------------------------------------------------
    def _reset_state(self) -> None:
        self._offset = 0
        self._torn = 0
        self._seq = 0
        self._entries: dict[int, _Entry] = {}
        self._by_cell: dict[Cell, list[int]] = {}
        self._plans: dict[str, list[int]] = {}
        self._begun: set[int] = set()
        self._notes: list[dict] = []
        self._touch()

    def _touch(self) -> None:
        """Drop derived caches after the folded state changed."""
        self._derived: dict[Cell, CellView] = {}
        self._owner_cache: dict[Cell, dict[Pos, _Owner]] = {}
        self._note_cache: dict | None = None
        self._planning_revision: str | None = None

    def _cell_of(self, pos: Pos) -> Cell:
        return cellmath.cell_of(pos[0], pos[2], self.cell_size)

    def _cells_of(self, positions: Iterable[Pos]) -> set[Cell]:
        size = self.cell_size
        return {cellmath.cell_of(x, z, size) for x, _y, z in positions}

    def _fold(self, d: dict) -> None:
        """Apply one parsed journal line to the folded state (deterministic, no I/O)."""
        try:
            op, seq = d['op'], d['seq']
        except (KeyError, TypeError) as exc:
            raise JournalError(f'journal line without op/seq: {d!r}') from exc
        if seq != self._seq + 1:
            raise JournalError(f'journal seq gap: expected {self._seq + 1}, found {seq}')
        self._seq = seq
        if op == 'begin':
            self._fold_begin(d)
        elif op == 'finish':
            self._fold_finish(d)
        elif op == 'note':
            self._notes.append(d)
            if d['kind'] == 'builder_registration':
                try:
                    entry = self._entries[int(d['ref'])]
                    registration = dict(json.loads(d['body']), seq=seq, ts=d.get('ts', ''))
                except (KeyError, TypeError, ValueError) as exc:
                    raise JournalError(f'invalid builder registration at seq {seq}') from exc
                entry.meta['builder_registration'] = registration
        elif op == 'revive':
            self._fold_revive(d)
        elif op != 'repair':
            raise JournalError(f'unknown journal op {op!r} at seq {seq}')
        self._touch()

    def _fold_begin(self, d: dict) -> None:
        cells = tuple(tuple(c) for c in d['cells'])
        e = _Entry(
            seq=d['seq'], kind=d['kind'], plan=d['plan'], pass_=d['pass'], cell=tuple(d['cell']),
            cells=cells, blob=d['blob'], n=d['n'], modifies=tuple(d.get('modifies', ())),
            strict=bool(d.get('strict')), masked=bool(d.get('mask')), strip=bool(d.get('strip')),
            targets=tuple(d.get('targets', ())), owner=d.get('owner'), ts=d.get('ts', ''),
            meta=dict(d.get('meta') or {}),
            signs=_signs_from_json(d.get('signs')), entities=_entities_from_json(d.get('entities')),
            prior=d.get('prior'), schema=int(d.get('schema', 1)))
        if e.schema > JOURNAL_SCHEMA:
            raise JournalError(f'journal entry {e.seq} has schema {e.schema}; this tool reads up to {JOURNAL_SCHEMA}')
        if e.kind == 'build' and e.pass_ == 'feature':
            earlier = self._latest(e.plan)
            if earlier is not None and earlier.status == 'landed':
                e.reopened = tuple(sorted(set(earlier.cells) | set(cells)))
        self._entries[e.seq] = e
        self._begun.add(e.seq)
        for c in cells:
            self._by_cell.setdefault(c, []).append(e.seq)
        self._plans.setdefault(e.plan, []).append(e.seq)

    def _fold_finish(self, d: dict) -> None:
        e = self._entries.get(d['ref'])
        if e is None:
            raise JournalError(f'finish for unknown entry {d["ref"]}')
        status = d['status']
        if status not in ('landed', 'mismatched', 'abandoned') or e.status in ('landed', 'abandoned') or (
                e.status == 'mismatched' and status != 'landed'):
            raise JournalError(f'bad finish {e.status} -> {status} for entry {e.seq}')
        e.status, e.finish_seq, e.mismatches = status, d['seq'], int(d.get('mismatches', 0))
        self._begun.discard(e.seq)
        if status == 'abandoned':
            e.undone = True                             # rolled back: owns nothing, counts for nothing
            return
        if e.kind == 'undo' and status == 'landed':
            for t in e.targets:
                if t in self._entries:
                    self._entries[t].undone = True

    def _fold_revive(self, d: dict) -> None:
        """Clear ``undone`` on targets (and cancel the undo) after a recover that restored pre-undo blocks."""
        for t in d.get('targets', ()):
            if t in self._entries:
                self._entries[t].undone = False
        u = d.get('undo')
        if u is not None and u in self._entries:
            self._entries[u].undone = True

    def _latest(self, plan: str) -> _Entry | None:
        for seq in reversed(self._plans.get(plan, ())):
            e = self._entries[seq]
            if not e.undone and e.kind == 'build':
                return e
        return None

    def _sync(self) -> None:
        """Fold any journal lines written since the last sync (incremental; torn tail ignored)."""
        try:
            size = self.journal_path.stat().st_size
        except FileNotFoundError:
            if self._offset or self._seq:
                self._reset_state()
            return
        if size < self._offset:
            self._reset_state()
        if size == self._offset:
            return
        try:
            with self.journal_path.open('rb') as fh:
                fh.seek(self._offset)
                data = fh.read()
        except OSError as exc:
            raise JournalError(f'cannot read {self.journal_path}: {exc}') from exc
        end = data.rfind(b'\n') + 1
        for raw in data[:end].split(b'\n'):
            if not raw.strip():
                continue
            try:
                d = json.loads(raw)
            except ValueError as exc:
                raise JournalError(f'corrupt journal line after seq {self._seq}: {raw[:80]!r}') from exc
            self._fold(d)
        self._offset += end
        self._torn = len(data) - end

    def _append(self, record: dict) -> int:
        """Append one line durably (flock, drop a torn tail, write, fsync) and fold it.

        @param record: line without ``seq``/``ts``
        @returns: its seq
        @raises JournalError: on any I/O failure (nothing was folded)
        """
        try:
            fresh = not self.journal_path.exists()
            self.dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.journal_path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as exc:
            raise JournalError(f'cannot open {self.journal_path}: {exc}') from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._sync()
            if self._torn:
                os.ftruncate(fd, self._offset)         # an unacknowledged partial line; never durable
                self._torn = 0
            line = dict(record, seq=self._seq + 1, ts=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
            payload = (json.dumps(line, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
            os.lseek(fd, 0, os.SEEK_END)
            written = os.write(fd, payload)
            if written != len(payload):
                raise OSError(f'short write {written}/{len(payload)}')
            os.fsync(fd)
            if fresh:
                self._fsync_dir()
            self._offset += len(payload)
            self._fold(json.loads(payload))
            return line['seq']
        except OSError as exc:
            note = ''
            try:
                os.ftruncate(fd, self._offset)         # undo a line that may be half-durable
            except OSError as rollback:
                note = f'; rollback failed: {rollback}'
            raise JournalError(f'journal write failed: {exc}{note}') from exc
        finally:
            os.close(fd)

    def _fsync_dir(self) -> None:
        fd = os.open(self.dir, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    # ---- plan blobs ------------------------------------------------------------------------
    def _blob_paths(self, key: str) -> list[Path]:
        """Every on-disk spelling of a blob, preferred first (``.json`` is the legacy uncompressed form)."""
        return [self.blob_dir / f'{key}.json{ext}' for ext in ('.zst', '.gz', '')]

    def _store_blob(self, cells: Mapping[Pos, str]) -> str:
        key, body = _blob_key(cells)
        try:
            if any(p.exists() for p in self._blob_paths(key)):
                return key
            self.blob_dir.mkdir(parents=True, exist_ok=True)
            if _zstd is not None:
                path, data = self.blob_dir / f'{key}.json.zst', _zstd.compress(body, level=3)
            else:
                path, data = self.blob_dir / f'{key}.json.gz', gzip.compress(body, 6, mtime=0)
            tmp = path.with_name(f'.{key}.{os.getpid()}.tmp')
            with open(tmp, 'wb') as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(path)
        except OSError as exc:
            raise JournalError(f'cannot store plan blob {key}: {exc}') from exc
        return key

    def _read_blob_bytes(self, key: str) -> bytes:
        """Decompressed bytes of a blob. Raises ``OSError`` when missing, corrupt, or ``.zst`` on Python < 3.14.

        Portability limit: ``.zst`` blobs (written on 3.14+) are unreadable on older Pythons; the gzip
        fallback is only what older interpreters *write*. Open a registry with the interpreter that wrote it.
        """
        for path in self._blob_paths(key):
            try:
                data = path.read_bytes()
            except FileNotFoundError:
                continue
            try:
                if path.suffix == '.zst':
                    if _zstd is None:
                        raise OSError(f'{path.name} needs Python 3.14+ (compression.zstd)')
                    return _zstd.decompress(data)
                return gzip.decompress(data) if path.suffix == '.gz' else data
            except _DECOMPRESS_ERRORS as exc:
                raise OSError(f'{path.name} is corrupt: {exc}') from exc
        raise FileNotFoundError(f'no blob file for {key}')

    def _blob(self, key: str) -> dict[Pos, str]:
        got = self._blob_cache.get(key)
        if got is not None:
            self._blob_cache.move_to_end(key)
            return got
        try:
            raw = json.loads(self._read_blob_bytes(key))
            if 'rle' in raw and len(raw) == 1:
                got = _decode_runs(raw['rle'])
            else:  # legacy keys are "x,y,z" strings, so they can never be exactly {'rle'}
                got = {tuple(int(v) for v in k.split(',')): s for k, s in raw.items()}
                if any(len(pos) != 3 for pos in got):
                    raise ValueError('legacy blob key is not "x,y,z"')
        except (OSError, ValueError, TypeError, AttributeError, LookupError) as exc:
            raise JournalError(f'plan blob {key} unreadable: {exc}') from exc
        self._blob_cache[key] = got
        while (len(self._blob_cache) > 16
               or sum(map(len, self._blob_cache.values())) > MAX_CACHED_BLOB_BLOCKS):
            self._blob_cache.popitem(last=False)
        return got

    # ---- ownership projection (private, rebuilt from the journal) --------------------------
    def _cell_owners(self, cell: Cell, *, exclude: frozenset[str] = frozenset(),
                     skip: frozenset[int] = frozenset()) -> dict[Pos, _Owner]:
        cacheable = not exclude and not skip
        if cacheable and cell in self._owner_cache:
            return self._owner_cache[cell]
        owners: dict[Pos, _Owner] = {}
        for seq in self._by_cell.get(cell, ()):
            e = self._entries[seq]
            if e.kind != 'build' or e.undone or e.status == 'begun' or seq in skip or e.plan in exclude:
                continue
            ghost = e.pass_ in GHOST_PASSES
            tops: dict[tuple[int, int], int] = {}       # (x, z) -> max Y written by this ground plan
            for pos, state in self._blob(e.blob).items():
                if self._cell_of(pos) != cell:
                    continue
                if block_id(state) in CANCEL:
                    owners.pop(pos, None)
                else:
                    owners[pos] = _Owner(seq, e.plan, state, ghost, e.pass_)
                    if e.pass_ == 'ground':
                        x, y, z = pos
                        xz = (x, z)
                        if y > tops.get(xz, -10 ** 9):
                            tops[xz] = y
            # Real ground supersedes ghost only in columns it wrote: drop ghost above that
            # column's real top (exact positions already lost via later-wins). Keep ghost
            # substrate below the top and every ghost column the ground plan did not cover.
            if e.pass_ == 'ground' and e.status == 'landed' and e.cell == cell and tops:
                for pos in [p for p, o in owners.items() if o.pass_ == 'ghost-ground']:
                    top = tops.get((pos[0], pos[2]))
                    if top is not None and pos[1] > top:
                        del owners[pos]
        if cacheable:
            # Ownership is reconstructed independently per cell; retain only the latest.
            self._owner_cache.clear()
            self._owner_cache[cell] = owners
        return owners

    def _owners_in(self, positions: Iterable[Pos], *, exclude: frozenset[str] = frozenset(),
                   skip: frozenset[int] = frozenset()) -> dict[Pos, _Owner]:
        by_cell: dict[Cell, list[Pos]] = {}
        for p in positions:
            by_cell.setdefault(self._cell_of(p), []).append(p)
        out: dict[Pos, _Owner] = {}
        for cell, plist in by_cell.items():
            owners = self._cell_owners(cell, exclude=exclude, skip=skip)
            for p in plist:
                o = owners.get(p)
                if o is not None:
                    out[p] = o
        return out

    def owned(self, within: Iterable[Pos], exclude: Iterable[str] = (), *,
              real_only: bool = False) -> dict[Pos, tuple[str, str]]:
        """Current owners of positions, like the old ``built_cells(exclude, within)``.

        @param within: positions to look up
        @param exclude: plan names to ignore (their blocks show earlier owners)
        @param real_only: drop ghost owners (they yield and never clash)
        @returns: ``{pos: (plan, state)}`` for owned positions (ghost owners included unless ``real_only``)
        """
        self._sync()
        got = self._owners_in(within, exclude=frozenset(exclude))
        return {p: (o.plan, o.state) for p, o in got.items() if not (real_only and o.ghost)}

    def owners(self, cells: Iterable[Cell], exclude: Iterable[str] = (), *,
               real_only: bool = False) -> dict[Pos, tuple[str, str]]:
        """Every owned position inside whole cells (the old full ``built_cells()`` merge, per cell).

        @param cells: cells to scan
        @param exclude: plan names to ignore
        @param real_only: drop ghost owners
        @returns: ``{pos: (plan, state)}``
        """
        self._sync()
        ex = frozenset(exclude)
        out: dict[Pos, tuple[str, str]] = {}
        for cell in {tuple(c) for c in cells}:
            for p, o in self._cell_owners(cell, exclude=ex).items():
                if not (real_only and o.ghost):
                    out[p] = (o.plan, o.state)
        return out

    def recorded(self, plan: str) -> dict[Pos, str] | None:
        """The recorded plan of the latest live (not undone) build entry of ``plan`` (the old ``*-cells.json``).

        @param plan: plan name
        @returns: ``{pos: state}`` copy, or None when the journal has no such build
        """
        self._sync()
        e = self._latest(plan)
        return None if e is None else dict(self._blob(e.blob))

    def cells_at(self, seq: int) -> dict[Pos, str] | None:
        """Plan cells stored on journal entry ``seq`` (build or undo blob).

        @param seq: begin-line seq of the entry
        @returns: ``{pos: state}`` copy, or None when there is no blob-bearing entry at ``seq``
        """
        self._sync()
        e = self._entries.get(int(seq))
        if e is None or not e.blob:
            return None
        return dict(self._blob(e.blob))

    def touched(self, passes: Iterable[str] = REAL_PASSES) -> dict[str, set[tuple[int, int]]]:
        """XZ columns of each plan's latest live build entry (the old ``touched_by_build``).

        @param passes: passes to include (default: Elements / ``REAL_PASSES``)
        @returns: ``{plan: {(x, z), ...}}``
        """
        self._sync()
        want = frozenset(passes)
        out: dict[str, set[tuple[int, int]]] = {}
        for plan in self._plans:
            e = self._latest(plan)
            if e is not None and e.pass_ in want:
                out[plan] = {(x, z) for x, _y, z in self._blob(e.blob)}
        return out

    def entries(self, cells: Iterable[Cell] | None = None, plan: str | None = None) -> tuple[EntryView, ...]:
        """Journalled build/undo entries in seq order (read-only).

        @param cells: only entries touching one of these cells
        @param plan: only entries of this plan name
        @returns: EntryView tuple
        """
        self._sync()
        scope = None if cells is None else {tuple(c) for c in cells}
        out = []
        for seq in sorted(self._entries):
            e = self._entries[seq]
            if plan is not None and e.plan != plan:
                continue
            if scope is not None and not scope & set(e.cells):
                continue
            out.append(EntryView(e.seq, e.kind, e.plan, e.pass_, e.cell, e.cells, e.status, e.undone, e.n,
                                 e.owner, e.ts, dict(e.meta), e.targets, e.mismatches, e.finish_seq, e.schema))
        return tuple(out)

    def planning_revision(self) -> str:
        """Identity of durable geometry and planning assumptions for an unexecuted plan.

        This is separate from completion: a ground redo changes future planning
        inputs without invalidating already landed trees/decor. Begun, mismatched
        and abandoned attempts, markers and validation/review notes are excluded.
        """
        self._sync()
        if self._planning_revision is None:
            geometry = [(e.seq, e.finish_seq, e.kind, e.plan, e.pass_, e.blob, e.cells,
                         e.undone, e.targets) for e in self._entries.values()
                        if e.status == 'landed' and e.pass_ in REAL_PASSES + GHOST_PASSES]
            assumptions = [n for n in self._notes if n['kind'] in
                           ('plan_feature', 'plan_feature-dismiss', 'clip_coverage-dismiss',
                            'coverage', 'coverage-dismiss')]
            body = json.dumps([geometry, assumptions], sort_keys=True, separators=(',', ':')).encode()
            self._planning_revision = hashlib.sha256(body).hexdigest()
        return self._planning_revision

    def ticket(self, seq: int) -> 'Ticket':
        """Re-open the ticket of a ``begun`` entry (recovery: read back, then ``finish`` or ``abandon``).

        @param seq: journal seq of the begun entry
        @returns: Ticket whose ``writes`` is the recorded plan
        @raises Refused: no such begun entry
        """
        self._sync()
        e = self._entries.get(seq)
        if e is None or e.status != 'begun':
            raise Refused(f'entry {seq} is not in flight')
        return Ticket(self, seq, _writes_for(e, self._blob(e.blob), seq=seq), e.strict or e.masked)

    # ---- views -----------------------------------------------------------------------------
    def _note_state(self) -> dict:
        if self._note_cache is not None:
            return self._note_cache
        st: dict = {
            'marker': None, 'holds': {}, 'warn': {}, 'open': {}, 'cells': set(),
            'plan': {}, 'plan_dismissed': {}, 'clip_dismissed': set(), 'approved': {},
        }
        for n in self._notes:
            cell, kind, ref, body = tuple(n['cell']), n['kind'], n.get('ref', ''), n.get('body', '')
            st['cells'].add(cell)
            if kind == 'marker':
                if body == 'up':
                    st['marker'] = cell
                elif st['marker'] == cell:
                    st['marker'] = None
            elif kind == 'hold':
                if body:
                    st['holds'][cell] = body
                else:
                    st['holds'].pop(cell, None)
            elif kind == 'coverage':
                st['warn'].setdefault(cell, {}).setdefault(ref, body)
            elif kind == 'coverage-dismiss':
                st['warn'].get(cell, {}).pop(ref, None)
            elif kind == 'clip_coverage-dismiss':
                st['clip_dismissed'].add(cell)
            elif kind == 'approval':              # latest approval replaces: it names the layers now covered
                st['approved'][cell] = frozenset(x for x in body.split(',') if x)
            elif kind == 'plan_feature':
                st['plan'].setdefault(cell, {})[ref] = body
                st['plan_dismissed'].get(cell, {}).pop(ref, None)
            elif kind == 'plan_feature-dismiss':
                st['plan'].get(cell, {}).pop(ref, None)
                st['plan_dismissed'].setdefault(cell, {})[ref] = body
            elif kind in ('review', 'issue'):
                st['open'].setdefault(cell, {})[ref or f'#{n["seq"]}'] = NoteView(kind, ref, body)
            elif kind == 'close':
                st['open'].get(cell, {}).pop(ref, None)
        self._note_cache = st
        return st

    def _derive(self, cell: Cell) -> CellView:
        got = self._derived.get(cell)
        if got is not None:
            return got
        real = ghost_ground = trees = decor = False
        events: list[tuple[int, int, _Entry]] = []
        flight: list[InFlightView] = []
        for seq in self._by_cell.get(cell, ()):
            e = self._entries[seq]
            if e.status == 'begun':
                flight.append(InFlightView(e.seq, e.plan, e.pass_))
            if e.kind != 'build' or e.undone:
                continue
            events.append((e.seq, 0, e))
            if e.status == 'landed':
                events.append((e.finish_seq, 1, e))
        for _seq, phase, e in sorted(events, key=lambda t: (t[0], t[1])):
            if phase == 0:
                if cell in e.reopened:
                    trees = decor = False               # reopening a built feature clears trees+decor
                continue
            mine = e.cell == cell
            if e.pass_ == 'ground' and mine:
                real, ghost_ground = True, False
            elif e.pass_ == 'feature':
                trees = decor = False                  # first landings change layout too
            elif e.pass_ == 'trees' and mine:
                trees, decor = True, False
            elif e.pass_ == 'decor' and mine:
                decor = True
            elif e.pass_ == 'ghost-ground' and mine:
                ghost_ground = not real
            # ghost-features journal lines (legacy) do not affect ground/ghost views
        ns = self._note_state()
        live: dict[str, str] = {}
        ever: set[str] = set()
        for seq in self._by_cell.get(cell, ()):
            e = self._entries[seq]
            if e.kind != 'build' or e.pass_ != 'feature' or cell not in e.cells:
                continue
            if e.status == 'abandoned':
                continue
            ever.add(e.plan)
        for plan in ever:
            e = self._latest(plan)
            if e is not None and e.pass_ == 'feature' and cell in e.cells:
                live[plan] = 'built' if e.status == 'landed' else 'in-progress'
        dismissed = set(ns['plan_dismissed'].get(cell, ()))
        planned_notes = set(ns['plan'].get(cell, ()))
        ids = (ever | planned_notes | set(live)) - dismissed
        # A live Build still shows even if earlier dismissed (dismiss clears the
        # obligation only once the build is gone).
        ids |= set(live)
        feats = [FeatureState(fid, live.get(fid, 'planned')) for fid in ids]
        ground = 'generated' if real else ('ghost' if ghost_ground else 'none')
        warns = ns['warn'].get(cell, {})
        notes = tuple(NoteView('coverage', k, v) for k, v in warns.items()) + tuple(
            ns['open'].get(cell, {}).values())
        marker = ns['marker'] == cell
        hold = ns['holds'].get(cell)
        feats.sort(key=lambda f: f.id)
        left = []
        if ground != 'generated':
            left.append(f'ground={ground}')
        left += [f'feature {f.id}={f.status}' for f in feats if f.status != 'built']
        if ground == 'generated' and not trees:
            left.append('trees')
        if ground == 'generated' and not decor:
            left.append('decor')
        if marker:
            left.append('marker up')
        left += [f'coverage warning {k}' for k in warns]
        # Historical journal issue/close notes are kept in ``notes`` but do not
        # block DONE (#121); snags live in registry/issues.json (closed: issues-closed.json) via scripts/issues.py.
        if hold:
            left.append(f'hold: {hold}')
        left += [f'in-flight: {f.plan}' for f in flight]
        done = (ground == 'generated' and trees and decor and not marker and not warns
                and all(f.status == 'built' for f in feats) and not flight)
        view = CellView(cell, ground, GhostView(ghost_ground), trees, decor, tuple(feats),
                        tuple(flight), marker, hold, notes, done, tuple(left))
        self._derived[cell] = view
        return view

    def _known_cells(self) -> list[Cell]:
        return sorted(set(self._by_cell) | self._note_state()['cells'])

    def view(self, cells: Iterable[Cell] | None = None) -> dict[Cell, CellView]:
        """Frozen per-cell views folded from the journal.

        @param cells: cells wanted (each gets a view, empty by default); None = every cell the journal mentions
        @returns: ``{cell: CellView}``
        """
        self._sync()
        wanted = self._known_cells() if cells is None else [tuple(c) for c in cells]
        return {c: self._derive(c) for c in wanted}

    def cell(self, cell: Cell) -> CellView:
        """One cell's view (empty defaults when the journal never mentions it).

        @param cell: (tx, tz)
        @returns: CellView
        """
        return self.view([cell])[tuple(cell)]

    def marker_cell(self) -> Cell | None:
        """The cell with the marker up, or None."""
        self._sync()
        return self._note_state()['marker']

    def seeds(self) -> list[Cell]:
        """Cells with any Element started (generated ground, trees, decor, or a begun feature).

        Planned-only cell-plan notes do not seed the frontier (#128).
        """
        self._sync()
        out = []
        for c in self._known_cells():
            v = self._derive(c)
            started = any(f.status != 'planned' for f in v.features)
            if v.ground == 'generated' or v.trees or v.decor or started:
                out.append(c)
        return out

    def frontier(self, ring: int | None = None) -> frozenset[Cell]:
        """Seeds plus every cell within Chebyshev ``ring`` of a seed (default ring 2).

        @param ring: depth override
        @returns: frontier cells
        """
        depth = self.ring if ring is None else ring
        if depth < 0:
            raise Refused(f'ring must be >= 0, got {depth}')
        out: set[Cell] = set()
        for s in self.seeds():
            out.add(s)
            for r in range(1, depth + 1):
                out.update(cellmath.ring_cells(r, home=s))
        return frozenset(out)

    # ---- writing: prepare / begin / finish -------------------------------------------------
    def _prepare(self, intent: Build | Undo) -> _Prepared:
        if isinstance(intent, Build):
            return self._prepare_build(intent)
        if isinstance(intent, Undo):
            return self._prepare_undo(intent)
        raise Refused(f'unknown intent {type(intent).__name__}')

    def _check_flight(self, cells: Iterable[Cell]) -> None:
        cells = set(cells)
        hits = {s for s in self._begun if cells & set(self._entries[s].cells)}
        if hits:
            raise InFlight({c for s in hits for c in self._entries[s].cells if c in cells}, hits)

    def _prepare_build(self, b: Build) -> _Prepared:
        self._sync()
        if b.pass_ not in PASSES:
            raise Refused(f'unknown pass {b.pass_!r}; want one of {list(PASSES)}')
        if not b.plan:
            raise Refused('plan name is required')
        positions = {tuple(p): s for p, s in b.cells.items()}
        ghost = b.pass_ in GHOST_PASSES
        # Empty trees/decor (and ghost) are allowed when the cell is named: a reviewed
        # "nothing to place" pass must still finish the cell (#127 / S-012).
        empty_ok = b.cell is not None and (ghost or b.pass_ in ('trees', 'decor'))
        if not positions and not empty_ok:
            raise Refused(f'{b.plan}: empty plan')
        if b.cell is not None:
            primary = tuple(b.cell)
        else:
            primary = Counter(self._cell_of(p) for p in positions).most_common(1)[0][0]
        touched = self._cells_of(positions) | {primary}
        previous = self._latest(b.plan)
        if b.pass_ == 'feature' and previous is not None and previous.pass_ == 'feature':
            touched.update(previous.cells)
        self._check_flight(touched)
        policy_meta = self.build_policy(b, touched) if self.build_policy is not None else {}
        view = self._derive(primary)
        strip = bool(positions) and all(block_id(s) in CANCEL for s in positions.values())
        if b.pass_ in ('trees', 'decor') and view.ground != 'generated':
            raise Refused(f'{b.pass_} only on generated ground (cell {list(primary)} has ground={view.ground})')
        if b.pass_ == 'ghost-ground' and view.ground == 'generated':
            raise Refused(f'ghost ground over real ground (cell {list(primary)})')
        if b.ground_layer and b.pass_ != 'ground' and b.pass_ not in GHOST_PASSES:
            bare = sorted(c for c in self._cells_of(positions) | {primary} if self._derive(c).ground != 'generated')
            if bare:
                state = ', '.join(f'{list(c)} ground={self._derive(c).ground} '
                                  f'approved={sorted(self.approved_layers(c))}' for c in bare)
                raise Refused(f'{b.plan}: ground-layer work needs a landed ground pass first ({state}); '
                              'run the ground pass there, or drop the ground-layer blocks')
        if b.pass_ == 'ghost-features':
            raise Refused('ghost-features is withdrawn; use coloured ghost ground tops (task #68)')
        owners = self._owners_in(positions, exclude=frozenset(b.modifies) | {b.plan})
        # ``payload.keep`` (door openings / keep_edits) may overwrite earlier owners (#143).
        keep = set(getattr(b.payload, 'keep', ()) or ()) if b.payload is not None else set()
        clash = {p: (o.plan, o.state, positions[p]) for p, o in owners.items()
                 if not o.ghost and o.state != positions[p] and p not in keep}
        if clash:
            raise Clash(clash)
        actual = self.blocks.read_many(positions) if positions else {}
        if b.protect is not None and positions:
            hits = {p: actual[p] for p in positions if b.protect(p, actual[p])}
            if hits:
                raise Refused(f'{len(hits)} protected cells would change', hits)
        # Snapshot world blocks at planned positions so undo can restore unowned substrate
        # (legacy begin lines omit ``prior`` and keep under-owner-else-air).
        prior = {p: (s if (s := actual.get(p)) is not None else AIR) for p in positions} if positions else None
        record = {
            'op': 'begin', 'kind': 'build', 'plan': b.plan, 'pass': b.pass_, 'cell': list(primary),
            'cells': [list(c) for c in sorted(touched)], 'n': len(positions),
            'modifies': sorted(b.modifies), 'strict': bool(b.strict), 'strip': strip,
            'owner': os.environ.get('SITE_OWNER'), 'schema': JOURNAL_SCHEMA,
        }
        if b.mask is not None:
            record['mask'] = b.mask
        if b.meta or policy_meta:
            record['meta'] = {**(b.meta or {}), **(policy_meta or {})}
        signs, entities = _attachments(b)
        if signs:
            record['signs'] = _signs_json(signs)
        if entities:
            record['entities'] = _entities_json(entities)
        writes = Writes(b.plan, b.pass_, positions, 'build', b, signs=signs, entities=entities)
        return _Prepared(record, writes, bool(b.strict or b.mask is not None), prior, actual)

    def _resolve_targets(self, target: str | tuple[Cell, str]) -> list[_Entry]:
        out = []
        for e in self._entries.values():
            if e.kind != 'build' or e.undone or e.status == 'begun':
                continue
            if isinstance(target, str):
                if e.plan == target:
                    out.append(e)
            else:
                cell, pass_ = tuple(target[0]), target[1]
                if e.pass_ == pass_ and (e.cell == cell or (pass_ == 'feature' and cell in e.cells)):
                    out.append(e)
        return out

    def _prepare_undo(self, u: Undo) -> _Prepared:
        self._sync()
        targets = self._resolve_targets(u.target)
        if not targets:
            raise Refused(f'nothing to undo for {u.target!r}')
        cells = {c for e in targets for c in e.cells}
        self._check_flight(cells)
        tseqs = frozenset(e.seq for e in targets)
        # Per position: earliest targeted build that wrote it — its prior is the world
        # before any of the targeted rebuilds (#138). Using the current (latest) owner's
        # prior would restore the first build instead of the pre-first world.
        earliest: dict[Pos, _Entry] = {}
        positions: set[Pos] = set()
        for e in sorted(targets, key=lambda ent: ent.seq):
            for p in self._blob(e.blob):
                positions.add(p)
                earliest.setdefault(p, e)
        current = self._owners_in(positions)
        under = self._owners_in(positions, skip=tseqs)
        erase: dict[Pos, str] = {}
        by_prior: dict[str, list[Pos]] = {}
        for p, o in current.items():
            if o.seq not in tseqs:
                continue
            if p in under:
                erase[p] = under[p].state
            else:
                src = earliest.get(p, self._entries[o.seq])
                if src.prior is not None:
                    by_prior.setdefault(src.prior, []).append(p)
                else:
                    erase[p] = AIR                      # legacy begin: no prior blob
        for prior, fallback in by_prior.items():
            # A prior larger than the cache budget is evicted immediately. Hold it only
            # for this group so undo decodes it once, without retaining all prior blobs.
            prior_cells = self._blob(prior)
            erase.update((p, prior_cells.get(p, AIR)) for p in fallback)
            del prior_cells
        # A feature built before its cell's ghost left those columns outside the ghost plan, so
        # nothing owns them and the prior is air: undo would dig a pit under the footprint.
        # Fall back to the ghost (lidar height) the cell is meant to have.
        ghost_plan: dict[Cell, Mapping[Pos, str]] = {}
        ghost_filled: set[Pos] = set()
        # Undoing a ghost plan itself must erase it, not rewrite it.
        if not any(e.pass_ in GHOST_PASSES for e in targets):
            for p, state in erase.items():
                if block_id(state) not in AIR_IDS or p in under:
                    continue
                cell = self._cell_of(p)
                if cell not in ghost_plan:
                    want = self.ghosts(cell, 'ground') if self.ghosts is not None and self._derive(cell).ground == 'ghost' else None
                    ghost_plan[cell] = {tuple(q): s for q, s in (want or {}).items()}
                fill = ghost_plan[cell].get(p)
                if fill is not None and block_id(fill) not in AIR_IDS:
                    erase[p] = fill
                    ghost_filled.add(p)
        names = sorted({e.plan for e in targets})
        first = targets[-1]
        record = {
            'op': 'begin', 'kind': 'undo', 'plan': f'undo:{",".join(names)}', 'pass': first.pass_,
            'cell': list(first.cell), 'cells': [list(c) for c in sorted(cells)], 'n': len(erase),
            'targets': sorted(tseqs), 'strict': bool(u.strict), 'owner': os.environ.get('SITE_OWNER'),
        }
        if u.meta:
            record['meta'] = dict(u.meta)
        return _Prepared(record, Writes(record['plan'], first.pass_, erase, 'undo', u, ghost_filled=frozenset(ghost_filled)), bool(u.strict))

    def _begin(self, prep: _Prepared) -> Ticket:
        record = dict(prep.record, blob=self._store_blob(prep.writes.cells))
        if prep.prior is not None:
            record['prior'] = self._store_blob(prep.prior)
        seq = self._append(record)
        return Ticket(self, seq, replace(prep.writes, seq=seq), prep.strict,
                      prep.observed if record['kind'] == 'build' else None)

    def begin(self, intent: Build | Undo) -> Ticket:
        """Journal ``begun`` (fsynced) and return the ticket; no block has been written yet.

        @param intent: Build or Undo
        @returns: Ticket (write ``ticket.writes``, flush, read back, ``ticket.finish(readback)``)
        @raises Refused: a rule is broken (bad pass, trees without ground, protect hit, ...)
        @raises InFlight: a ``begun`` entry covers the cells
        @raises Clash: planned blocks belong to a real earlier owner
        @raises JournalError: the begin line was not written (so no block may be written)
        """
        return self._begin(self._prepare(intent))

    def _finish(self, t: Ticket, readback: Mapping[Pos, str | None]) -> Receipt:
        self._sync()
        e = self._entries[t.seq]
        if e.status != 'begun':
            raise Refused(f'entry {t.seq} is already {e.status}')
        bad = {p: [s, readback.get(p)] for p, s in t.writes.cells.items()
               if not same_state(s, readback.get(p))}
        sample = {f'{x},{y},{z}': v for (x, y, z), v in list(bad.items())[:SAMPLE]}
        if bad and t.strict:
            raise Mismatch(t.seq, len(bad), sample)
        status = 'mismatched' if bad else 'landed'
        self._append({'op': 'finish', 'ref': t.seq, 'status': status, 'mismatches': len(bad)})
        return Receipt(t.seq, status, len(t.writes.cells), len(bad), sample)

    def _abandon(self, t: Ticket, reason: str) -> None:
        self._sync()
        e = self._entries[t.seq]
        if e.status != 'begun':
            raise Refused(f'entry {t.seq} is already {e.status}')
        self._append({'op': 'finish', 'ref': t.seq, 'status': 'abandoned', 'mismatches': 0, 'reason': reason})

    def revive(self, targets: Iterable[int], *, undo_seq: int | None = None) -> None:
        """Clear ``undone`` on Build entries after a recover restored the pre-undo world.

        Journals a ``revive`` line so ownership/views match the restored blocks. When ``undo_seq``
        is set, that undo entry is itself marked undone (cancelled).

        @param targets: journal seqs of the Builds the undo had voided
        @param undo_seq: optional seq of the landed Undo that is being cancelled
        @raises JournalError: the revive line could not be written
        """
        self._sync()
        record: dict = {'op': 'revive', 'targets': sorted({int(t) for t in targets})}
        if undo_seq is not None:
            record['undo'] = int(undo_seq)
        self._append(record)

    def change(self, intent: Build | Undo | Note, apply: Apply | None = None, *,
               cascade: bool = False) -> Receipt:
        """The only writer: journal, let ``apply`` write the blocks, read back, finish.

        @param intent: :class:`Build`, :class:`Undo` or :class:`Note`
        @param apply: callable that writes ``Writes.cells`` (required when blocks change)
        @param cascade: also apply the follow-up Builds (strips, ghost ensure) after a landed change
        @returns: Receipt (``follow_ups`` lists what is still needed after a landed real change)
        @raises Refused: see :meth:`begin`; also when blocks must change but ``apply`` is None
        @raises Mismatch: strict/mask readback failed (entry stays ``begun``)
        """
        if isinstance(intent, Note):
            return self._note(intent)
        prep = self._prepare(intent)
        if prep.writes.cells and apply is None:
            raise Refused('an apply callable is required to write blocks')
        ticket = self._begin(prep)
        if ticket.writes.cells:
            apply(ticket.writes)
            mark = getattr(self.blocks, 'mark_written', None)
            if mark is not None:
                mark()                                  # LiveBlocks: refuse reads until flush
            self.blocks.flush()
        receipt = ticket.finish(self.blocks.read_many(ticket.writes.cells) if ticket.writes.cells else {})
        if receipt.status != 'landed':
            return receipt
        if prep.record['kind'] == 'undo' or prep.record['pass'] in REAL_PASSES:
            receipt = replace(receipt, follow_ups=self.follow_ups())
            if cascade:
                receipt = replace(receipt, followed=tuple(self.ensure(apply)))
        return receipt

    def _note(self, n: Note) -> Receipt:
        self._sync()
        if n.kind not in NOTE_KINDS:
            raise Refused(f'unknown note kind {n.kind!r}; want one of {list(NOTE_KINDS)}')
        cell = tuple(n.cell)
        if n.kind == 'marker' and n.body not in ('up', 'none'):
            raise Refused(f'marker body must be up|none, got {n.body!r}')
        if n.kind in ('coverage', 'coverage-dismiss', 'close',
                      'plan_feature', 'plan_feature-dismiss') and not n.ref:
            raise Refused(f'{n.kind} needs a ref')
        if n.kind == 'coverage-dismiss':
            if not n.body.strip():
                raise Refused('dismissing a coverage warning needs a reason')
            if n.ref not in self._note_state()['warn'].get(cell, {}):
                raise Refused(f'no open coverage warning {n.ref!r} on cell {list(cell)}')
        if n.kind == 'plan_feature-dismiss':
            if not n.body.strip():
                raise Refused('dismissing a planned feature needs a reason')
            open_ids = self._plan_feature_ids(cell)
            if n.ref not in open_ids:
                raise Refused(f'no planned feature {n.ref!r} on cell {list(cell)}')
        if n.kind == 'approval':
            layers = [x for x in n.body.split(',') if x]
            bad = [x for x in layers if x not in APPROVAL_LAYERS]
            if not layers or bad:
                raise Refused(f'approval body must list layers from {list(APPROVAL_LAYERS)}, got {n.body!r}')
        if n.kind == 'clip_coverage-dismiss' and not n.body.strip():
            raise Refused('dismissing clip query coverage needs a reason')
        if n.kind == 'builder_registration':
            self._check_builder_registration(n)
        seq = self._append({'op': 'note', 'cell': list(cell), 'kind': n.kind, 'ref': n.ref, 'body': n.body})
        return Receipt(seq, 'landed')

    def _check_builder_registration(self, n: Note) -> None:
        """Bind a registration to the reviewed live feature entry without rewriting its history."""
        try:
            target = int(n.ref)
            body = json.loads(n.body)
            builder = body['builder']
            valid = (builder['kind'] == 'spec' and bool(builder['path'].strip())
                     and bool(builder['name'].strip()) and bool(body['reason'].strip())
                     and len(body['spec_hash']) == 64
                     and all(c in '0123456789abcdef' for c in body['spec_hash']))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise Refused('builder registration needs a builder, spec hash and verification reason') from exc
        if not valid:
            raise Refused('builder registration needs a builder, spec hash and verification reason')
        e = self._entries.get(target)
        if (e is None or e.kind != 'build' or e.pass_ != 'feature' or e.status != 'landed'
                or e.undone or self._latest(e.plan) is not e or tuple(n.cell) != e.cell):
            raise Refused(f'entry {target} is not the latest live landed feature build in this cell')
        if (e.meta.get('builder') or e.meta.get('builder_registration')
                or (e.meta.get('builder_name') and e.meta.get('spec_path'))):
            raise Refused(f'entry {target} already has a recorded or registered builder')
        flying = [seq for seq in self._begun if set(self._entries[seq].cells) & set(e.cells)]
        if flying:
            raise InFlight(e.cells, flying)

    def approved_layers(self, cell: Cell) -> frozenset[str]:
        """Layers the latest approval note for this cell covers (#327).

        @param cell: (tx, tz)
        @returns: layer names from :data:`APPROVAL_LAYERS`; empty when the cell has no approval note
        """
        self._sync()
        return self._note_state()['approved'].get(tuple(cell), frozenset())

    def clip_coverage_dismissed(self, cell: Cell) -> bool:
        """Whether incomplete query coverage was deliberately accepted for this cell.

        @param cell: (tx, tz)
        @returns: True when a clip_coverage-dismiss note exists
        """
        self._sync()
        return tuple(cell) in self._note_state()['clip_dismissed']

    def notes(self, cell: Cell, *, kinds: Iterable[str] | None = None) -> tuple[dict, ...]:
        """Copies of durable notes, including superseded validation and dismissal history."""
        self._sync()
        return tuple(dict(n) for n in self._notes if tuple(n['cell']) == tuple(cell)
                     and (kinds is None or n['kind'] in kinds))

    def plan_dismissed_ids(self, cell: Cell) -> set[str]:
        """Feature ids deliberately dismissed from the cell plan (#128).

        A later ``plan_feature`` note clears a dismissal; ``propose-features --record``
        must skip these ids so a re-run does not undo the dismiss.

        @param cell: (tx, tz)
        @returns: dismissed feature plan ids for the cell
        """
        self._sync()
        return set(self._note_state()['plan_dismissed'].get(tuple(cell), ()))

    def _plan_feature_ids(self, cell: Cell) -> set[str]:
        """Feature ids still on the cell plan (notes + ever-built, minus dismissals).

        @param cell: (tx, tz)
        @returns: open plan feature ids (excludes live-only that were dismissed while built)
        """
        ns = self._note_state()
        dismissed = set(ns['plan_dismissed'].get(cell, ()))
        ever: set[str] = set()
        for seq in self._by_cell.get(cell, ()):
            e = self._entries[seq]
            if (e.kind == 'build' and e.pass_ == 'feature' and cell in e.cells
                    and e.status != 'abandoned'):
                ever.add(e.plan)
        return (ever | set(ns['plan'].get(cell, ()))) - dismissed

    # ---- follow-ups: ghost ground ensure ---------------------------------------------------
    def _ghost_needs(self, scope: set[Cell] | None = None) -> list[tuple[Cell, str]]:
        needs = []
        for cell in sorted(self.frontier()):
            if scope is not None and cell not in scope:
                continue
            v = self._derive(cell)
            if v.in_flight:
                continue
            if v.ground == 'none':
                needs.append((cell, 'ground'))
        return needs

    def _ghost_build(self, cell: Cell, layer: str) -> Build | None:
        if layer != 'ground':
            return None
        planned = self.ghosts(cell, layer) if self.ghosts is not None else None
        if planned is None:
            return None
        positions = {tuple(p): s for p, s in planned.items()}
        owners = self._owners_in(positions)
        positions = {p: s for p, s in positions.items() if p not in owners or owners[p].ghost}
        name = ghost_name(cell, layer)
        return Build(name, f'ghost-{layer}', positions, modifies=(name,), strict=True, cell=cell)

    def follow_ups(self, cells: Iterable[Cell] | None = None) -> tuple[Build, ...]:
        """Builds still needed: ghost ground for every frontier cell that lacks it (needs a
        ``ghosts`` planner). Idempotent and derived: applying them removes them from this list.

        @param cells: restrict to these cells (default all)
        @returns: Build intents for the caller to ``change``
        """
        self._sync()
        scope = None if cells is None else {tuple(c) for c in cells}
        out: list[Build] = []
        for cell, layer in self._ghost_needs(scope):
            b = self._ghost_build(cell, layer)
            if b is not None:
                out.append(b)
        return tuple(out)

    def ensure(self, apply: Apply, *, cells: Iterable[Cell] | None = None, rounds: int = 8) -> list[Receipt]:
        """Apply follow-ups until none are left (frontier ghost ground).

        @param apply: block writer
        @param cells: restrict to these cells
        @param rounds: safety bound
        @returns: receipts in application order
        @raises Refused: follow-ups did not settle within ``rounds``
        """
        out: list[Receipt] = []
        warned = False
        for _ in range(rounds):
            pending = self.follow_ups(cells)
            if not pending:
                return out
            if not warned:
                announce_ghost_generation(len(pending))
                warned = True
            for b in pending:
                out.append(self.change(b, apply))
        if self.follow_ups(cells):
            raise Refused(f'follow-ups did not settle after {rounds} rounds')
        return out

    # ---- reconcile -------------------------------------------------------------------------
    def _classify(self, owners: Mapping[Pos, _Owner]) -> tuple[dict[Pos, tuple[str, str | None]], dict[Pos, tuple[str, str | None]]]:
        actual = self.blocks.read_many(owners)
        missing, foreign = {}, {}
        for pos, o in owners.items():
            got = actual[pos]
            if same_state(o.state, got):
                continue
            (missing if got is None or got in AIR_IDS or block_id(got) in AIR_IDS else foreign)[pos] = (o.state, got)
        return missing, foreign

    def _finish_line(self, seq: int, status: str, mismatches: int) -> None:
        self._append({'op': 'finish', 'ref': seq, 'status': status, 'mismatches': mismatches})

    def reconcile(self, cells: Iterable[Cell] | None = None, *, fix: bool = False,
                  apply: Apply | None = None) -> Drift:
        """Compare the journal with the blocks (flushes the store first).

        Kinds: ``in-flight`` (a ``begun`` entry), ``missing`` / ``foreign`` (a position the fold owns is
        absent / holds another block), ``ghost-missing`` (frontier cell lacks ghost ground or features;
        reported only when a ``ghosts`` planner is set), ``ghost-stale`` (ghost features still owned
        inside a real feature's footprint).

        With ``fix`` (needs ``apply``): begun entries are read back and landed, re-writing the recorded plan
        first when it does not match; missing/foreign positions are re-written from their current owner
        (journalled as an informational ``repair`` line); mismatched entries whose owned blocks now read
        back are upgraded to ``landed``; follow-up Builds are applied. Re-written plans carry journalled
        signs (always) and entities (when the full plan is re-committed, not on a partial cell repair).

        @param cells: cells in scope (default: every cell the journal mentions)
        @param fix: repair what is found
        @param apply: block writer, required with ``fix``
        @returns: Drift
        @raises Refused: ``fix`` without ``apply``
        """
        if fix and apply is None:
            raise Refused('reconcile(fix=True) needs an apply callable')
        self._sync()
        scope = None if cells is None else {tuple(c) for c in cells}
        self.blocks.flush()
        items: list[DriftItem] = []
        # 1. in-flight entries
        for seq in sorted(self._begun):
            e = self._entries[seq]
            hit = set(e.cells) if scope is None else set(e.cells) & scope
            if not hit:
                continue
            writes = _writes_for(e, self._blob(e.blob), seq=e.seq)
            bad = self._bad(writes.cells)
            fixed = False
            if fix:
                if bad or writes.signs or writes.entities:
                    # blocks may already match while signs/entities were skipped mid-apply
                    apply(writes)
                    self._flush_after_write()
                    bad = self._bad(writes.cells)
                self._finish_line(seq, 'mismatched' if bad else 'landed', len(bad))
                fixed = not bad
            items.append(DriftItem(min(hit), 'in-flight', seq, len(bad) if bad else 0,
                                   {f'{x},{y},{z}': v for (x, y, z), v in list(bad.items())[:5]},
                                   (e.plan,), fixed))
        # 2. missing / foreign blocks under landed or mismatched entries
        upgraded = 0
        for cell in self._known_cells():
            if scope is not None and cell not in scope:
                continue
            owners = {p: o for p, o in self._cell_owners(cell).items() if o.seq not in self._begun}
            missing, foreign = self._classify(owners)
            repaired = 0
            for kind, found in (('missing', missing), ('foreign', foreign)):
                if not found:
                    continue
                plans = sorted({owners[p].plan for p in found})
                seq = max(owners[p].seq for p in found)
                sample = {f'{x},{y},{z}': list(v) for (x, y, z), v in list(found.items())[:5]}
                fixed = False
                if fix:
                    by_owner: dict[int, dict[Pos, str]] = {}
                    for p in found:
                        by_owner.setdefault(owners[p].seq, {})[p] = owners[p].state
                    for oseq, part in sorted(by_owner.items()):
                        o = self._entries[oseq]
                        full = set(part) == set(self._blob(o.blob))
                        apply(_writes_for(o, part, kind='repair', seq=None, full_plan=full))
                    repaired += len(found)
                    self._flush_after_write()
                    fixed = not self._bad({p: owners[p].state for p in found})
                items.append(DriftItem(cell, kind, seq, len(found), sample, tuple(plans), fixed))
            if repaired:
                self._append({'op': 'repair', 'cell': list(cell), 'count': repaired})
        cleared: list[tuple[str, int]] = []
        if fix:
            cleared = self._upgrade_mismatched(scope)
            upgraded = len(cleared)
        # 2b. entries still mismatched: they block preflight until landed, so each is its own finding
        items += self._mismatched_items(scope)
        # 3. ghosts
        if self.ghosts is not None:
            for cell, layer in self._ghost_needs(scope):
                items.append(DriftItem(cell, 'ghost-missing', 0, 1, {}, (ghost_name(cell, layer),), False))
        if fix:
            self.ensure(apply, cells=scope)
            need = {c for c, _l in self._ghost_needs(scope)}
            items = [replace(i, fixed=True) if (i.kind == 'ghost-missing' and i.cell not in need)
                     else i for i in items]
        return Drift(tuple(items), upgraded, tuple(cleared))

    def _bad(self, planned: Mapping[Pos, str]) -> dict[Pos, list]:
        actual = self.blocks.read_many(planned)
        return {p: [s, actual[p]] for p, s in planned.items() if not same_state(s, actual[p])}

    def _flush_after_write(self) -> None:
        mark = getattr(self.blocks, 'mark_written', None)
        if mark is not None:
            mark()
        self.blocks.flush()

    def _mismatch_mine(self, seq: int, e: _Entry) -> dict[Pos, str]:
        """Positions a mismatched entry still owns in the fold (superseded ones are not its to answer for)."""
        mine: dict[Pos, str] = {}
        for cell in e.cells:
            mine.update({p: o.state for p, o in self._cell_owners(cell).items() if o.seq == seq})
        return mine

    def owned_count(self, seq: int) -> int:
        """Positions the entry ``seq`` still owns after later landed entries (0: fully superseded).

        @param seq: journal seq of a build entry
        @returns: owned position count across all the entry's cells
        """
        self._sync()
        e = self._entries.get(seq)
        return len(self._mismatch_mine(seq, e)) if e is not None else 0

    def owns_any(self, seq: int, first: Cell | None = None) -> bool:
        """Whether the entry ``seq`` still owns any position; stops at the first cell where it does.

        Cheaper than :meth:`owned_count` for a yes/no (each cell fold costs seconds on a big world).

        @param seq: journal seq of a build entry
        @param first: cell to fold first (the caller's cell, most likely to answer yes)
        @returns: False when later landed entries wholly supersede it (or it is unknown)
        """
        self._sync()
        e = self._entries.get(seq)
        if e is None:
            return False
        cells = sorted(e.cells, key=lambda c: c != first)
        return any(o.seq == seq for c in cells for o in self._cell_owners(c).values())

    def _upgrade_mismatched(self, scope: set[Cell] | None) -> list[tuple[str, int]]:
        cleared = []
        for seq, e in list(self._entries.items()):
            if e.status != 'mismatched' or e.undone or (scope is not None and not set(e.cells) & scope):
                continue
            if not self._bad(self._mismatch_mine(seq, e)):
                self._finish_line(seq, 'landed', 0)
                cleared.append((e.plan, seq))
        return cleared

    def _mismatched_items(self, scope: set[Cell] | None) -> list[DriftItem]:
        """One finding per mismatched entry that blocks preflight: the latest live build of its plan
        that still owns a position (a wholly superseded one blocks nothing, #284)."""
        latest: dict[str, int] = {}
        for seq, e in self._entries.items():
            if e.kind == 'build' and not e.undone:
                latest[e.plan] = max(seq, latest.get(e.plan, 0))
        out = []
        for seq, e in self._entries.items():
            if e.kind != 'build' or e.status != 'mismatched' or e.undone or latest.get(e.plan) != seq:
                continue
            if scope is not None and not set(e.cells) & scope:
                continue
            mine = self._mismatch_mine(seq, e)
            if not mine:
                continue
            bad = self._bad(mine)
            sample = {f'{x},{y},{z}': v for (x, y, z), v in list(bad.items())[:5]}
            out.append(DriftItem(e.cell or min(e.cells), 'mismatched', seq, len(bad), sample,
                                 (e.plan,), False, tuple(sorted(e.cells)), e.pass_, e.cell))
        return out

    # ---- housekeeping ----------------------------------------------------------------------
    def reset(self) -> Path | None:
        """Start a fresh journal: the current file is renamed ``journal-<stamp>.jsonl`` (blobs are kept).

        @returns: path of the archived journal, or None when there was none
        @raises JournalError: the rename failed
        """
        self._sync()
        if not self.journal_path.exists():
            return None
        stamp = time.strftime('%Y%m%dT%H%M%S', time.gmtime())
        target = self.journal_path.with_name(f'journal-{stamp}-{self._seq}.jsonl')
        try:
            self.journal_path.replace(target)
            self._fsync_dir()
        except OSError as exc:
            raise JournalError(f'cannot archive journal: {exc}') from exc
        self._reset_state()
        return target

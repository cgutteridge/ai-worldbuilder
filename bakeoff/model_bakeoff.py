#!/usr/bin/env python3
"""Model bake-off framework: one model per sitting, self-contained round records.

  python3 bakeoff/model_bakeoff.py status
  python3 bakeoff/model_bakeoff.py next
  python3 bakeoff/model_bakeoff.py add-tool --id cursor --name Cursor
  python3 bakeoff/model_bakeoff.py add-model --tool cursor --id composer --tier low --label Composer
  python3 bakeoff/model_bakeoff.py open --tool cursor --model composer --task feature_design
  python3 bakeoff/model_bakeoff.py score R-001 --overall great --axis massing=great --axis keep=great
  python3 bakeoff/model_bakeoff.py usage R-001 --input 12000 --output 3000 --source cursor-ui
  python3 bakeoff/model_bakeoff.py usage R-001 --from-transcript ~/.claude/projects/.../session.jsonl --model-prefix claude-opus-5-5
  python3 bakeoff/model_bakeoff.py open --tool claude-code --model claude-opus-5-5-medium --plan-pct 45
  python3 bakeoff/model_bakeoff.py open --tool claude-code --model claude-sonnet-5-5-medium --designer claude-opus-5-5-low
  python3 bakeoff/model_bakeoff.py score R-001 --overall great --plan-pct 47 ...
  python3 bakeoff/model_bakeoff.py score R-001 --build-script worlds/bakeoff/site/builds/r013_susu.py ...
  python3 bakeoff/model_bakeoff.py usage R-001 --from-transcript session.jsonl  # hybrid: splits by_role
  python3 bakeoff/model_bakeoff.py repair R-001 --brief "raise ridge one block"
  python3 bakeoff/model_bakeoff.py baseline R-001
  python3 bakeoff/model_bakeoff.py baseline R-001 --plan
  python3 bakeoff/model_bakeoff.py capture R-001

Records live under ``bakeoff/`` (tool repo). See ``bakeoff/README.md``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'scripts'
for _path in (ROOT, SCRIPTS):
    _text = str(_path)
    if _text not in sys.path:
        sys.path.insert(0, _text)

import active_world  # noqa: E402
from atomic_json import write_json  # noqa: E402

DEFAULT_BAKEOFFS = ROOT / 'bakeoff'
# Prefix every bake-off CLI call (agent shells do not keep a one-off export).
BAKEOFF_WORLD_PREFIX = 'AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff'

TIERS = ('low', 'mid', 'high')
GRADE_RANK = {'fail': 0, 'pass': 1, 'great': 2}
STATUSES = ('ready', 'in_progress', 'scored', 'skipped')
USAGE_SOURCES = (
    'cursor-ui',
    'api',
    'agent-report',
    'estimate',
    'unknown',
)
ROUND_TYPES = ('single', 'hybrid')
USAGE_ROLES = ('designer', 'builder')
HANDOFF_KINDS = ('script', 'spec', 'other')
# Catalogue ids often append effort/reasoning; Claude Code message.model does not.
TRANSCRIPT_EFFORT_SUFFIXES = ('-low', '-medium', '-high')


def transcript_model_prefix(catalogue_id: str) -> str:
    """Derive the API model-id prefix used in transcripts from a catalogue id.

    Catalogue rows often append an effort/reasoning suffix (``-low``, ``-medium``,
    ``-high``) that does not appear on Claude Code ``message.model``. Strip one
    trailing effort suffix so ``startswith`` filters match real transcripts
    (e.g. ``claude-opus-5-5-low`` → ``claude-opus-5-5``).

    @param catalogue_id: model id from catalogue.json / round record
    @returns: prefix for ``sum_transcript_usage(..., model_prefix=...)``
    """
    for suffix in TRANSCRIPT_EFFORT_SUFFIXES:
        if catalogue_id.endswith(suffix):
            return catalogue_id[: -len(suffix)]
    return catalogue_id



def bakeoffs_root(path: Path | None = None) -> Path:
    """Resolve the bake-off data root.

    @param path: optional override (tests)
    @returns: directory containing config.json / rounds/
    """
    return Path(path) if path is not None else DEFAULT_BAKEOFFS


def utc_now() -> str:
    """ISO-8601 UTC timestamp with trailing Z.

    @returns: timestamp string
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def load_json(path: Path) -> Any:
    """Load a JSON file.

    @param path: file path
    @returns: parsed value
    """
    return json.loads(path.read_text(encoding='utf-8'))


def load_config(root: Path) -> dict:
    """Load bake-off config.json.

    @param root: bake-offs root
    @returns: config dict
    """
    return load_json(root / 'config.json')


def sitting_checklist(
    cfg: dict,
    *,
    tool: str,
    model: str,
    pack: str,
    round_id: str = 'R-NNN',
    designer: str | None = None,
) -> list[str]:
    """Render sitting steps from config with round-specific fields.

    Uses ``sitting_steps_hybrid`` when ``designer`` is set (else ``sitting_steps``).
    Placeholders: ``{tool}``, ``{model}`` (builder / apply owner), ``{designer}``,
    ``{builder}`` (same as ``{model}``), ``{pack}``, ``{cell}`` (e.g. ``(48,-44)``),
    ``{id}`` (round id).

    @param cfg: bake-off config (must include ``sitting_steps``)
    @param tool: tool id for this round
    @param model: builder model id (apply owner)
    @param pack: pack id for this round
    @param round_id: round id for ``{id}`` (default ``R-NNN``)
    @param designer: optional designer model id (hybrid rounds)
    @returns: checklist strings for the round record / open card
    @raises ValueError: when the chosen steps list is missing or empty
    """
    key = 'sitting_steps_hybrid' if designer else 'sitting_steps'
    steps = cfg.get(key)
    if designer and (not isinstance(steps, list) or not steps):
        steps = cfg.get('sitting_steps')
    if not isinstance(steps, list) or not steps:
        raise ValueError(f'config.json missing non-empty {key} list')
    cell = cfg.get('cell') or []
    cell_s = f'({cell[0]},{cell[1]})' if len(cell) >= 2 else ''
    ctx = {
        'tool': tool,
        'model': model,
        'builder': model,
        'designer': designer or '',
        'pack': pack,
        'cell': cell_s,
        'id': round_id,
    }
    return [str(step).format(**ctx) for step in steps]


def is_hybrid_round(round_: dict) -> bool:
    """True when the round names a designer distinct from the builder.

    Same designer and builder id is a single-model sitting that still records
    both roles for clarity (see ``build_roles``); that is not hybrid.

    @param round_: round record
    @returns: whether this is a hybrid designer/builder sitting
    """
    designer = round_.get('designer')
    designer_id = designer.get('id') if isinstance(designer, dict) else None
    builder_id = (round_.get('model') or {}).get('id')
    return bool(designer_id and builder_id and designer_id != builder_id)


def model_ref(row: dict) -> dict:
    """Compact model identity for a round record.

    @param row: catalogue model row
    @returns: id/label/tier/bulk_ok dict
    """
    return {
        'id': row['id'],
        'label': row.get('label') or row['id'],
        'tier': row['tier'],
        'bulk_ok': bool(row.get('bulk_ok', True)),
    }


def handoff_kind_for_path(path: Path | str) -> str:
    """Classify a hand-off artefact path as script, spec, or other.

    @param path: file path
    @returns: one of HANDOFF_KINDS
    """
    suf = Path(path).suffix.lower()
    if suf == '.py':
        return 'script'
    if suf == '.json':
        return 'spec'
    return 'other'


def handoff_template(path: Path | str | None = None) -> dict:
    """Empty or path-filled hand-off block for a hybrid round.

    @param path: optional designer artefact path (spec or build script)
    @returns: handoff dict
    """
    if path is None:
        return {'path': None, 'kind': None, 'recorded_at': None}
    p = Path(path)
    return {
        'path': str(p),
        'kind': handoff_kind_for_path(p),
        'recorded_at': utc_now(),
    }


def set_handoff(round_: dict, path: Path | str) -> dict:
    """Record the designer→builder hand-off artefact on a round.

    @param round_: round record (mutated)
    @param path: artefact path (script or feature_build spec)
    @returns: the handoff dict stored on the round
    """
    block = handoff_template(path)
    round_['handoff'] = block
    # Also remember for score-time artefact copy when it is a build script.
    p = Path(path)
    if p.suffix.lower() in ARTEFACT_TEXT_SUFFIXES:
        stored = list(round_.get('build_scripts') or [])
        s = str(p)
        if s not in stored:
            stored.append(s)
            round_['build_scripts'] = stored
    return block


def validate_catalogue_model(row: dict, *, role: str) -> None:
    """Refuse models that are not bulk_ok or outside active tiers (caller checks tier).

    @param row: catalogue model row
    @param role: human label for errors (builder / designer)
    """
    mid = row.get('id')
    if not row.get('bulk_ok', True):
        raise ValueError(f'{role} model {mid!r} is not bulk_ok — skip for bake-offs')


def usage_role_template() -> dict:
    """Empty per-role usage counts (designer or builder).

    @returns: token/request fields only (plan % stays on the top-level usage block)
    """
    return {
        'input_tokens': None,
        'output_tokens': None,
        'total_tokens': None,
        'cache_read_tokens': None,
        'cache_write_tokens': None,
        'requests': None,
        'cost_usd': None,
        'source': None,
        'recorded_at': None,
        'notes': None,
        'first_timestamp': None,
        'last_timestamp': None,
        'model_prefix': None,
    }


def _sum_usage_dicts(parts: list[dict]) -> dict[str, int | None]:
    """Add numeric usage fields across role blocks (None-safe).

    @param parts: usage-like dicts
    @returns: summed counts (None when every part left that field None)
    """
    keys = (
        'input_tokens',
        'output_tokens',
        'total_tokens',
        'cache_read_tokens',
        'cache_write_tokens',
        'requests',
    )
    out: dict[str, int | None] = {}
    for key in keys:
        vals = [p.get(key) for p in parts if p.get(key) is not None]
        out[key] = sum(int(v) for v in vals) if vals else None
    return out


def _apply_counts_to_usage_block(
    block: dict,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    requests: int | None = None,
    cost_usd: float | None = None,
    source: str | None = None,
    notes: str | None = None,
    first_timestamp: str | None = None,
    last_timestamp: str | None = None,
    model_prefix: str | None = None,
) -> dict:
    """Merge non-None count fields into a usage / by_role block.

    @param block: mutable usage dict
    @returns: block
    """
    updates = {
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'total_tokens': total_tokens,
        'cache_read_tokens': cache_read_tokens,
        'cache_write_tokens': cache_write_tokens,
        'requests': requests,
        'cost_usd': cost_usd,
        'notes': notes,
        'first_timestamp': first_timestamp,
        'last_timestamp': last_timestamp,
        'model_prefix': model_prefix,
    }
    for key, value in updates.items():
        if value is not None:
            block[key] = value
    if (
        block.get('total_tokens') is None
        and block.get('input_tokens') is not None
        and block.get('output_tokens') is not None
    ):
        block['total_tokens'] = int(block['input_tokens']) + int(block['output_tokens'])
    if source is not None:
        block['source'] = source
    block['recorded_at'] = utc_now()
    return block


def require_bakeoff_active_world(root: Path) -> None:
    """Refuse CLI open/repair/baseline/capture unless the active world matches config ``world``.

    Library helpers such as ``open_round`` are not guarded (tests use temp roots).
    Does not restrict world repair tools outside this CLI.

    @param root: bake-offs root (config.json with a ``world`` key)
    @raises RuntimeError: when the active ``gameworld_id`` does not match
    """
    expected = load_config(root).get('world')
    if not expected:
        raise RuntimeError(
            'bakeoff/config.json missing "world" '
            f'(expected bakeoff); prefix every command with {BAKEOFF_WORLD_PREFIX}'
        )
    actual = active_world.gameworld_id()
    if actual != expected:
        raise RuntimeError(
            f'bake-off open/repair/baseline/capture requires world {expected!r} '
            f'(active is {actual!r}); prefix every command with {BAKEOFF_WORLD_PREFIX}'
        )


def round_shots_slug(round_id: str) -> str:
    """Filesystem slug for a round's shot folder (``R-013`` → ``r013``).

    @param round_id: round id
    @returns: lower-case slug without hyphens
    """
    return round_id.lower().replace('-', '')


def shots_rel_dir(world_id: str, round_id: str) -> str:
    """Repo-relative shot directory for a round.

    @param world_id: config ``world`` (e.g. ``bakeoff``)
    @param round_id: round id
    @returns: e.g. ``worlds/bakeoff/verification/site/r013-shots``
    """
    return f'worlds/{world_id}/verification/site/{round_shots_slug(round_id)}-shots'


def scale_aerial_crop(
    crop_pixels: list[int] | tuple[int, ...],
    capture_width: int,
    *,
    base_width: int = 1512,
) -> tuple[int, int, int, int]:
    """Scale reference aerial crop box to the actual capture width.

    @param crop_pixels: ``[left, top, right, bottom]`` at ``base_width``
    @param capture_width: actual ``aerial_top`` PNG width
    @param base_width: width the crop was authored against
    @returns: scaled integer crop box
    """
    if base_width <= 0:
        raise ValueError(f'base_width must be positive, got {base_width}')
    if len(crop_pixels) != 4:
        raise ValueError(f'aerial_crop_pixels must have 4 ints, got {crop_pixels!r}')
    scale = capture_width / base_width
    return tuple(round(float(q) * scale) for q in crop_pixels)  # type: ignore[return-value]


def photos_map_for_shots(
    shots_rel: str,
    *,
    camera_names: list[str] | tuple[str, ...] | None = None,
    portals_note: str = 'reference/portals/42-A..D.jpg',
) -> dict[str, str]:
    """Build the round ``photos`` map pointing at captured shot paths.

    @param shots_rel: repo-relative shots directory
    @param camera_names: camera keys (default square_south / street_front / aerial_top)
    @param portals_note: short note for portal reference photos
    @returns: photos dict suitable for the round JSON
    """
    names = list(camera_names or ('square_south', 'street_front', 'aerial_top'))
    photos: dict[str, str] = {name: f'{shots_rel}/{name}.png' for name in names}
    photos['real_refs_note'] = f'{portals_note}; real aerial crop {shots_rel}/real_aerial.png'
    return photos


def xyz_pixel_from_wgs84(
    lat: float,
    lon: float,
    *,
    zoom: int,
    origin_tx: float,
    origin_ty: float,
) -> tuple[float, float]:
    """Project WGS84 to mosaic-local pixel coords for an XYZ tile origin.

    Delegates slippy-map maths to ``world_frame.tile_float``.

    @param lat: latitude
    @param lon: longitude
    @param zoom: XYZ zoom level
    @param origin_tx: mosaic west edge tile X (float OK)
    @param origin_ty: mosaic north edge tile Y (float OK)
    @returns: ``(u, v)`` pixels in the mosaic image
    """
    from world_frame import WM_TILE_PX, tile_float  # noqa: WPS433

    tx, ty = tile_float(lat, lon, zoom)
    return (tx - origin_tx) * WM_TILE_PX, (ty - origin_ty) * WM_TILE_PX


def load_catalogue(root: Path) -> dict:
    """Load catalogue.json (tools and models).

    @param root: bake-offs root
    @returns: catalogue dict
    """
    return load_json(root / 'catalogue.json')


def save_catalogue(root: Path, catalogue: dict) -> None:
    """Write catalogue.json atomically.

    @param root: bake-offs root
    @param catalogue: catalogue dict
    """
    write_json(root / 'catalogue.json', catalogue)


def list_rounds(root: Path) -> list[dict]:
    """Load every round JSON under rounds/, sorted by id.

    @param root: bake-offs root
    @returns: list of round dicts
    """
    rounds_dir = root / 'rounds'
    if not rounds_dir.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(rounds_dir.glob('R-*.json')):
        out.append(load_json(path))
    return out


def round_path(root: Path, round_id: str) -> Path:
    """Path for a round id.

    @param root: bake-offs root
    @param round_id: e.g. R-001
    @returns: path
    """
    if not re.fullmatch(r'R-\d{3,}', round_id):
        raise ValueError(f'bad round id: {round_id}')
    return root / 'rounds' / f'{round_id}.json'


def load_round(root: Path, round_id: str) -> dict:
    """Load one round by id.

    @param root: bake-offs root
    @param round_id: e.g. R-001
    @returns: round dict
    """
    path = round_path(root, round_id)
    if not path.is_file():
        raise FileNotFoundError(f'no round {round_id}')
    return load_json(path)


def save_round(root: Path, round_: dict) -> Path:
    """Persist a round dict.

    @param root: bake-offs root
    @param round_: round record
    @returns: path written
    """
    path = round_path(root, round_['id'])
    write_json(path, round_)
    return path


def next_round_id(root: Path) -> str:
    """Allocate the next R-NNN id from existing files.

    @param root: bake-offs root
    @returns: new id
    """
    n = 0
    for r in list_rounds(root):
        m = re.fullmatch(r'R-(\d+)', r.get('id', ''))
        if m:
            n = max(n, int(m.group(1)))
    return f'R-{n + 1:03d}'


def find_model(catalogue: dict, tool_id: str, model_id: str) -> tuple[dict, dict]:
    """Find tool and model rows.

    @param catalogue: catalogue dict
    @param tool_id: tool id
    @param model_id: model id
    @returns: (tool, model)
    """
    for tool in catalogue.get('tools') or []:
        if tool.get('id') != tool_id:
            continue
        for model in tool.get('models') or []:
            if model.get('id') == model_id:
                return tool, model
        raise KeyError(f'unknown model {model_id!r} under tool {tool_id!r}')
    raise KeyError(f'unknown tool {tool_id!r}')


def bulk_models(catalogue: dict, active_tiers: list[str]) -> list[dict]:
    """Flat list of bulk-ok models in active tiers, low before mid.

    Each item: tool_id, tool_name, model_id, tier, label.

    @param catalogue: catalogue dict
    @param active_tiers: e.g. ['low', 'mid']
    @returns: suggestion candidates
    """
    tier_order = {t: i for i, t in enumerate(TIERS)}
    active = set(active_tiers)
    rows: list[dict] = []
    for tool in catalogue.get('tools') or []:
        for model in tool.get('models') or []:
            if not model.get('bulk_ok', True):
                continue
            tier = model.get('tier')
            if tier not in active:
                continue
            rows.append(
                {
                    'tool': tool['id'],
                    'tool_name': tool.get('name') or tool['id'],
                    'model': model['id'],
                    'tier': tier,
                    'label': model.get('label') or model['id'],
                }
            )
    rows.sort(key=lambda r: (tier_order.get(r['tier'], 99), r['tool'], r['model']))
    return rows


def cold_key(task: str, tool: str, model: str, pack: str) -> tuple:
    """Identity for a cold sitting slot.

    @returns: tuple key
    """
    return (task, tool, model, pack)


def scored_cold_rounds(rounds: list[dict], task: str, pack: str) -> dict[tuple, dict]:
    """Map cold_key → scored cold round.

    @param rounds: all rounds
    @param task: task id
    @param pack: pack version
    @returns: key → round
    """
    out: dict[tuple, dict] = {}
    for r in rounds:
        if r.get('task') != task or r.get('pack') != pack:
            continue
        if r.get('kind') != 'cold' or r.get('status') != 'scored':
            continue
        if is_hybrid_round(r):
            continue
        key = cold_key(task, r['tool']['id'], r['model']['id'], pack)
        out[key] = r
    return out


def mid_unlocked(rounds: list[dict], task: str, pack: str) -> bool:
    """True when any scored low cold round for the task is not great.

    @param rounds: all rounds
    @param task: task id
    @param pack: pack version
    @returns: whether mid tier sittings may be opened
    """
    for r in rounds:
        if r.get('task') != task or r.get('pack') != pack:
            continue
        if r.get('kind') != 'cold' or r.get('status') != 'scored':
            continue
        if is_hybrid_round(r):
            continue
        if r.get('tier') != 'low':
            continue
        if r.get('overall') != 'great':
            return True
    return False


def suggest_next(root: Path, task: str | None = None) -> dict | None:
    """Suggest the next model sitting to open (does not create a file).

    Prefers unfinished ``ready``/``in_progress`` rounds, then the next catalogue
    slot: all low bulk models first; mid only after a low cold score is not great.
    High / non-bulk models are never suggested.

    @param root: bake-offs root
    @param task: optional task filter (default pilot_task)
    @returns: suggestion dict or None if nothing left
    """
    cfg = load_config(root)
    catalogue = load_catalogue(root)
    task = task or cfg['pilot_task']
    pack = cfg['pack']
    rounds = list_rounds(root)

    for r in rounds:
        if r.get('task') != task:
            continue
        if r.get('status') in ('ready', 'in_progress'):
            return {
                'action': 'continue',
                'round_id': r['id'],
                'tool': r['tool']['id'],
                'model': r['model']['id'],
                'tier': r['tier'],
                'task': task,
                'attempt': r.get('attempt'),
                'kind': r.get('kind'),
            }

    scored = scored_cold_rounds(rounds, task, pack)
    allow_mid = mid_unlocked(rounds, task, pack)
    for row in bulk_models(catalogue, cfg['active_tiers']):
        if row['tier'] == 'mid' and not allow_mid:
            continue
        key = cold_key(task, row['tool'], row['model'], pack)
        if key in scored:
            continue
        # skip if an unscored cold already exists (handled above) or open pending
        existing = [
            r
            for r in rounds
            if r.get('task') == task
            and r.get('pack') == pack
            and r.get('kind') == 'cold'
            and not is_hybrid_round(r)
            and r.get('tool', {}).get('id') == row['tool']
            and r.get('model', {}).get('id') == row['model']
        ]
        if existing:
            continue
        return {
            'action': 'open',
            'tool': row['tool'],
            'model': row['model'],
            'tier': row['tier'],
            'label': row['label'],
            'tool_name': row['tool_name'],
            'task': task,
            'pack': pack,
        }
    return None


def interview_template(cfg: dict) -> dict:
    """Build the interview block embedded in each round.

    @param cfg: config
    @returns: interview dict
    """
    axes = {name: {'grade': None, 'note': None} for name in cfg['axes']}
    return {
        'axes': axes,
        'why': None,
        'repair_brief_for_next': None,
        'questions': [
            'massing: shape/roof vs real refs',
            'openings: doors/windows at play distance',
            'roof_greeblies: street-scale roof bits or none visible',
            'materials: Minecraft-readable facade',
            'keep: keep / keep-with-follow-up / tear down',
            'why (if not great): what is wrong vs refs; repair brief?',
        ],
    }


def usage_template() -> dict:
    """Empty token/cost block for a new round.

    Prefer Claude Code / API transcript sums (``source=api``) plus weekly
    ``plan_pct_start`` / ``plan_pct_end`` for cross-model cost comparison.
    Cursor UI paste remains fine when that is all you have.

    @returns: usage dict with null counts
    """
    return {
        'input_tokens': None,
        'output_tokens': None,
        'total_tokens': None,
        'cache_read_tokens': None,
        'cache_write_tokens': None,
        'requests': None,
        'cost_usd': None,
        'plan_pct_start': None,
        'plan_pct_end': None,
        'wallclock_min': None,
        'interruptions': None,
        'source': None,
        'recorded_at': None,
        'notes': None,
        'first_timestamp': None,
        'last_timestamp': None,
    }


def _validate_plan_pct(value: int | None, *, label: str = 'plan_pct') -> int | None:
    """Require a weekly plan percentage in 0..100, or None.

    @param value: percentage or None
    @param label: field name for errors
    @returns: value unchanged when valid
    """
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > 100:
        raise ValueError(f'{label} must be an int 0..100, got {value!r}')
    return value


def _transcript_paths(path: Path) -> list[Path]:
    """Resolve a transcript path to one or more Claude Code JSONL files.

    A session file ``<id>.jsonl`` also pulls in ``<id>/subagents/*.jsonl`` when
    present. A directory yields every ``*.jsonl`` under it (recursive).

    @param path: session jsonl, subagent jsonl, or directory
    @returns: existing jsonl paths (deduped, sorted)
    """
    path = Path(path).expanduser().resolve()
    found: list[Path] = []
    if path.is_dir():
        found.extend(sorted(path.rglob('*.jsonl')))
    elif path.is_file():
        found.append(path)
        if path.suffix == '.jsonl':
            sub_dir = path.with_suffix('') / 'subagents'
            if sub_dir.is_dir():
                found.extend(sorted(sub_dir.glob('*.jsonl')))
    else:
        raise FileNotFoundError(f'transcript not found: {path}')
    # Preserve order but drop duplicates
    seen: set[Path] = set()
    out: list[Path] = []
    for p in found:
        if p not in seen:
            seen.add(p)
            out.append(p)
    if not out:
        raise FileNotFoundError(f'no .jsonl under {path}')
    return out


def sum_transcript_usage(
    path: Path | str,
    *,
    model_prefix: str | None = None,
) -> dict[str, Any]:
    """Sum per-response API usage from a Claude Code transcript (JSONL).

    De-duplicates by ``message.id`` (Claude Code often repeats the same
    assistant turn on multiple lines). Optional ``model_prefix`` keeps only
    models whose id starts with that string (e.g. ``claude-opus-5-5``).
    ``total_tokens`` = input + output + cache_read + cache_write.

    @param path: session ``.jsonl``, subagent ``.jsonl``, or a directory of them
    @param model_prefix: optional model id prefix filter
    @returns: counts plus first/last timestamps (ISO strings from the file)
    """
    by_id: dict[str, dict[str, Any]] = {}
    for jsonl in _transcript_paths(Path(path)):
        with jsonl.open(encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get('type') != 'assistant':
                    continue
                msg = obj.get('message') or {}
                if not isinstance(msg, dict):
                    continue
                model = msg.get('model') or ''
                if model_prefix and not str(model).startswith(model_prefix):
                    continue
                mid = msg.get('id')
                usage = msg.get('usage')
                if not mid or not isinstance(usage, dict):
                    continue
                # First sighting wins (same id → same usage; later lines are echoes)
                if mid in by_id:
                    continue
                by_id[mid] = {
                    'usage': usage,
                    'timestamp': obj.get('timestamp'),
                    'model': model,
                }

    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_write_tokens = 0
    timestamps: list[str] = []
    for row in by_id.values():
        u = row['usage']
        input_tokens += int(u.get('input_tokens') or 0)
        output_tokens += int(u.get('output_tokens') or 0)
        cache_read_tokens += int(
            u.get('cache_read_input_tokens')
            if u.get('cache_read_input_tokens') is not None
            else u.get('cache_read_tokens') or 0
        )
        cache_write_tokens += int(
            u.get('cache_creation_input_tokens')
            if u.get('cache_creation_input_tokens') is not None
            else u.get('cache_write_tokens') or 0
        )
        ts = row.get('timestamp')
        if isinstance(ts, str) and ts:
            timestamps.append(ts)
    timestamps.sort()
    total = input_tokens + output_tokens + cache_read_tokens + cache_write_tokens
    return {
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'cache_read_tokens': cache_read_tokens,
        'cache_write_tokens': cache_write_tokens,
        'total_tokens': total,
        'requests': len(by_id),
        'first_timestamp': timestamps[0] if timestamps else None,
        'last_timestamp': timestamps[-1] if timestamps else None,
        'model_prefix': model_prefix,
    }


def open_round(
    root: Path,
    *,
    tool: str,
    model: str,
    task: str | None = None,
    kind: str = 'cold',
    attempt: int = 1,
    parent_round: str | None = None,
    repair_brief: str | None = None,
    force_mid: bool = False,
    plan_pct: int | None = None,
    build_scripts: list[Path | str] | None = None,
    designer: str | None = None,
    handoff: Path | str | None = None,
) -> dict:
    """Create a self-contained round record for one sitting.

    ``model`` is always the **builder** (apply owner). Pass ``designer`` to name
    the design model. When designer differs from builder, this is a **hybrid**
    sitting (designer writes the evidence table / build spec; builder applies;
    excluded from the single-model ladder). When designer equals builder, both
    roles are recorded but the sitting is single-model (one session). Hybrid
    designer/builder pairs must have distinct transcript model prefixes
    (catalogue effort suffixes stripped) so usage auto-split can tell them apart.

    @param root: bake-offs root
    @param tool: tool id
    @param model: builder model id (apply owner)
    @param task: task id (default pilot)
    @param kind: cold | repair
    @param attempt: 1..max_attempts
    @param parent_round: parent id for repairs
    @param repair_brief: repair text for kind=repair
    @param force_mid: allow mid cold open before low unlocks (escape hatch)
    @param plan_pct: optional weekly plan % at sitting start (all models)
    @param build_scripts: optional source paths remembered for score-time copy
    @param designer: optional designer model id (defaults via CLI ``build_roles``)
    @param handoff: optional designer artefact path (spec/script) recorded now
    @returns: new round dict (also written to disk)
    """
    cfg = load_config(root)
    catalogue = load_catalogue(root)
    task = task or cfg['pilot_task']
    tool_row, model_row = find_model(catalogue, tool, model)
    validate_catalogue_model(model_row, role='builder')
    if model_row.get('tier') not in cfg['active_tiers']:
        raise ValueError(
            f"builder model tier {model_row.get('tier')!r} not in active_tiers "
            f"{cfg['active_tiers']}"
        )

    designer_row = None
    split_sessions = False
    if designer is not None:
        _tool_d, designer_row = find_model(catalogue, tool, designer)
        validate_catalogue_model(designer_row, role='designer')
        if designer_row.get('tier') not in cfg['active_tiers']:
            raise ValueError(
                f"designer model tier {designer_row.get('tier')!r} not in "
                f"active_tiers {cfg['active_tiers']}"
            )
        split_sessions = designer_row['id'] != model_row['id']
        if split_sessions:
            designer_prefix = transcript_model_prefix(designer_row['id'])
            builder_prefix = transcript_model_prefix(model_row['id'])
            if designer_prefix == builder_prefix:
                raise ValueError(
                    'hybrid designer/builder share the same transcript model '
                    f'prefix {designer_prefix!r} after stripping effort suffixes '
                    f'({designer_row["id"]!r} / {model_row["id"]!r}); pick models '
                    'from different families so usage auto-split can tell them '
                    'apart, or record usage with --role'
                )

    # Mid unlock applies to the builder (apply owner), not the designer.
    if model_row.get('tier') == 'mid' and kind == 'cold' and not force_mid:
        if not mid_unlocked(list_rounds(root), task, cfg['pack']):
            raise ValueError(
                'mid tier locked until a low cold round scores fail or pass '
                '(not great). Score low sittings first.'
            )

    round_id = next_round_id(root)
    pack = cfg['pack']
    usage = usage_template()
    plan_pct = _validate_plan_pct(plan_pct, label='plan_pct_start')
    if plan_pct is not None:
        usage['plan_pct_start'] = plan_pct
    round_type = 'hybrid' if split_sessions else 'single'
    if round_type == 'hybrid':
        usage['by_role'] = {
            'designer': usage_role_template(),
            'builder': usage_role_template(),
        }
    round_: dict[str, Any] = {
        'id': round_id,
        'status': 'ready',
        'created': utc_now(),
        'scored_at': None,
        'task': task,
        'pack': pack,
        'pack_brief': f'packs/{pack}/BRIEF.md',
        'round_type': round_type,
        'tier': model_row['tier'],
        'tool': {'id': tool_row['id'], 'name': tool_row.get('name') or tool_row['id']},
        'model': model_ref(model_row),
        'cell': list(cfg['cell']),
        'cell_note': cfg.get('cell_note'),
        'ghost_cells': [list(c) for c in cfg.get('ghost_cells') or []],
        'attempt': attempt,
        'kind': kind,
        'parent_round': parent_round,
        'repair_brief': repair_brief,
        'mech_gate': None,
        'interview': interview_template(cfg),
        'overall': None,
        'critical_axes': list(cfg.get('critical_axes') or []),
        'photos': {
            'square_south': None,
            'street_front': None,
            'aerial_top': None,
            'real_refs_note': None,
        },
        'usage': usage,
        'sitting': {
            'checklist': sitting_checklist(
                cfg,
                tool=tool_row['id'],
                model=model_row['id'],
                pack=pack,
                round_id=round_id,
                designer=designer_row['id'] if split_sessions else None,
            ),
        },
        'notes': 'opened with --force-mid' if force_mid else '',
        'force_mid': bool(force_mid) or None,
    }
    if designer_row is not None:
        round_['designer'] = model_ref(designer_row)
        if split_sessions:
            round_['handoff'] = handoff_template()
    if round_['force_mid'] is None:
        del round_['force_mid']
    stored = [str(p) for p in _normalise_build_script_paths(build_scripts)]
    if stored:
        round_['build_scripts'] = stored
    if handoff is not None:
        if not split_sessions:
            raise ValueError(
                '--handoff requires a hybrid round (designer distinct from builder)'
            )
        set_handoff(round_, handoff)
    save_round(root, round_)
    return round_


def overall_from_axes(axes: dict[str, str], critical: list[str]) -> str:
    """Worst grade among critical axes (fail < pass < great).

    @param axes: axis → grade
    @param critical: axis names that determine overall
    @returns: overall grade
    """
    ranks = []
    for name in critical:
        g = axes.get(name)
        if g not in GRADE_RANK:
            raise ValueError(f'missing/invalid grade for critical axis {name!r}: {g!r}')
        ranks.append(GRADE_RANK[g])
    worst = min(ranks)
    for grade, rank in GRADE_RANK.items():
        if rank == worst:
            return grade
    raise RuntimeError('unreachable')



ARTEFACT_TEXT_SUFFIXES = ('.py', '.json')
ARTEFACT_SKIP_SUFFIXES = {
    '.npz',
    '.png',
    '.jpg',
    '.jpeg',
    '.webp',
    '.gif',
    '.tif',
    '.tiff',
}


def round_artefacts_dir(root: Path, round_id: str) -> Path:
    """Directory for a round's copied build scripts and capture metadata.

    Lives beside ``R-NNN.json`` as ``rounds/R-NNN/`` (same pattern as the older
    ``R-008-photos`` folders).

    @param root: bake-offs root
    @param round_id: e.g. R-013
    @returns: absolute artefacts directory
    """
    return root / 'rounds' / round_id


def artefacts_rel_path(round_id: str, filename: str) -> str:
    """Repo-relative path under ``bakeoff/rounds/R-NNN/``.

    @param round_id: round id
    @param filename: basename inside the artefacts dir
    @returns: e.g. ``bakeoff/rounds/R-013/r013_susu.py``
    """
    return f'bakeoff/rounds/{round_id}/{filename}'


def feature_plans_for_round(entries: list[Any], round_id: str) -> list[str]:
    """Plan names from feature builds tagged with ``meta.round == round_id``.

    Dedupes to the latest journal seq per plan.

    @param entries: journal EntryView-like objects
    @param round_id: round id stored on apply (e.g. R-013)
    @returns: plan names, newest first
    """
    latest: dict[str, int] = {}
    for e in entries:
        if getattr(e, 'kind', None) != 'build':
            continue
        if getattr(e, 'pass_', None) != 'feature':
            continue
        meta = getattr(e, 'meta', None) or {}
        if meta.get('round') != round_id:
            continue
        plan = getattr(e, 'plan', None)
        if not plan:
            continue
        seq = int(e.seq)
        if plan not in latest or seq > latest[plan]:
            latest[plan] = seq
    return [
        plan
        for plan, _seq in sorted(latest.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def resolve_plan_build_files(world_root: Path, plan: str) -> list[Path]:
    """Locate text build sources for a journal plan under ``site/builds/``.

    Prefers ``.py`` then ``.json``. Skips large binaries (``.npz``) and images.

    @param world_root: active world checkout
    @param plan: journal plan name (e.g. ``r013_susu``)
    @returns: existing source files to copy
    """
    builds = Path(world_root) / 'site' / 'builds'
    out: list[Path] = []
    for suf in ARTEFACT_TEXT_SUFFIXES:
        candidate = builds / f'{plan}{suf}'
        if candidate.is_file() and candidate.suffix.lower() not in ARTEFACT_SKIP_SUFFIXES:
            out.append(candidate)
    return out


def _normalise_build_script_paths(paths: list[Path | str] | None) -> list[Path]:
    """Turn CLI / round-stored script paths into Path objects.

    @param paths: optional list of paths
    @returns: Path list (empty when none)
    """
    out: list[Path] = []
    for raw in paths or []:
        p = Path(raw)
        if p.suffix.lower() in ARTEFACT_SKIP_SUFFIXES:
            continue
        out.append(p)
    return out


def collect_build_script_sources(
    root: Path,
    round_: dict,
    *,
    build_scripts: list[Path | str] | None = None,
    world_root: Path | None = None,
    list_entries_fn: Callable[[list[list[int]]], list[Any]] | None = None,
) -> list[Path]:
    """Resolve build-script sources for a round (flag → stored → journal plan).

    Order of preference:

    1. Explicit ``build_scripts`` (score / open flag).
    2. Paths already stored on the round from ``open --build-script``.
    3. Journal feature plans with ``meta.round`` matching this round, resolved
       under ``<world>/site/builds/<plan>.{py,json}``.

    @param root: bake-offs root (unused; kept for call-site symmetry)
    @param round_: round record
    @param build_scripts: explicit source paths
    @param world_root: world checkout for journal / plan resolution
    @param list_entries_fn: injectable journal listing (tests)
    @returns: absolute-or-as-given source paths to copy (deduped by resolve)
    """
    del root  # call-site symmetry with copy_round_artefacts
    explicit = _normalise_build_script_paths(build_scripts)
    if explicit:
        return explicit

    stored = _normalise_build_script_paths(round_.get('build_scripts'))
    if stored:
        return stored

    wroot = Path(world_root) if world_root is not None else None
    if wroot is None:
        try:
            wroot = Path(active_world.WORLD_ROOT)
        except Exception:
            return []
    ghost_cells = [list(c) for c in (round_.get('ghost_cells') or [])]
    if not ghost_cells:
        return []
    list_entries = list_entries_fn or _default_list_feature_entries
    try:
        entries = list_entries(ghost_cells)
    except Exception:
        return []
    sources: list[Path] = []
    seen: set[Path] = set()
    for plan in feature_plans_for_round(entries, round_['id']):
        for src in resolve_plan_build_files(wroot, plan):
            key = src.resolve() if src.exists() else src
            if key in seen:
                continue
            seen.add(key)
            sources.append(src)
    return sources


def copy_round_artefacts(
    root: Path,
    round_: dict,
    *,
    build_scripts: list[Path | str] | None = None,
    world_root: Path | None = None,
    list_entries_fn: Callable[[list[list[int]]], list[Any]] | None = None,
) -> dict[str, Any]:
    """Copy build scripts + capture metadata into ``rounds/R-NNN/``.

    Skips large binaries (``.npz``) and screenshots. Capture metadata is
    ``cameras.json`` from the round's shots directory when present.

    @param root: bake-offs root
    @param round_: round record (must include ``id``)
    @param build_scripts: optional explicit source paths
    @param world_root: world checkout (journal + cameras.json)
    @param list_entries_fn: injectable journal listing (tests)
    @returns: ``artefacts`` map for the round JSON (repo-relative paths)
    """
    round_id = round_['id']
    sources = collect_build_script_sources(
        root,
        round_,
        build_scripts=build_scripts,
        world_root=world_root,
        list_entries_fn=list_entries_fn,
    )

    wroot = Path(world_root) if world_root is not None else None
    if wroot is None:
        try:
            wroot = Path(active_world.WORLD_ROOT)
        except Exception:
            wroot = None
    cameras_src: Path | None = None
    if wroot is not None:
        candidate = (
            wroot
            / 'verification'
            / 'site'
            / f'{round_shots_slug(round_id)}-shots'
            / 'cameras.json'
        )
        if candidate.is_file():
            cameras_src = candidate

    if not sources and cameras_src is None:
        return {'build_scripts': []}

    dest_dir = round_artefacts_dir(root, round_id)
    dest_dir.mkdir(parents=True, exist_ok=True)

    copied_scripts: list[str] = []
    for src in sources:
        src_path = Path(src)
        if not src_path.is_file():
            raise FileNotFoundError(f'build script not found: {src_path}')
        if src_path.suffix.lower() in ARTEFACT_SKIP_SUFFIXES:
            continue
        dest = dest_dir / src_path.name
        shutil.copy2(src_path, dest)
        copied_scripts.append(artefacts_rel_path(round_id, dest.name))

    artefacts: dict[str, Any] = {'build_scripts': copied_scripts}
    if cameras_src is not None:
        cameras_dest = dest_dir / 'cameras.json'
        shutil.copy2(cameras_src, cameras_dest)
        artefacts['cameras'] = artefacts_rel_path(round_id, 'cameras.json')

    return artefacts


def score_round(
    root: Path,
    round_id: str,
    *,
    overall: str | None = None,
    axes: dict[str, str] | None = None,
    mech_gate: str = 'pass',
    why: str | None = None,
    notes: str | None = None,
    axis_notes: dict[str, str] | None = None,
    plan_pct: int | None = None,
    wallclock_min: int | None = None,
    interruptions: str | None = None,
    build_scripts: list[Path | str] | None = None,
    handoff: Path | str | None = None,
    world_root: Path | None = None,
    list_entries_fn: Callable[[list[list[int]]], list[Any]] | None = None,
) -> dict:
    """Record interview grades on a round.

    Also copies the round's build script(s) and capture metadata into
    ``bakeoff/rounds/R-NNN/`` and records repo-relative paths under
    ``artefacts``. Script paths come from ``build_scripts``, paths stored at
    open, the hybrid ``handoff`` path, or the journal plan name tagged with
    this round id.

    @param root: bake-offs root
    @param round_id: round id
    @param overall: optional override; else derived from critical axes
    @param axes: axis → grade
    @param mech_gate: pass | fail
    @param why: why-not-great text
    @param notes: free notes
    @param axis_notes: optional per-axis notes
    @param plan_pct: optional weekly plan % at sitting end (all models)
    @param wallclock_min: optional active wall-clock minutes
    @param interruptions: optional interruption notes for the usage block
    @param build_scripts: optional source paths to copy (overrides stored/journal)
    @param handoff: optional designer hand-off artefact path (hybrid rounds)
    @param world_root: world checkout for journal / cameras.json resolution
    @param list_entries_fn: injectable journal listing (tests)
    @returns: updated round
    """
    cfg = load_config(root)
    round_ = load_round(root, round_id)
    if mech_gate not in ('pass', 'fail'):
        raise ValueError('mech_gate must be pass or fail')
    axes = dict(axes or {})
    axis_notes = dict(axis_notes or {})

    if mech_gate == 'fail':
        for name in cfg['axes']:
            axes.setdefault(name, 'fail')
        overall = 'fail'

    for name, grade in axes.items():
        if name not in round_['interview']['axes']:
            raise ValueError(f'unknown axis {name!r}')
        if grade not in GRADE_RANK:
            raise ValueError(f'bad grade {grade!r} for {name}')
        round_['interview']['axes'][name]['grade'] = grade
        if name in axis_notes:
            round_['interview']['axes'][name]['note'] = axis_notes[name]

    if overall is None:
        critical = round_.get('critical_axes') or cfg['critical_axes']
        crit_grades = {n: axes[n] for n in critical if n in axes}
        if len(crit_grades) < len(critical):
            missing = set(critical) - set(crit_grades)
            raise ValueError(f'need grades for critical axes: {sorted(missing)}')
        overall = overall_from_axes(crit_grades, critical)
    if overall not in GRADE_RANK:
        raise ValueError(f'bad overall {overall!r}')

    round_['mech_gate'] = mech_gate
    round_['overall'] = overall
    round_['interview']['why'] = why
    if notes is not None:
        round_['notes'] = notes

    plan_pct = _validate_plan_pct(plan_pct, label='plan_pct_end')
    if wallclock_min is not None and (
        not isinstance(wallclock_min, int)
        or isinstance(wallclock_min, bool)
        or wallclock_min < 0
    ):
        raise ValueError(f'wallclock_min must be a non-negative int, got {wallclock_min!r}')
    if plan_pct is not None or wallclock_min is not None or interruptions is not None:
        usage = dict(round_.get('usage') or usage_template())
        if plan_pct is not None:
            usage['plan_pct_end'] = plan_pct
        if wallclock_min is not None:
            usage['wallclock_min'] = wallclock_min
        if interruptions is not None:
            usage['interruptions'] = interruptions
        round_['usage'] = usage

    round_['status'] = 'scored'
    round_['scored_at'] = utc_now()
    if handoff is not None:
        if not is_hybrid_round(round_):
            raise ValueError('handoff requires a hybrid round (--designer at open)')
        set_handoff(round_, handoff)
    if build_scripts:
        round_['build_scripts'] = [
            str(p) for p in _normalise_build_script_paths(build_scripts)
        ]
    round_['artefacts'] = copy_round_artefacts(
        root,
        round_,
        build_scripts=build_scripts,
        world_root=world_root,
        list_entries_fn=list_entries_fn,
    )
    save_round(root, round_)
    return round_


def record_usage(
    root: Path,
    round_id: str,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    requests: int | None = None,
    cost_usd: float | None = None,
    source: str = 'unknown',
    notes: str | None = None,
    plan_pct_start: int | None = None,
    plan_pct_end: int | None = None,
    wallclock_min: int | None = None,
    interruptions: str | None = None,
    first_timestamp: str | None = None,
    last_timestamp: str | None = None,
    from_transcript: Path | str | None = None,
    model_prefix: str | None = None,
    role: str | None = None,
) -> dict:
    """Attach token/cost usage to a round (merge; does not clear omitted fields).

    Prefer ``--from-transcript`` (sets ``source=api``) or the tool usage UI
    (`source=cursor-ui`). Leave nulls rather than inventing counts.

    Hybrid rounds: omit ``role`` with ``from_transcript`` to auto-split into
    ``usage.by_role.designer`` / ``usage.by_role.builder`` using transcript
    prefixes derived from catalogue ids (effort suffixes stripped) and set
    top-level totals to the sum. Raises if a role matches 0 requests.
    Pass ``role=designer|builder`` to record one side (optionally with
    ``model_prefix``). Weekly ``plan_pct_*`` stays on the top-level usage
    block (shared meter).

    @param root: bake-offs root
    @param round_id: round id
    @param input_tokens: prompt / input tokens
    @param output_tokens: completion / output tokens
    @param total_tokens: total when the UI only shows one number
    @param cache_read_tokens: optional cache read tokens
    @param cache_write_tokens: optional cache write tokens
    @param requests: optional billable request count
    @param cost_usd: optional USD cost
    @param source: where the numbers came from (see USAGE_SOURCES); ignored when
        ``from_transcript`` is set (forced to ``api``)
    @param notes: free text (e.g. parent+subagent split)
    @param plan_pct_start: weekly plan % at sitting start
    @param plan_pct_end: weekly plan % at sitting end
    @param wallclock_min: active wall-clock minutes
    @param interruptions: interruption notes
    @param first_timestamp: first API response timestamp (from transcript)
    @param last_timestamp: last API response timestamp (from transcript)
    @param from_transcript: Claude Code session/subagent jsonl (or directory)
    @param model_prefix: filter transcript models (e.g. ``claude-opus-5-5``)
    @param role: ``designer`` / ``builder`` for hybrid per-model usage
    @returns: updated round
    """
    if role is not None and role not in USAGE_ROLES:
        raise ValueError(f'role must be one of {USAGE_ROLES}, got {role!r}')

    round_ = load_round(root, round_id)
    hybrid = is_hybrid_round(round_)
    if role is not None and not hybrid:
        raise ValueError('role= requires a hybrid round (--designer at open)')

    # Auto-split hybrid transcript into by_role when no explicit role/prefix.
    if (
        from_transcript is not None
        and hybrid
        and role is None
        and model_prefix is None
    ):
        designer_id = (round_.get('designer') or {}).get('id')
        builder_id = (round_.get('model') or {}).get('id')
        if not designer_id or not builder_id:
            raise ValueError('hybrid round missing designer/model ids')
        usage = dict(round_.get('usage') or usage_template())
        by_role = dict(usage.get('by_role') or {})
        role_notes = []
        for role_name, catalogue_id in (
            ('designer', designer_id),
            ('builder', builder_id),
        ):
            prefix = transcript_model_prefix(catalogue_id)
            summed = sum_transcript_usage(from_transcript, model_prefix=prefix)
            if summed['requests'] == 0:
                raise ValueError(
                    f'hybrid usage auto-split matched 0 {role_name} requests '
                    f'for transcript prefix {prefix!r} (catalogue id '
                    f'{catalogue_id!r}); check the transcript path or pass '
                    f'--role {role_name} with an explicit --model-prefix'
                )
            block = dict(by_role.get(role_name) or usage_role_template())
            _apply_counts_to_usage_block(
                block,
                input_tokens=summed['input_tokens'],
                output_tokens=summed['output_tokens'],
                total_tokens=summed['total_tokens'],
                cache_read_tokens=summed['cache_read_tokens'],
                cache_write_tokens=summed['cache_write_tokens'],
                requests=summed['requests'],
                source='api',
                first_timestamp=summed['first_timestamp'],
                last_timestamp=summed['last_timestamp'],
                model_prefix=prefix,
                notes=(
                    f'Summed {role_name} API usage from transcript '
                    f'({summed["requests"]} unique {prefix} responses). '
                    f'total = input+output+cache read+cache write.'
                ),
            )
            by_role[role_name] = block
            role_notes.append(f'{role_name}={summed["total_tokens"]}')
        usage['by_role'] = by_role
        totals = _sum_usage_dicts([by_role['designer'], by_role['builder']])
        _apply_counts_to_usage_block(
            usage,
            input_tokens=totals['input_tokens'],
            output_tokens=totals['output_tokens'],
            total_tokens=totals['total_tokens'],
            cache_read_tokens=totals['cache_read_tokens'],
            cache_write_tokens=totals['cache_write_tokens'],
            requests=totals['requests'],
            source='api',
            notes=(
                'Hybrid split from transcript: '
                + ', '.join(role_notes)
                + '. total = designer+builder (input+output+cache).'
            ),
            first_timestamp=min(
                t
                for t in (
                    by_role['designer'].get('first_timestamp'),
                    by_role['builder'].get('first_timestamp'),
                )
                if t
            )
            if any(
                by_role[r].get('first_timestamp') for r in USAGE_ROLES
            )
            else None,
            last_timestamp=max(
                t
                for t in (
                    by_role['designer'].get('last_timestamp'),
                    by_role['builder'].get('last_timestamp'),
                )
                if t
            )
            if any(by_role[r].get('last_timestamp') for r in USAGE_ROLES)
            else None,
        )
        plan_pct_start = _validate_plan_pct(plan_pct_start, label='plan_pct_start')
        plan_pct_end = _validate_plan_pct(plan_pct_end, label='plan_pct_end')
        if plan_pct_start is not None:
            usage['plan_pct_start'] = plan_pct_start
        if plan_pct_end is not None:
            usage['plan_pct_end'] = plan_pct_end
        if wallclock_min is not None:
            if (
                not isinstance(wallclock_min, int)
                or isinstance(wallclock_min, bool)
                or wallclock_min < 0
            ):
                raise ValueError(
                    f'wallclock_min must be a non-negative int, got {wallclock_min!r}'
                )
            usage['wallclock_min'] = wallclock_min
        if interruptions is not None:
            usage['interruptions'] = interruptions
        round_['usage'] = usage
        save_round(root, round_)
        return round_

    if from_transcript is not None:
        if role is not None and model_prefix is None:
            catalogue_id = (
                (round_.get('designer') or {}).get('id')
                if role == 'designer'
                else (round_.get('model') or {}).get('id')
            )
            if not catalogue_id:
                raise ValueError(f'hybrid round missing {role} model id')
            model_prefix = transcript_model_prefix(catalogue_id)
        summed = sum_transcript_usage(from_transcript, model_prefix=model_prefix)
        input_tokens = summed['input_tokens']
        output_tokens = summed['output_tokens']
        cache_read_tokens = summed['cache_read_tokens']
        cache_write_tokens = summed['cache_write_tokens']
        total_tokens = summed['total_tokens']
        requests = summed['requests']
        first_timestamp = summed['first_timestamp']
        last_timestamp = summed['last_timestamp']
        source = 'api'
        if notes is None:
            prefix_bit = model_prefix or 'all models'
            span = ''
            if first_timestamp and last_timestamp:
                span = f', {first_timestamp}–{last_timestamp}'
            role_bit = f'{role} ' if role else ''
            notes = (
                f'Summed per-request API usage from transcript '
                f'({requests} unique {role_bit}{prefix_bit} responses{span}). '
                f'total = input+output+cache read+cache write.'
            )

    if source not in USAGE_SOURCES:
        raise ValueError(f'source must be one of {USAGE_SOURCES}, got {source!r}')
    for label, value in (
        ('input_tokens', input_tokens),
        ('output_tokens', output_tokens),
        ('total_tokens', total_tokens),
        ('cache_read_tokens', cache_read_tokens),
        ('cache_write_tokens', cache_write_tokens),
        ('requests', requests),
        ('wallclock_min', wallclock_min),
    ):
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise ValueError(f'{label} must be a non-negative int, got {value!r}')
    if cost_usd is not None and cost_usd < 0:
        raise ValueError(f'cost_usd must be >= 0, got {cost_usd!r}')
    plan_pct_start = _validate_plan_pct(plan_pct_start, label='plan_pct_start')
    plan_pct_end = _validate_plan_pct(plan_pct_end, label='plan_pct_end')

    usage = dict(round_.get('usage') or usage_template())
    if role is not None:
        by_role = dict(usage.get('by_role') or {})
        block = dict(by_role.get(role) or usage_role_template())
        _apply_counts_to_usage_block(
            block,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            requests=requests,
            cost_usd=cost_usd,
            source=source,
            notes=notes,
            first_timestamp=first_timestamp,
            last_timestamp=last_timestamp,
            model_prefix=model_prefix,
        )
        by_role[role] = block
        # Ensure the other role key exists for hybrid.
        other = 'builder' if role == 'designer' else 'designer'
        by_role.setdefault(other, usage_role_template())
        usage['by_role'] = by_role
        totals = _sum_usage_dicts(
            [by_role.get('designer') or {}, by_role.get('builder') or {}]
        )
        _apply_counts_to_usage_block(
            usage,
            input_tokens=totals['input_tokens'],
            output_tokens=totals['output_tokens'],
            total_tokens=totals['total_tokens'],
            cache_read_tokens=totals['cache_read_tokens'],
            cache_write_tokens=totals['cache_write_tokens'],
            requests=totals['requests'],
            source=source,
        )
    else:
        _apply_counts_to_usage_block(
            usage,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            requests=requests,
            cost_usd=cost_usd,
            source=source,
            notes=notes,
            first_timestamp=first_timestamp,
            last_timestamp=last_timestamp,
            model_prefix=model_prefix,
        )

    if plan_pct_start is not None:
        usage['plan_pct_start'] = plan_pct_start
    if plan_pct_end is not None:
        usage['plan_pct_end'] = plan_pct_end
    if wallclock_min is not None:
        usage['wallclock_min'] = wallclock_min
    if interruptions is not None:
        usage['interruptions'] = interruptions
    # Top-level source for non-role path already set; for role path keep api/source.
    if role is None or from_transcript is not None:
        usage['source'] = source
    elif usage.get('source') is None:
        usage['source'] = source
    usage['recorded_at'] = utc_now()
    round_['usage'] = usage
    save_round(root, round_)
    return round_


def plan_pct_delta_bit(usage: dict) -> str:
    """Format plan % start→end (and delta) for status lines when both set.

    @param usage: round usage block
    @returns: short suffix (may be empty)
    """
    start = usage.get('plan_pct_start')
    end = usage.get('plan_pct_end')
    if start is not None and end is not None:
        delta = int(end) - int(start)
        sign = '+' if delta > 0 else ''
        return f' plan={start}%→{end}% (Δ{sign}{delta})'
    if start is not None:
        return f' plan_start={start}%'
    if end is not None:
        return f' plan_end={end}%'
    return ''


def open_repair(root: Path, parent_id: str, brief: str) -> dict:
    """Open attempt N+1 repair round from a scored parent.

    @param root: bake-offs root
    @param parent_id: parent round id
    @param brief: repair brief
    @returns: new round
    """
    cfg = load_config(root)
    parent = load_round(root, parent_id)
    if parent.get('status') != 'scored':
        raise ValueError('parent must be scored before repair')
    if parent.get('overall') == 'great':
        raise ValueError('no repair needed when overall is great')
    attempt = int(parent.get('attempt') or 1) + 1
    if attempt > int(cfg.get('max_attempts') or 3):
        raise ValueError(f'max_attempts ({cfg["max_attempts"]}) reached')
    brief = (brief or '').strip()
    if not brief:
        raise ValueError('repair brief required')
    parent['interview']['repair_brief_for_next'] = brief
    save_round(root, parent)
    return open_round(
        root,
        tool=parent['tool']['id'],
        model=parent['model']['id'],
        task=parent['task'],
        kind='repair',
        attempt=attempt,
        parent_round=parent_id,
        repair_brief=brief,
    )


def add_tool(root: Path, tool_id: str, name: str) -> dict:
    """Add an empty tool to the catalogue.

    @param root: bake-offs root
    @param tool_id: id slug
    @param name: display name
    @returns: updated catalogue
    """
    cat = load_catalogue(root)
    if any(t.get('id') == tool_id for t in cat.get('tools') or []):
        raise ValueError(f'tool {tool_id!r} already exists')
    cat.setdefault('tools', []).append({'id': tool_id, 'name': name, 'models': []})
    save_catalogue(root, cat)
    return cat


def add_model(
    root: Path,
    *,
    tool: str,
    model_id: str,
    tier: str,
    label: str,
    bulk_ok: bool = True,
) -> dict:
    """Add a model under a tool.

    @param root: bake-offs root
    @param tool: tool id
    @param model_id: model id
    @param tier: low | mid | high
    @param label: display label
    @param bulk_ok: false for non-bulk / too dear
    @returns: updated catalogue
    """
    if tier not in TIERS:
        raise ValueError(f'tier must be one of {TIERS}')
    cat = load_catalogue(root)
    for tool_row in cat.get('tools') or []:
        if tool_row.get('id') != tool:
            continue
        if any(m.get('id') == model_id for m in tool_row.get('models') or []):
            raise ValueError(f'model {model_id!r} already on {tool}')
        tool_row.setdefault('models', []).append(
            {
                'id': model_id,
                'tier': tier,
                'label': label,
                'bulk_ok': bool(bulk_ok),
            }
        )
        save_catalogue(root, cat)
        return cat
    raise KeyError(f'unknown tool {tool!r} — add-tool first')


def select_feature_undo_targets(
    entries: list[Any],
    ghost_cells: list[list[int]] | list[tuple[int, int]],
) -> list[dict[str, Any]]:
    """Pick non-undone feature builds that touch the 3×3 ghost cells.

    Skips undone, begun, non-feature, and non-build entries. Same plan name is
    deduped to the latest seq. Newest plans are listed first (safer undo order).

    @param entries: journal EntryView-like objects (``.plan``, ``.pass_``, …)
    @param ghost_cells: cells that make up the bake-off baseline ring
    @returns: list of ``{plan, seq}`` dicts
    """
    scope = {tuple(c) for c in ghost_cells}
    latest: dict[str, int] = {}
    for e in entries:
        if getattr(e, 'kind', None) != 'build':
            continue
        if getattr(e, 'pass_', None) != 'feature':
            continue
        if getattr(e, 'undone', False):
            continue
        if getattr(e, 'status', None) == 'begun':
            continue
        cells = {tuple(c) for c in (getattr(e, 'cells', None) or ())}
        if getattr(e, 'cell', None) is not None:
            cells.add(tuple(e.cell))
        if not cells & scope:
            continue
        plan = e.plan
        seq = int(e.seq)
        if plan not in latest or seq > latest[plan]:
            latest[plan] = seq
    return [
        {'plan': plan, 'seq': seq}
        for plan, seq in sorted(latest.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _lock_read() -> dict | None:
    """Read ``site/LOCK.json`` (indirection for tests)."""
    import site_lock

    return site_lock.read()


def _lock_fresh(lk: dict | None) -> bool:
    """Whether ``lk`` is a fresh site lock (indirection for tests)."""
    import site_lock

    return bool(site_lock.fresh(lk))


def _lock_claim(owner: str, task: str) -> None:
    """Claim the site lock (indirection for tests)."""
    import site_lock

    site_lock.claim(owner, task)


def _lock_check(owner: str | None = None) -> None:
    """Refresh heartbeat for the held lock (indirection for tests)."""
    import site_lock

    site_lock.check(owner)


def ensure_baseline_lock(round_id: str, *, owner: str | None = None) -> str:
    """Require SITE_OWNER; claim the lock if free/stale, refuse if held by another.

    @param round_id: round id (for the claim task string)
    @param owner: lock owner (default ``SITE_OWNER``)
    @returns: owner id that holds the lock
    @raises RuntimeError: when SITE_OWNER is unset or another owner holds a fresh lock
    """
    owner = owner or os.environ.get('SITE_OWNER')
    if not owner:
        raise RuntimeError(
            'SITE_OWNER is not set; claim first '
            '(SITE_OWNER=<you> / scripts/site_lock.py claim …, or wrap the command in scripts/site_lock.py run <you> "<task>" -- <cmd>) then retry baseline'
        )
    lk = _lock_read()
    if _lock_fresh(lk) and lk is not None and lk.get('owner') != owner:
        raise RuntimeError(
            f'World lock held by {lk.get("owner")!r} '
            f'(task={lk.get("task")!r}); release or wait before baseline'
        )
    if not (_lock_fresh(lk) and lk is not None and lk.get('owner') == owner):
        _lock_claim(owner, f'model_bakeoff baseline {round_id}')
    else:
        _lock_check(owner)
    return owner


def _default_list_feature_entries(ghost_cells: list[list[int]]) -> list[Any]:
    """Load journal entries that touch ``ghost_cells`` (view-only registry)."""
    import cell_registry

    cells = [tuple(c) for c in ghost_cells]
    reg = cell_registry.open_registry(blocks=None, ghosts=None)
    return list(reg.entries(cells=cells))


def _default_undo(plan: str, *, meta: dict | None = None) -> Any:
    """Undo a plan via ``site_kit.undo``."""
    import site_kit

    return site_kit.undo(plan, meta=meta)


def _default_lidar_apply(focus: tuple[int, int]) -> None:
    """Run ``shims/lidar_ghost.py apply --cell … --ring 1 --include-focus --force``."""
    script = ROOT / 'shims' / 'lidar_ghost.py'
    cmd = [
        sys.executable,
        str(script),
        'apply',
        '--cell',
        f'{focus[0]},{focus[1]}',
        '--ring',
        '1',
        '--include-focus',
        '--force',
    ]
    result = subprocess.run(cmd, cwd=str(ROOT), check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f'lidar_ghost apply failed (exit {result.returncode}): '
            f'{(result.stderr or result.stdout or "").strip()[:500]}'
        )


def _default_lidar_status(focus: tuple[int, int]) -> dict:
    """Run ``shims/lidar_ghost.py status`` and parse JSON stdout."""
    script = ROOT / 'shims' / 'lidar_ghost.py'
    cmd = [
        sys.executable,
        str(script),
        'status',
        '--cell',
        f'{focus[0]},{focus[1]}',
        '--ring',
        '1',
        '--include-focus',
    ]
    result = subprocess.run(cmd, cwd=str(ROOT), check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f'lidar_ghost status failed (exit {result.returncode}): '
            f'{(result.stderr or result.stdout or "").strip()[:500]}'
        )
    return json.loads(result.stdout)


def _default_reconcile_clean(cell: tuple[int, int]) -> bool:
    """True when ``cell_registry reconcile <cell>`` reports no drift."""
    import cell_registry
    import site_kit

    reg = cell_registry.open_registry(blocks=site_kit.live_blocks())
    drift = reg.reconcile([cell])
    return bool(drift.clean)


def ghosts_complete_for_cells(status: dict, ghost_cells: list[list[int]]) -> bool:
    """True when every ghost cell row has ``ghosts_complete``.

    @param status: parsed ``lidar_ghost status`` JSON
    @param ghost_cells: expected cells
    @returns: whether all nine (or configured) cells are complete
    """
    by_cell = {}
    for row in status.get('cells') or []:
        c = row.get('cell')
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            by_cell[(int(c[0]), int(c[1]))] = bool(row.get('ghosts_complete'))
    for gc in ghost_cells:
        key = (int(gc[0]), int(gc[1]))
        if not by_cell.get(key):
            return False
    return True


# Earlier-round build artefacts left in site/builds/ (r010_susu.py, r011-lidar.npz, …).
# A previous Minecraft build is not evidence — baseline archives these before the sitting.
PRIOR_ROUND_BUILD_RE = re.compile(r'^r\d{3}[_-]', re.IGNORECASE)


def site_builds_dir(world_root: Path) -> Path:
    """``<world>/site/builds`` where models write sitting scripts.

    @param world_root: active world checkout
    @returns: builds directory path (may not exist yet)
    """
    return Path(world_root) / 'site' / 'builds'


def archive_rounds_dir(world_root: Path, round_id: str) -> Path:
    """Off-limits archive for prior-round artefacts cleared by baseline.

    @param world_root: active world checkout
    @param round_id: sitting id that triggered the archive (e.g. ``R-014``)
    @returns: ``<world>/archive/rounds/<id>/``
    """
    return Path(world_root) / 'archive' / 'rounds' / round_id


def list_prior_round_build_artefacts(builds_dir: Path) -> list[Path]:
    """List ``r0NN_*`` / ``r0NN-*`` files still under ``site/builds/``.

    @param builds_dir: ``site/builds`` directory
    @returns: sorted file paths (not directories)
    """
    builds = Path(builds_dir)
    if not builds.is_dir():
        return []
    found: list[Path] = []
    for path in builds.iterdir():
        if path.is_file() and PRIOR_ROUND_BUILD_RE.match(path.name):
            found.append(path)
    return sorted(found, key=lambda p: p.name.lower())


def archive_prior_round_builds(world_root: Path, round_id: str) -> list[str]:
    """Move earlier-round artefacts from ``site/builds/`` into ``archive/rounds/<id>/``.

    @param world_root: active world checkout
    @param round_id: sitting id owning this archive folder
    @returns: basenames that were moved (empty if none)
    """
    builds = site_builds_dir(world_root)
    sources = list_prior_round_build_artefacts(builds)
    if not sources:
        return []
    dest_dir = archive_rounds_dir(world_root, round_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for src in sources:
        dest = dest_dir / src.name
        if dest.exists():
            dest.unlink()
        shutil.move(str(src), str(dest))
        moved.append(src.name)
    return moved


def format_prior_round_builds_warning(names: list[str]) -> str | None:
    """Human warning when ``site/builds/`` still holds earlier-round artefacts.

    @param names: leftover basenames
    @returns: warning text, or None when the floor is clear
    """
    if not names:
        return None
    shown = ', '.join(names[:8])
    extra = f' (+{len(names) - 8} more)' if len(names) > 8 else ''
    return (
        'warning: site/builds/ still has earlier-round artefacts '
        f'({shown}{extra}); run baseline to move them under archive/rounds/ '
        '(off-limits — a previous Minecraft build is not evidence)'
    )


def format_baseline_report(report: dict) -> str:
    """Short human report for a baseline run.

    @param report: baseline report dict
    @returns: text
    """
    lines = [
        f"baseline {report.get('round_id')}: "
        f"{'PLAN' if report.get('plan_only') else 'done'}",
        f"  undone: {', '.join(report.get('undone') or []) or '(none)'}",
    ]
    if report.get('plan_only'):
        would = report.get('would_archive') or []
        lines.append(f"  would_archive: {', '.join(would) or '(none)'}")
    else:
        lines.append(f"  journal_seqs: {report.get('journal_seqs') or []}")
        lines.append(f"  clean: {report.get('clean')}")
        archived = report.get('archived') or []
        lines.append(f"  archived: {', '.join(archived) or '(none)'}")
        if report.get('archive_dir'):
            lines.append(f"  archive_dir: {report['archive_dir']}")
        if report.get('incomplete'):
            lines.append(f"  incomplete: {report['incomplete']}")
    return '\n'.join(lines)


def run_baseline(
    root: Path,
    round_id: str,
    *,
    plan_only: bool = False,
    world_root: Path | None = None,
    list_entries_fn: Callable[[list[list[int]]], list[Any]] | None = None,
    undo_fn: Callable[..., Any] | None = None,
    lidar_apply_fn: Callable[[], Any] | None = None,
    lidar_status_fn: Callable[[], dict] | None = None,
    reconcile_fn: Callable[[tuple[int, int]], bool] | None = None,
) -> dict[str, Any]:
    """Reset the 3×3 ghost baseline for a round: undo features, re-ghost, verify.

    Refuses unless the active world matches config ``world``. Real runs also
    require ``SITE_OWNER`` and the site lock (claims it when free). Moves
    earlier-round ``r0NN_*`` artefacts out of ``site/builds/`` into
    ``archive/rounds/<id>/`` so the next sitting cannot treat them as evidence.

    @param root: bake-offs root
    @param round_id: round to record the baseline report on
    @param plan_only: list undo targets only (no lock, undo, apply, or round write)
    @param world_root: world checkout (default ``active_world.WORLD_ROOT``)
    @param list_entries_fn: injectable journal listing (tests)
    @param undo_fn: injectable ``undo(plan, meta=…)`` (tests)
    @param lidar_apply_fn: injectable ghost apply (tests)
    @param lidar_status_fn: injectable status JSON (tests)
    @param reconcile_fn: injectable per-cell clean check (tests)
    @returns: baseline report dict
    @raises RuntimeError: world / lock / MCP step failure
    """
    require_bakeoff_active_world(root)
    cfg = load_config(root)
    round_ = load_round(root, round_id)
    ghost_cells = [list(c) for c in (round_.get('ghost_cells') or cfg.get('ghost_cells') or [])]
    if not ghost_cells:
        raise RuntimeError('no ghost_cells on round or config')
    focus = tuple(round_.get('cell') or cfg.get('cell') or [])
    if len(focus) < 2:
        raise RuntimeError('round/config missing focus cell')
    focus_t = (int(focus[0]), int(focus[1]))
    wroot = Path(world_root) if world_root is not None else Path(active_world.WORLD_ROOT)
    prior_names = [p.name for p in list_prior_round_build_artefacts(site_builds_dir(wroot))]

    list_entries = list_entries_fn or _default_list_feature_entries
    targets = select_feature_undo_targets(list_entries(ghost_cells), ghost_cells)
    undone = [t['plan'] for t in targets]

    if plan_only:
        return {
            'round_id': round_id,
            'plan_only': True,
            'undone': undone,
            'targets': targets,
            'ghost_cells': ghost_cells,
            'journal_seqs': [],
            'clean': None,
            'would_archive': prior_names,
        }

    ensure_baseline_lock(round_id)
    archived = archive_prior_round_builds(wroot, round_id)
    archive_rel = None
    if archived:
        world_id = str(cfg.get('world') or 'bakeoff')
        archive_rel = f'worlds/{world_id}/archive/rounds/{round_id}'

    undo = undo_fn or _default_undo
    journal_seqs: list[int] = []
    for plan in undone:
        receipt = undo(plan, meta={'round': round_id})
        seq = getattr(receipt, 'seq', None)
        if seq is not None:
            journal_seqs.append(int(seq))

    apply = lidar_apply_fn or (lambda: _default_lidar_apply(focus_t))
    apply()

    status_fn = lidar_status_fn or (lambda: _default_lidar_status(focus_t))
    status = status_fn()
    ghosts_ok = ghosts_complete_for_cells(status, ghost_cells)

    recon = reconcile_fn or _default_reconcile_clean
    incomplete: list[str] = []
    if not ghosts_ok:
        incomplete.append('lidar_ghost status: not all ghosts_complete')
    for gc in ghost_cells:
        cell = (int(gc[0]), int(gc[1]))
        if not recon(cell):
            incomplete.append(f'reconcile {cell[0]},{cell[1]}: drift')

    clean = not incomplete
    report = {
        'round_id': round_id,
        'plan_only': False,
        'undone': undone,
        'targets': targets,
        'ghost_cells': ghost_cells,
        'journal_seqs': journal_seqs,
        'archived': archived,
        'archive_dir': archive_rel,
        'clean': clean,
        'incomplete': incomplete or None,
        'at': utc_now(),
    }
    round_ = load_round(root, round_id)
    round_['baseline'] = {
        'undone': undone,
        'journal_seqs': journal_seqs,
        'archived': archived,
        'clean': clean,
        'at': report['at'],
    }
    if archive_rel:
        round_['baseline']['archive_dir'] = archive_rel
    if incomplete:
        round_['baseline']['incomplete'] = incomplete
    save_round(root, round_)
    if not clean:
        raise RuntimeError(
            'baseline finished but not clean: ' + '; '.join(incomplete)
        )
    return report


def _default_capture_view(
    eye: list[float],
    look: list[float],
    dest: Path,
    *,
    downscale: int = 1,
) -> Path:
    """Capture one client view via ``evidence_sheet._capture_view``."""
    from evidence_sheet import _capture_view

    return _capture_view(eye, look, dest, downscale=downscale)


def _default_save_world() -> None:
    """Flush the live Minecraft world to disk."""
    from mc_paced import Paced

    Paced().call('server_save_all_worlds', {'flush': True})


def _default_mc_to_wgs84(x: float, z: float) -> tuple[float, float]:
    """Minecraft X/Z → WGS84 (active world frame)."""
    from world_frame import mc_to_wgs84

    return mc_to_wgs84(x, z)


def load_capture_config(cfg: dict) -> dict[str, Any]:
    """Return the ``capture`` block from bake-off config.

    @param cfg: loaded config.json
    @returns: capture settings
    @raises ValueError: when the block or required cameras are missing
    """
    cap = cfg.get('capture')
    if not isinstance(cap, dict):
        raise ValueError('config.json missing capture camera table')
    cameras = cap.get('cameras')
    if not isinstance(cameras, dict) or not cameras:
        raise ValueError('config.json capture.cameras must be a non-empty object')
    for name in ('square_south', 'street_front', 'aerial_top'):
        if name not in cameras:
            raise ValueError(f'config.json capture.cameras missing {name!r}')
    return cap


def format_capture_report(report: dict) -> str:
    """Short human report for a capture run.

    @param report: capture report dict
    @returns: text
    """
    shots = report.get('shots_dir') or ''
    lines = [
        f"capture {report.get('round_id')}: wrote {shots}",
        f"  photos: {', '.join(report.get('photo_keys') or [])}",
    ]
    if report.get('aerial_crop'):
        lines.append(f"  aerial_crop: {report['aerial_crop']}")
    return '\n'.join(lines)


def run_capture(
    root: Path,
    round_id: str,
    *,
    world_root: Path | None = None,
    capture_fn: Callable[..., Path] | None = None,
    save_world_fn: Callable[[], Any] | None = None,
    mc_to_wgs84_fn: Callable[[float, float], tuple[float, float]] | None = None,
    pair_only: bool = False,
) -> dict[str, Any]:
    """Capture fixed interview cameras for a round and fill its ``photos`` map.

    Writes PNGs under ``<world>/verification/site/<slug>-shots/``, crops
    ``aerial_top``, writes matching ``real_aerial.png`` from the configured
    mosaic, saves the world, and updates the round JSON.

    @param root: bake-offs root
    @param round_id: round to capture
    @param world_root: active world checkout (default ``active_world.WORLD_ROOT``)
    @param capture_fn: injectable ``(eye, look, dest, *, downscale=1) → Path``
    @param save_world_fn: injectable world flush (tests)
    @param mc_to_wgs84_fn: injectable MC→WGS84 for real aerial crop (tests)
    @param pair_only: skip live captures; re-crop aerial from existing full frame
    @returns: capture report dict
    @raises RuntimeError: wrong active world
    @raises ValueError: missing capture config
    @raises FileNotFoundError: missing round or mosaic / prior frame
    """
    require_bakeoff_active_world(root)
    cfg = load_config(root)
    cap = load_capture_config(cfg)
    world_id = str(cfg.get('world') or 'bakeoff')
    wroot = Path(world_root) if world_root is not None else Path(active_world.WORLD_ROOT)
    load_round(root, round_id)  # ensure exists before writing files

    shots_rel = shots_rel_dir(world_id, round_id)
    out = wroot / 'verification' / 'site' / f'{round_shots_slug(round_id)}-shots'
    out.mkdir(parents=True, exist_ok=True)

    cameras: dict[str, Any] = dict(cap['cameras'])
    capture = capture_fn or _default_capture_view
    if not pair_only:
        for name, cam in cameras.items():
            eye = list(cam['eye'])
            look = list(cam['look'])
            capture(eye, look, out / f'{name}.png', downscale=1)

    crop_pixels = list(cap.get('aerial_crop_pixels') or [451, 156, 1061, 793])
    base_width = int(cap.get('aerial_crop_base_width') or 1512)
    rotation = int(cap.get('aerial_rotation_degrees') or 180)
    bbox = list(cap.get('aerial_bbox_mc') or [6140, -5584, 6230, -5490])

    metadata: dict[str, Any] = {
        'cameras': cameras,
        'aerial_bbox_mc': bbox,
        'FOV': cap.get('FOV', 70),
        'reference_plane_y': cap.get('reference_plane_y', 60),
        'aerial_crop_pixels': crop_pixels,
    }
    (out / 'cameras.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')

    from PIL import Image

    full_path = out / 'aerial_top-full.png'
    if not pair_only:
        Image.open(out / 'aerial_top.png').save(full_path)
    if not full_path.is_file():
        raise FileNotFoundError(
            f'missing {full_path}; run capture without --pair-only first'
        )
    im = Image.open(full_path)
    crop = scale_aerial_crop(crop_pixels, im.width, base_width=base_width)
    im.crop(crop).rotate(rotation).save(out / 'aerial_top.png')
    metadata['actual_capture_size'] = list(im.size)
    metadata['actual_aerial_crop_pixels'] = list(crop)
    metadata['aerial_rotation_degrees'] = rotation
    (out / 'cameras.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')

    real_cfg = dict(cap.get('real_aerial') or {})
    mosaic_rel = real_cfg.get(
        'mosaic',
        'reference/aerial/susu/mosaic-z18-3x3-centre-130054-87835.png',
    )
    mosaic_path = wroot / mosaic_rel
    if not mosaic_path.is_file():
        raise FileNotFoundError(f'real aerial mosaic missing: {mosaic_path}')
    origin = list(real_cfg.get('xyz_origin') or [130053, 87834])
    zoom = int(real_cfg.get('zoom') or 18)
    resize = list(real_cfg.get('resize') or [610, 637])
    wgs84 = mc_to_wgs84_fn or _default_mc_to_wgs84
    x0, z0, x1, z1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    corners = [
        xyz_pixel_from_wgs84(
            *wgs84(x, z),
            zoom=zoom,
            origin_tx=float(origin[0]),
            origin_ty=float(origin[1]),
        )
        for x in (x0, x1)
        for z in (z0, z1)
    ]
    us, vs = zip(*corners)
    real = Image.open(mosaic_path)
    real.crop((min(us), min(vs), max(us), max(vs))).resize(
        (int(resize[0]), int(resize[1]))
    ).save(out / 'real_aerial.png')

    save = save_world_fn or _default_save_world
    save()

    portals_note = str(cap.get('portals_note') or 'reference/portals/42-A..D.jpg')
    photos = photos_map_for_shots(
        shots_rel,
        camera_names=list(cameras.keys()),
        portals_note=portals_note,
    )
    round_ = load_round(root, round_id)
    round_['photos'] = photos
    save_round(root, round_)

    return {
        'round_id': round_id,
        'shots_dir': shots_rel,
        'shots_abs': str(out),
        'photos': photos,
        'photo_keys': list(photos.keys()),
        'aerial_crop': list(crop),
        'at': utc_now(),
    }


def format_status(root: Path) -> str:
    """Human status report.

    @param root: bake-offs root
    @returns: text
    """
    cfg = load_config(root)
    rounds = list_rounds(root)
    lines = [
        f"pack={cfg['pack']} cell={tuple(cfg['cell'])} pilot={cfg['pilot_task']}",
        f"active_tiers={cfg['active_tiers']} (high/non-bulk never scheduled)",
        '',
        'ROUNDS:',
    ]
    if not rounds:
        lines.append('  (none yet)')
    for r in rounds:
        usage = r.get('usage') or {}
        tok = usage.get('total_tokens')
        if tok is None and usage.get('input_tokens') is not None:
            out_t = usage.get('output_tokens')
            tok = (
                f"{usage['input_tokens']}+{out_t}"
                if out_t is not None
                else f"{usage['input_tokens']}+?"
            )
        usage_bit = f" tokens={tok}" if tok is not None else ''
        usage_bit += plan_pct_delta_bit(usage)
        model_bit = f"{r['tool']['id']}/{r['model']['id']}"
        if is_hybrid_round(r):
            designer_id = (r.get('designer') or {}).get('id') or '?'
            model_bit = (
                f"{r['tool']['id']}/builder={r['model']['id']}"
                f"+designer={designer_id}"
            )
        lines.append(
            f"  {r['id']} {r['status']:11} {r['tier']:4} "
            f"{model_bit} "
            f"task={r['task']} attempt={r['attempt']} {r['kind']} "
            f"overall={r.get('overall')}{usage_bit}"
        )
    nxt = suggest_next(root)
    lines.append('')
    if nxt is None:
        lines.append('NEXT: (nothing — catalogue empty, or ladder complete for pilot)')
    elif nxt['action'] == 'continue':
        lines.append(
            f"NEXT: continue {nxt['round_id']} "
            f"({nxt['tool']}/{nxt['model']} {nxt['kind']} attempt {nxt['attempt']})"
        )
    else:
        lines.append(
            f"NEXT: open --tool {nxt['tool']} --model {nxt['model']} "
            f"--task {nxt['task']}  # {nxt['tier']} / {nxt.get('label')}"
        )
        if nxt['tier'] == 'mid':
            lines.append('      (mid unlocked: a low cold round was not great)')
    return '\n'.join(lines)


def format_sitting(round_: dict) -> str:
    """Print a one-sitting card for the agent/human.

    @param round_: round dict
    @returns: text
    """
    lines = [
        f"=== {round_['id']} — one sitting ===",
        f"status: {round_['status']}",
        f"round_type: {round_.get('round_type') or 'single'}",
    ]
    designer = round_.get('designer') or {}
    if is_hybrid_round(round_):
        lines.append(
            f"builder (apply owner): {round_['tool']['id']} / "
            f"{round_['model']['label']} ({round_['model']['id']})"
        )
        lines.append(
            f"designer: {designer.get('label') or designer.get('id')} "
            f"({designer.get('id')})"
        )
        handoff = round_.get('handoff') or {}
        handoff_path = handoff.get('path') or (
            '(unset — record with open/score --handoff)'
        )
        kind_bit = f" kind={handoff.get('kind')}" if handoff.get('kind') else ''
        lines.append(f"handoff: {handoff_path}{kind_bit}")
    elif designer.get('id'):
        lines.append(
            f"builder (apply owner): {round_['tool']['id']} / "
            f"{round_['model']['label']} ({round_['model']['id']})"
        )
        lines.append(
            f"designer: {designer.get('label') or designer.get('id')} "
            f"({designer.get('id')}) — same model, one session"
        )
    else:
        lines.append(
            f"tool/model: {round_['tool']['id']} / {round_['model']['label']} "
            f"({round_['model']['id']})"
        )
    lines.extend(
        [
            f"tier: {round_['tier']}  kind: {round_['kind']}  attempt: {round_['attempt']}",
            f"task: {round_['task']}  pack: {round_['pack']}  cell: {tuple(round_['cell'])}",
            f"brief: bakeoff/{round_['pack_brief']}",
        ]
    )
    if round_.get('repair_brief'):
        lines.append(f"repair_brief: {round_['repair_brief']}")
    if round_.get('parent_round'):
        lines.append(f"parent: {round_['parent_round']}")
    lines.append('')
    steps_src = (
        'sitting_steps_hybrid' if is_hybrid_round(round_) else 'sitting_steps'
    )
    lines.append(
        f'Sequence (do not skip) — from bakeoff/config.json {steps_src}:'
    )
    for item in round_.get('sitting', {}).get('checklist') or []:
        lines.append(f'  - {item}')
    usage = round_.get('usage') or {}
    if any(
        usage.get(k) is not None
        for k in ('input_tokens', 'output_tokens', 'total_tokens', 'cost_usd')
    ):
        lines.append('')
        lines.append(
            f"usage: in={usage.get('input_tokens')} out={usage.get('output_tokens')} "
            f"total={usage.get('total_tokens')} cost_usd={usage.get('cost_usd')} "
            f"source={usage.get('source')}"
        )
        by_role = usage.get('by_role') or {}
        for role_name in USAGE_ROLES:
            block = by_role.get(role_name) or {}
            if block.get('total_tokens') is not None:
                lines.append(
                    f"  {role_name}: total={block.get('total_tokens')} "
                    f"requests={block.get('requests')} "
                    f"prefix={block.get('model_prefix')}"
                )
    lines.append('')
    lines.append('When finished:')
    lines.append(
        f"  python3 bakeoff/model_bakeoff.py score {round_['id']} "
        '--mech-gate pass --overall pass '
        '--axis massing=pass --axis keep=pass '
        '# …all axes…'
    )
    if is_hybrid_round(round_):
        lines.append(
            f"  python3 bakeoff/model_bakeoff.py usage {round_['id']} "
            '--from-transcript <session.jsonl>  # splits designer/builder'
        )
    else:
        lines.append(
            f"  python3 bakeoff/model_bakeoff.py usage {round_['id']} "
            '--input N --output M --source cursor-ui'
        )
    lines.append(
        '  (if Cursor usage UI has no numbers: leave usage null — do not invent)'
    )
    return '\n'.join(lines)


def _parse_axis(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in pairs or []:
        if '=' not in raw:
            raise ValueError(f'axis must be name=grade, got {raw!r}')
        name, grade = raw.split('=', 1)
        out[name.strip()] = grade.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    """CLI entry.

    @param argv: optional arg list
    @returns: exit code
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', type=Path, default=None, help='override bakeoff/ path')
    sub = ap.add_subparsers(dest='cmd', required=True)

    sub.add_parser('status', help='list rounds and suggest next sitting')
    p_next = sub.add_parser('next', help='print next suggested sitting as JSON')
    p_next.add_argument('--task')

    p_tool = sub.add_parser('add-tool', help='register an agent app / host')
    p_tool.add_argument('--id', required=True)
    p_tool.add_argument('--name', required=True)

    p_model = sub.add_parser('add-model', help='register a model under a tool')
    p_model.add_argument('--tool', required=True)
    p_model.add_argument('--id', required=True)
    p_model.add_argument('--tier', required=True, choices=TIERS)
    p_model.add_argument('--label', required=True)
    p_model.add_argument(
        '--no-bulk',
        action='store_true',
        help='mark too dear for bulk bake-offs (never scheduled)',
    )

    p_open = sub.add_parser('open', help='create one self-contained round for a sitting')
    p_open.add_argument('--tool', required=True)
    p_open.add_argument('--model', required=True)
    p_open.add_argument('--task', default=None)
    p_open.add_argument(
        '--force-mid',
        action='store_true',
        help='allow opening mid even if low has not unlocked it (escape hatch)',
    )
    p_open.add_argument(
        '--plan-pct',
        type=int,
        default=None,
        help='weekly plan usage %% at sitting start (all models; 0..100)',
    )
    p_open.add_argument(
        '--build-script',
        action='append',
        default=[],
        type=Path,
        dest='build_scripts',
        help='build script path to remember for score-time artefact copy (repeatable)',
    )
    p_open.add_argument(
        '--designer',
        default=None,
        help=(
            'designer model id (builder is --model / apply owner). '
            'When omitted, uses config/build-roles.json for the same tool, '
            'or equals --model when the tool differs. Same id as --model = '
            'one session; different ids = hybrid'
        ),
    )
    p_open.add_argument(
        '--handoff',
        type=Path,
        default=None,
        help='designer hand-off artefact path (spec or build script); hybrid only',
    )

    p_score = sub.add_parser('score', help='record interview grades for a round')
    p_score.add_argument('round_id')
    p_score.add_argument('--overall', choices=list(GRADE_RANK))
    p_score.add_argument('--axis', action='append', default=[], help='name=grade (repeatable)')
    p_score.add_argument('--mech-gate', default='pass', choices=('pass', 'fail'))
    p_score.add_argument('--why', default=None)
    p_score.add_argument('--notes', default=None)
    p_score.add_argument(
        '--plan-pct',
        type=int,
        default=None,
        help='weekly plan usage %% at sitting end (all models; 0..100)',
    )
    p_score.add_argument(
        '--wallclock-min',
        type=int,
        default=None,
        help='active wall-clock minutes for the sitting',
    )
    p_score.add_argument(
        '--interruptions',
        default=None,
        help='short interruption notes (stored on the usage block)',
    )
    p_score.add_argument(
        '--build-script',
        action='append',
        default=[],
        type=Path,
        dest='build_scripts',
        help='build script to copy into bakeoff/rounds/R-NNN/ (repeatable; '
        'else uses open --build-script or journal plan for this round)',
    )
    p_score.add_argument(
        '--handoff',
        type=Path,
        default=None,
        help='designer hand-off artefact path to record + copy (hybrid rounds)',
    )

    p_repair = sub.add_parser('repair', help='open attempt N+1 with a repair brief')
    p_repair.add_argument('round_id')
    p_repair.add_argument('--brief', required=True)

    p_usage = sub.add_parser(
        'usage',
        help='record token/cost usage (prefer --from-transcript; else paste from UI)',
    )
    p_usage.add_argument('round_id')
    p_usage.add_argument(
        '--from-transcript',
        type=Path,
        default=None,
        help='Claude Code session/subagent .jsonl (or dir); sums API usage, source=api',
    )
    p_usage.add_argument(
        '--model-prefix',
        default=None,
        help='with --from-transcript: keep models starting with this id (e.g. claude-opus-5-5)',
    )
    p_usage.add_argument(
        '--role',
        default=None,
        choices=USAGE_ROLES,
        help='hybrid only: record designer or builder usage (auto-split if omitted '
        'with --from-transcript)',
    )
    p_usage.add_argument('--input', type=int, default=None, dest='input_tokens')
    p_usage.add_argument('--output', type=int, default=None, dest='output_tokens')
    p_usage.add_argument('--total', type=int, default=None, dest='total_tokens')
    p_usage.add_argument('--cache-read', type=int, default=None, dest='cache_read_tokens')
    p_usage.add_argument('--cache-write', type=int, default=None, dest='cache_write_tokens')
    p_usage.add_argument('--requests', type=int, default=None)
    p_usage.add_argument('--cost-usd', type=float, default=None)
    p_usage.add_argument(
        '--source',
        default='unknown',
        choices=USAGE_SOURCES,
        help='where the numbers came from (default unknown; forced to api with --from-transcript)',
    )
    p_usage.add_argument('--notes', default=None)
    p_usage.add_argument(
        '--plan-pct-start',
        type=int,
        default=None,
        help='weekly plan %% at sitting start (0..100)',
    )
    p_usage.add_argument(
        '--plan-pct-end',
        type=int,
        default=None,
        help='weekly plan %% at sitting end (0..100)',
    )
    p_usage.add_argument('--wallclock-min', type=int, default=None)
    p_usage.add_argument('--interruptions', default=None)

    p_show = sub.add_parser('show', help='print a round JSON path / sitting card')
    p_show.add_argument('round_id')

    p_base = sub.add_parser(
        'baseline',
        help='undo feature builds in ghost_cells, lidar_ghost apply --force, reconcile',
    )
    p_base.add_argument('round_id')
    p_base.add_argument(
        '--plan',
        action='store_true',
        dest='plan_only',
        help='list feature plans that would be undone; change nothing',
    )

    p_cap = sub.add_parser(
        'capture',
        help='fixed interview cameras from config; write shots + fill round photos',
    )
    p_cap.add_argument('round_id')
    p_cap.add_argument(
        '--pair-only',
        action='store_true',
        help='skip live view capture; re-crop aerial from aerial_top-full.png',
    )

    args = ap.parse_args(argv)
    root = bakeoffs_root(args.root)

    try:
        if args.cmd == 'status':
            print(format_status(root))
            return 0
        if args.cmd == 'next':
            nxt = suggest_next(root, task=args.task)
            print(json.dumps(nxt, indent=2))
            return 0 if nxt else 1
        if args.cmd == 'add-tool':
            add_tool(root, args.id, args.name)
            print(f'added tool {args.id}')
            return 0
        if args.cmd == 'add-model':
            add_model(
                root,
                tool=args.tool,
                model_id=args.id,
                tier=args.tier,
                label=args.label,
                bulk_ok=not args.no_bulk,
            )
            print(f'added {args.tool}/{args.id} tier={args.tier}')
            return 0
        if args.cmd == 'open':
            require_bakeoff_active_world(root)
            from build_roles import resolve_roles

            roles = resolve_roles(
                designer=args.designer,
                builder=args.model,
                tool=args.tool,
            )
            round_ = open_round(
                root,
                tool=args.tool,
                model=roles.builder,
                task=args.task,
                force_mid=bool(args.force_mid),
                plan_pct=args.plan_pct,
                build_scripts=args.build_scripts or None,
                designer=roles.designer,
                handoff=args.handoff,
            )
            print(format_sitting(round_))
            print(f"\nwrote {round_path(root, round_['id'])}")
            leftovers = [
                p.name
                for p in list_prior_round_build_artefacts(
                    site_builds_dir(Path(active_world.WORLD_ROOT))
                )
            ]
            warn = format_prior_round_builds_warning(leftovers)
            if warn:
                print(warn, file=sys.stderr)
            return 0
        if args.cmd == 'score':
            round_ = score_round(
                root,
                args.round_id,
                overall=args.overall,
                axes=_parse_axis(args.axis),
                mech_gate=args.mech_gate,
                why=args.why,
                notes=args.notes,
                plan_pct=args.plan_pct,
                wallclock_min=args.wallclock_min,
                interruptions=args.interruptions,
                build_scripts=args.build_scripts or None,
                handoff=args.handoff,
            )
            arts = round_.get('artefacts') or {}
            scripts = arts.get('build_scripts') or []
            extra = f" artefacts={len(scripts)} script(s)" if scripts else ''
            if arts.get('cameras'):
                extra += ' +cameras'
            print(f"scored {round_['id']} overall={round_['overall']}{extra}")
            return 0
        if args.cmd == 'repair':
            require_bakeoff_active_world(root)
            child = open_repair(root, args.round_id, args.brief)
            print(format_sitting(child))
            print(f"\nwrote {round_path(root, child['id'])}")
            return 0
        if args.cmd == 'usage':
            round_ = record_usage(
                root,
                args.round_id,
                input_tokens=args.input_tokens,
                output_tokens=args.output_tokens,
                total_tokens=args.total_tokens,
                cache_read_tokens=args.cache_read_tokens,
                cache_write_tokens=args.cache_write_tokens,
                requests=args.requests,
                cost_usd=args.cost_usd,
                source=args.source,
                notes=args.notes,
                plan_pct_start=args.plan_pct_start,
                plan_pct_end=args.plan_pct_end,
                wallclock_min=args.wallclock_min,
                interruptions=args.interruptions,
                from_transcript=args.from_transcript,
                model_prefix=args.model_prefix,
                role=args.role,
            )
            u = round_['usage']
            plan_bit = plan_pct_delta_bit(u)
            print(
                f"usage {round_['id']}: in={u.get('input_tokens')} "
                f"out={u.get('output_tokens')} total={u.get('total_tokens')} "
                f"cache_read={u.get('cache_read_tokens')} "
                f"cache_write={u.get('cache_write_tokens')} "
                f"requests={u.get('requests')} "
                f"cost_usd={u.get('cost_usd')} source={u.get('source')}"
                f"{plan_bit}"
            )
            by_role = u.get('by_role') or {}
            for role_name in USAGE_ROLES:
                block = by_role.get(role_name) or {}
                if block.get('total_tokens') is not None:
                    print(
                        f"  {role_name}: total={block.get('total_tokens')} "
                        f"requests={block.get('requests')} "
                        f"prefix={block.get('model_prefix')}"
                    )
            return 0
        if args.cmd == 'show':
            round_ = load_round(root, args.round_id)
            print(format_sitting(round_))
            print()
            print(json.dumps(round_, indent=2))
            return 0
        if args.cmd == 'baseline':
            report = run_baseline(root, args.round_id, plan_only=bool(args.plan_only))
            print(format_baseline_report(report))
            if args.plan_only:
                for t in report.get('targets') or []:
                    print(f"  would undo {t['plan']} (journal seq {t['seq']})")
            return 0
        if args.cmd == 'capture':
            report = run_capture(
                root,
                args.round_id,
                pair_only=bool(args.pair_only),
            )
            print(format_capture_report(report))
            return 0
    except (ValueError, KeyError, FileNotFoundError, RuntimeError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

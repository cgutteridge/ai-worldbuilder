#!/usr/bin/env python3
"""Model bake-off framework: one model per sitting, self-contained round records.

  python3 scripts/model_bakeoff.py status
  python3 scripts/model_bakeoff.py next
  python3 scripts/model_bakeoff.py add-tool --id cursor --name Cursor
  python3 scripts/model_bakeoff.py add-model --tool cursor --id composer --tier low --label Composer
  python3 scripts/model_bakeoff.py open --tool cursor --model composer --task feature_design
  python3 scripts/model_bakeoff.py score R-001 --overall great --axis massing=great --axis keep=great
  python3 scripts/model_bakeoff.py usage R-001 --input 12000 --output 3000 --source cursor-ui
  python3 scripts/model_bakeoff.py repair R-001 --brief "raise ridge one block"

Records live under ``model-bakeoffs/`` (tool repo). See ``model-bakeoffs/README.md``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from atomic_json import write_json

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BAKEOFFS = ROOT / 'model-bakeoffs'

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

    Cursor (and most agent UIs) rarely expose exact tokens to the agent; paste
    from the tool's usage panel after the sitting when you can.

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
        'source': None,
        'recorded_at': None,
        'notes': None,
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
) -> dict:
    """Create a self-contained round record for one sitting.

    @param root: bake-offs root
    @param tool: tool id
    @param model: model id
    @param task: task id (default pilot)
    @param kind: cold | repair
    @param attempt: 1..max_attempts
    @param parent_round: parent id for repairs
    @param repair_brief: repair text for kind=repair
    @param force_mid: allow mid cold open before low unlocks (escape hatch)
    @returns: new round dict (also written to disk)
    """
    cfg = load_config(root)
    catalogue = load_catalogue(root)
    task = task or cfg['pilot_task']
    tool_row, model_row = find_model(catalogue, tool, model)
    if not model_row.get('bulk_ok', True):
        raise ValueError(f'model {model!r} is not bulk_ok — skip for bake-offs')
    if model_row.get('tier') not in cfg['active_tiers']:
        raise ValueError(
            f"model tier {model_row.get('tier')!r} not in active_tiers {cfg['active_tiers']}"
        )
    if model_row.get('tier') == 'mid' and kind == 'cold' and not force_mid:
        if not mid_unlocked(list_rounds(root), task, cfg['pack']):
            raise ValueError(
                'mid tier locked until a low cold round scores fail or pass '
                '(not great). Score low sittings first.'
            )

    round_id = next_round_id(root)
    pack = cfg['pack']
    round_: dict[str, Any] = {
        'id': round_id,
        'status': 'ready',
        'created': utc_now(),
        'scored_at': None,
        'task': task,
        'pack': pack,
        'pack_brief': f'packs/{pack}/BRIEF.md',
        'tier': model_row['tier'],
        'tool': {'id': tool_row['id'], 'name': tool_row.get('name') or tool_row['id']},
        'model': {
            'id': model_row['id'],
            'label': model_row.get('label') or model_row['id'],
            'tier': model_row['tier'],
            'bulk_ok': bool(model_row.get('bulk_ok', True)),
        },
        'cell': list(cfg['cell']),
        'cell_note': cfg.get('cell_note'),
        'ghost_cells': [list(c) for c in cfg.get('ghost_cells') or []],
        'ghost_snap': cfg.get('ghost_snap'),
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
        'usage': usage_template(),
        'sitting': {
            'checklist': [
                'One apply owner for this sitting (parent recovery → interrupt the model subagent first)',
                'Quit Minecraft world (title / close save)',
                (
                    f"Restore ghost (whole-world + registry): "
                    f"python3 scripts/model_bakeoff.py restore-ghost --confirm-logged-out"
                    + (f"  # cfg ghost_snap={cfg['ghost_snap']}" if cfg.get('ghost_snap') else '')
                ),
                'Rejoin world (MCP up) — ghost may look sparse/empty around the cell',
                'Claim site lock; build with that tool/model only',
                f"Use tool={tool_row['id']} model={model_row['id']} only",
                f'Read packs/{pack}/BRIEF.md + packs/{pack}/FAIRNESS.md (and repair_brief if kind=repair)',
                'Build from evidence table — not a previous Minecraft build',
                'Log footprint pad: pad_cells / osm_cells (square only to kill 1-block stairs, not a fixed +2 halo)',
                'Apply, capture fixed cameras, release lock',
                'Interview + score; then usage paste (need-you if counts unknown)',
            ],
            'ghost_snap_hint': (
                f"canonical whole-world snap: {cfg['ghost_snap']}"
                if cfg.get('ghost_snap')
                else 'create with: python3 scripts/region_backup.py snap-world --name bakeoff-ghost --confirm-logged-out'
            ),
        },
        'notes': 'opened with --force-mid' if force_mid else '',
        'force_mid': bool(force_mid) or None,
    }
    if round_['force_mid'] is None:
        del round_['force_mid']
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
) -> dict:
    """Record interview grades on a round.

    @param root: bake-offs root
    @param round_id: round id
    @param overall: optional override; else derived from critical axes
    @param axes: axis → grade
    @param mech_gate: pass | fail
    @param why: why-not-great text
    @param notes: free notes
    @param axis_notes: optional per-axis notes
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
    round_['status'] = 'scored'
    round_['scored_at'] = utc_now()
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
) -> dict:
    """Attach token/cost usage to a round (merge; does not clear omitted fields).

    Prefer numbers from the tool's usage UI (`source=cursor-ui`). Agents often
    cannot read tokens themselves — leave nulls rather than inventing counts.

    @param root: bake-offs root
    @param round_id: round id
    @param input_tokens: prompt / input tokens
    @param output_tokens: completion / output tokens
    @param total_tokens: total when the UI only shows one number
    @param cache_read_tokens: optional cache read tokens
    @param cache_write_tokens: optional cache write tokens
    @param requests: optional billable request count
    @param cost_usd: optional USD cost
    @param source: where the numbers came from (see USAGE_SOURCES)
    @param notes: free text (e.g. parent+subagent split)
    @returns: updated round
    """
    if source not in USAGE_SOURCES:
        raise ValueError(f'source must be one of {USAGE_SOURCES}, got {source!r}')
    for label, value in (
        ('input_tokens', input_tokens),
        ('output_tokens', output_tokens),
        ('total_tokens', total_tokens),
        ('cache_read_tokens', cache_read_tokens),
        ('cache_write_tokens', cache_write_tokens),
        ('requests', requests),
    ):
        if value is not None and (not isinstance(value, int) or value < 0):
            raise ValueError(f'{label} must be a non-negative int, got {value!r}')
    if cost_usd is not None and cost_usd < 0:
        raise ValueError(f'cost_usd must be >= 0, got {cost_usd!r}')

    round_ = load_round(root, round_id)
    usage = dict(round_.get('usage') or usage_template())
    updates: dict[str, Any] = {
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'total_tokens': total_tokens,
        'cache_read_tokens': cache_read_tokens,
        'cache_write_tokens': cache_write_tokens,
        'requests': requests,
        'cost_usd': cost_usd,
        'notes': notes,
    }
    for key, value in updates.items():
        if value is not None:
            usage[key] = value
    if (
        usage.get('total_tokens') is None
        and usage.get('input_tokens') is not None
        and usage.get('output_tokens') is not None
    ):
        usage['total_tokens'] = int(usage['input_tokens']) + int(usage['output_tokens'])
    usage['source'] = source
    usage['recorded_at'] = utc_now()
    round_['usage'] = usage
    save_round(root, round_)
    return round_


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
        lines.append(
            f"  {r['id']} {r['status']:11} {r['tier']:4} "
            f"{r['tool']['id']}/{r['model']['id']} "
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
        f"tool/model: {round_['tool']['id']} / {round_['model']['label']} ({round_['model']['id']})",
        f"tier: {round_['tier']}  kind: {round_['kind']}  attempt: {round_['attempt']}",
        f"task: {round_['task']}  pack: {round_['pack']}  cell: {tuple(round_['cell'])}",
        f"brief: model-bakeoffs/{round_['pack_brief']}",
    ]
    if round_.get('repair_brief'):
        lines.append(f"repair_brief: {round_['repair_brief']}")
    if round_.get('parent_round'):
        lines.append(f"parent: {round_['parent_round']}")
    lines.append('')
    lines.append('Sequence (do not skip):')
    lines.append(
        '  quit world → model_bakeoff restore-ghost (whole-world + registry) '
        '→ rejoin → lock → build/apply → photos → interview'
    )
    lines.append('')
    lines.append('Checklist:')
    for item in round_.get('sitting', {}).get('checklist') or []:
        lines.append(f'  - {item}')
    usage = round_.get('usage') or {}
    if any(usage.get(k) is not None for k in ('input_tokens', 'output_tokens', 'total_tokens', 'cost_usd')):
        lines.append('')
        lines.append(
            f"usage: in={usage.get('input_tokens')} out={usage.get('output_tokens')} "
            f"total={usage.get('total_tokens')} cost_usd={usage.get('cost_usd')} "
            f"source={usage.get('source')}"
        )
    lines.append('')
    lines.append('When finished:')
    lines.append(
        f"  python3 scripts/model_bakeoff.py score {round_['id']} "
        '--mech-gate pass --overall pass '
        '--axis massing=pass --axis keep=pass '
        '# …all axes…'
    )
    lines.append(
        f"  python3 scripts/model_bakeoff.py usage {round_['id']} "
        '--input N --output M --source cursor-ui'
    )
    lines.append('  (if Cursor usage UI has no numbers: leave usage null — do not invent)')
    return '\n'.join(lines)


def _parse_axis(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in pairs or []:
        if '=' not in raw:
            raise ValueError(f'axis must be name=grade, got {raw!r}')
        name, grade = raw.split('=', 1)
        out[name.strip()] = grade.strip()
    return out


def restore_ghost_step(
    *,
    root: Path | None = None,
    snap: str | None = None,
    confirm_logged_out: bool = False,
) -> list[str]:
    """Restore the bake-off whole-world ghost (regions + registry pair).

    Ticket #32: bake-off wipes/restores the whole world with one paired backup,
    not per-cell slices. Partial snaps are refused by region_backup.restore.
    When config sets ``ghost_region_path``, restore targets that folder (bakeoff
    save) rather than the snapshot's recorded SouthamptonAI ``region_path``.

    @param root: model-bakeoffs root (for config ghost_snap)
    @param snap: override snap name (default config ``ghost_snap``)
    @param confirm_logged_out: logout gate flag
    @returns: restored ``.mca`` names
    """
    import region_backup as rb

    cfg = load_config(bakeoffs_root(root))
    name = snap or cfg.get('ghost_snap')
    if not name:
        raise ValueError(
            'no ghost_snap in model-bakeoffs/config.json; pass --snap or run '
            'region_backup.py snap-world --name bakeoff-ghost --confirm-logged-out'
        )
    snap_dir = rb.resolve_snap_dir(str(name))
    region = cfg.get('ghost_region_path')
    return rb.restore_snap(
        snap_dir,
        confirm_logged_out=confirm_logged_out,
        allow_partial=False,
        restore_registry=True,
        region_dir=Path(region) if region else None,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry.

    @param argv: optional arg list
    @returns: exit code
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', type=Path, default=None, help='override model-bakeoffs/ path')
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

    p_score = sub.add_parser('score', help='record interview grades for a round')
    p_score.add_argument('round_id')
    p_score.add_argument('--overall', choices=list(GRADE_RANK))
    p_score.add_argument('--axis', action='append', default=[], help='name=grade (repeatable)')
    p_score.add_argument('--mech-gate', default='pass', choices=('pass', 'fail'))
    p_score.add_argument('--why', default=None)
    p_score.add_argument('--notes', default=None)

    p_repair = sub.add_parser('repair', help='open attempt N+1 with a repair brief')
    p_repair.add_argument('round_id')
    p_repair.add_argument('--brief', required=True)

    p_usage = sub.add_parser(
        'usage',
        help='record token/cost usage for a round (paste from tool usage UI when possible)',
    )
    p_usage.add_argument('round_id')
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
        help='where the numbers came from (default unknown)',
    )
    p_usage.add_argument('--notes', default=None)

    p_show = sub.add_parser('show', help='print a round JSON path / sitting card')
    p_show.add_argument('round_id')

    p_rg = sub.add_parser(
        'restore-ghost',
        help='restore whole-world + registry ghost snap (quit world first)',
    )
    p_rg.add_argument('--snap', default=None, help='override config ghost_snap')
    p_rg.add_argument('--confirm-logged-out', action='store_true', required=True)

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
            round_ = open_round(
                root,
                tool=args.tool,
                model=args.model,
                task=args.task,
                force_mid=bool(args.force_mid),
            )
            print(format_sitting(round_))
            print(f"\nwrote {round_path(root, round_['id'])}")
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
            )
            print(f"scored {round_['id']} overall={round_['overall']}")
            return 0
        if args.cmd == 'repair':
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
            )
            u = round_['usage']
            print(
                f"usage {round_['id']}: in={u.get('input_tokens')} "
                f"out={u.get('output_tokens')} total={u.get('total_tokens')} "
                f"cost_usd={u.get('cost_usd')} source={u.get('source')}"
            )
            return 0
        if args.cmd == 'show':
            round_ = load_round(root, args.round_id)
            print(format_sitting(round_))
            print()
            print(json.dumps(round_, indent=2))
            return 0
        if args.cmd == 'restore-ghost':
            restored = restore_ghost_step(
                root=root,
                snap=args.snap,
                confirm_logged_out=bool(args.confirm_logged_out),
            )
            print(f'restored ghost: {len(restored)} region files (+ registry if present)')
            return 0
    except (ValueError, KeyError, FileNotFoundError, RuntimeError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

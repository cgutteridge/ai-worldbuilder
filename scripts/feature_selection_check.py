#!/usr/bin/env python3
"""Paper-only feature-selection smoke checks (not A/B bake-offs).

  python3 scripts/feature_selection_check.py list
  python3 scripts/feature_selection_check.py brief FS-001
  python3 scripts/feature_selection_check.py grade FS-001 --answer answer.json

See ``model-bakeoffs/checks/feature-selection/``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHECKS = ROOT / 'model-bakeoffs' / 'checks' / 'feature-selection'

ANSWER_SCHEMA = {
    'osm_way_id': 'string — vector-map way id of the single feature the cue names',
    'reject_ways': 'string[] — neighbouring map way ids that must NOT be included',
    'bbox_mc': '[x0, z0, x1, z1] inclusive Minecraft block bounds of that way only',
    'dilated': 'boolean — must be false (no readable-footprint pad)',
    'notes': 'string — optional short evidence trail',
}


def checks_root(path: Path | None = None) -> Path:
    """Resolve the feature-selection checks root.

    @param path: optional override (tests)
    @returns: directory with scenarios/ and keys/
    """
    return Path(path) if path is not None else DEFAULT_CHECKS


def _load_json(path: Path) -> Any:
    """Load a JSON file.

    @param path: file path
    @returns: parsed JSON
    """
    return json.loads(path.read_text(encoding='utf-8'))


def list_scenarios(root: Path | None = None) -> list[dict]:
    """List scenario metadata (id, title, cell).

    @param root: checks root override
    @returns: sorted scenario dicts
    """
    scenarios_dir = checks_root(root) / 'scenarios'
    out: list[dict] = []
    for path in sorted(scenarios_dir.glob('FS-*.json')):
        data = _load_json(path)
        out.append({
            'id': data['id'],
            'title': data.get('title', ''),
            'cell': data.get('cell'),
            'path': str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        })
    return out


def load_scenario(scenario_id: str, root: Path | None = None) -> dict:
    """Load one scenario JSON.

    @param scenario_id: e.g. FS-001
    @param root: checks root override
    @returns: scenario dict
    """
    path = checks_root(root) / 'scenarios' / f'{scenario_id}.json'
    if not path.is_file():
        raise SystemExit(f'unknown scenario: {scenario_id} ({path})')
    return _load_json(path)


def load_key(scenario_id: str, root: Path | None = None) -> dict:
    """Load gold key for a scenario (operator / grader only).

    @param scenario_id: e.g. FS-001
    @param root: checks root override
    @returns: gold key dict
    """
    path = checks_root(root) / 'keys' / f'{scenario_id}.json'
    if not path.is_file():
        raise SystemExit(f'missing key: {scenario_id} ({path})')
    return _load_json(path)


def format_brief(scenario: dict) -> str:
    """Build the pasteable agent brief for a scenario.

    @param scenario: scenario dict
    @returns: markdown brief
    """
    cue = scenario.get('cue') or {}
    photos = cue.get('photos') or []
    photo_lines = '\n'.join(f'- `{p}`' for p in photos) if photos else '- (none attached; use cue text + vector map/portals)'
    photo_note = cue.get('photo_note')
    stand = cue.get('stand_mc')
    allowed = '\n'.join(f'- {a}' for a in scenario.get('allowed') or [])
    forbidden = '\n'.join(f'- {f}' for f in scenario.get('forbidden') or [])
    schema_lines = '\n'.join(f'- `{k}`: {v}' for k, v in ANSWER_SCHEMA.items())
    bits = [
        f'# Feature-selection check `{scenario["id"]}`',
        '',
        f'**Title:** {scenario.get("title", "")}',
        f'**Cell:** `{scenario.get("cell")}`',
        '',
        'Paper only: identify the feature and its exclusive bounds. Do not apply, do not design massing.',
        '',
        '## Cue',
        '',
        cue.get('text', ''),
    ]
    if stand:
        bits.extend(['', f'**Stand (MC x, z):** `{stand[0]}, {stand[1]}`'])
    bits.extend([
        '',
        '## Photos (look at these paths if present)',
        '',
        photo_lines,
    ])
    if photo_note:
        bits.extend(['', f'_Note:_ {photo_note}'])
    bits.extend([
        '',
        '## Allowed',
        '',
        allowed or '- (none listed)',
        '',
        '## Forbidden',
        '',
        forbidden or '- (none listed)',
        '',
        '## Answer',
        '',
        'Reply with **one JSON object only** (no build script), fields:',
        '',
        schema_lines,
        '',
        'Bounds must be the **exclusive** vector-map footprint of `osm_way_id` (inclusive bbox is fine).',
        'Do **not** grow a readable-massing fringe. Set `dilated` to `false`.',
    ])
    return '\n'.join(bits) + '\n'


def osm_way_bbox(way_id: str) -> tuple[int, int, int, int]:
    """Inclusive MC bbox of one vector-map building way.

    @param way_id: map way id string
    @returns: (x0, z0, x1, z1)
    """
    from osm_footprints import OSMIndex

    want = str(way_id).lstrip('w')
    idx = OSMIndex()
    cells: list[tuple[int, int]] = []
    for (x, z), i in idx.cells().items():
        props = idx.buildings[i][1]
        wid = str(props.get('osm_way_id') or props.get('osm_id') or '').lstrip('w')
        if wid == want:
            cells.append((x, z))
    if not cells:
        raise SystemExit(f'no vector-map cells for way {way_id}')
    xs = [c[0] for c in cells]
    zs = [c[1] for c in cells]
    return min(xs), min(zs), max(xs), max(zs)


def bbox_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersection-over-union of two inclusive x/z bboxes.

    @param a: (x0, z0, x1, z1)
    @param b: (x0, z0, x1, z1)
    @returns: IoU in [0, 1]
    """
    ax0, az0, ax1, az1 = a
    bx0, bz0, bx1, bz1 = b
    ix0, iz0 = max(ax0, bx0), max(az0, bz0)
    ix1, iz1 = min(ax1, bx1), min(az1, bz1)
    if ix1 < ix0 or iz1 < iz0:
        return 0.0
    inter = (ix1 - ix0 + 1) * (iz1 - iz0 + 1)
    area_a = (ax1 - ax0 + 1) * (az1 - az0 + 1)
    area_b = (bx1 - bx0 + 1) * (bz1 - bz0 + 1)
    union = area_a + area_b - inter
    return inter / union if union else 0.0


def chebyshev_outer_pad(
    claimed: tuple[int, int, int, int],
    gold: tuple[int, int, int, int],
) -> int:
    """How far the claimed bbox sticks out past gold on the worst side.

    @param claimed: agent bbox
    @param gold: vector-map bbox
    @returns: max blocks of outer pad (0 if claimed ⊆ gold expanded by 0)
    """
    cx0, cz0, cx1, cz1 = claimed
    gx0, gz0, gx1, gz1 = gold
    return max(
        gx0 - cx0,  # claimed extends west of gold
        gz0 - cz0,
        cx1 - gx1,
        cz1 - gz1,
        0,
    )


def normalize_way_id(value: Any) -> str:
    """Normalize a vector-map way id to digits-only string.

    @param value: raw answer field
    @returns: normalized id
    """
    s = str(value).strip()
    s = re.sub(r'^[Ww]', '', s)
    return s


def parse_answer(data: Any) -> dict:
    """Validate and normalize an agent answer object.

    @param data: parsed JSON
    @returns: normalized answer dict
    """
    if not isinstance(data, dict):
        raise SystemExit('answer must be a JSON object')
    bbox = data.get('bbox_mc')
    if not (isinstance(bbox, list) and len(bbox) == 4):
        raise SystemExit('bbox_mc must be [x0, z0, x1, z1]')
    try:
        bbox_t = tuple(int(v) for v in bbox)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f'bbox_mc must be integers: {exc}') from exc
    rejects = data.get('reject_ways') or []
    if not isinstance(rejects, list):
        raise SystemExit('reject_ways must be a list')
    return {
        'osm_way_id': normalize_way_id(data.get('osm_way_id', '')),
        'reject_ways': [normalize_way_id(r) for r in rejects],
        'bbox_mc': bbox_t,
        'dilated': bool(data.get('dilated', True)),
        'notes': data.get('notes'),
    }


def grade_answer(
    answer: dict,
    key: dict,
    gold_bbox: tuple[int, int, int, int],
) -> dict:
    """Grade a normalized answer against a gold key and vector-map bbox.

    @param answer: normalized answer from ``parse_answer``
    @param key: gold key dict
    @param gold_bbox: inclusive map bbox for the gold way
    @returns: grade report with overall pass/fail
    """
    want = normalize_way_id(key['osm_way_id'])
    require = [normalize_way_id(r) for r in key.get('require_reject') or []]
    min_iou = float(key.get('min_bbox_iou', 0.85))
    max_pad = int(key.get('max_pad', 0))

    way_ok = answer['osm_way_id'] == want
    missing_rejects = [r for r in require if r not in answer['reject_ways']]
    reject_ok = not missing_rejects
    iou = bbox_iou(answer['bbox_mc'], gold_bbox)
    pad = chebyshev_outer_pad(answer['bbox_mc'], gold_bbox)
    bbox_ok = iou >= min_iou and pad <= max_pad
    dilated_ok = answer['dilated'] is False

    checks = {
        'way_id': {'pass': way_ok, 'got': answer['osm_way_id'], 'want': want},
        'reject_ways': {
            'pass': reject_ok,
            'got': answer['reject_ways'],
            'missing': missing_rejects,
            'require': require,
        },
        'bbox': {
            'pass': bbox_ok,
            'iou': round(iou, 4),
            'min_iou': min_iou,
            'outer_pad': pad,
            'max_pad': max_pad,
            'got': list(answer['bbox_mc']),
            'gold': list(gold_bbox),
        },
        'dilated': {'pass': dilated_ok, 'got': answer['dilated'], 'want': False},
    }
    overall = all(c['pass'] for c in checks.values())
    return {'overall': 'pass' if overall else 'fail', 'checks': checks}


def load_answer_file(path: Path) -> dict:
    """Load and parse an answer JSON file (object or fenced extract).

    @param path: answer file
    @returns: normalized answer
    """
    text = path.read_text(encoding='utf-8').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    return parse_answer(json.loads(text))


def cmd_list(root: Path | None) -> int:
    """Print scenario list.

    @param root: checks root override
    @returns: exit code
    """
    for row in list_scenarios(root):
        print(f'{row["id"]}\t{row["title"]}\tcell={row["cell"]}')
    return 0


def cmd_brief(scenario_id: str, root: Path | None) -> int:
    """Print pasteable brief.

    @param scenario_id: FS-NNN
    @param root: checks root override
    @returns: exit code
    """
    sys.stdout.write(format_brief(load_scenario(scenario_id, root)))
    return 0


def cmd_grade(
    scenario_id: str,
    answer_path: Path,
    root: Path | None,
    gold_bbox: tuple[int, int, int, int] | None = None,
) -> int:
    """Grade an answer file against the gold key.

    @param scenario_id: FS-NNN
    @param answer_path: path to agent JSON
    @param root: checks root override
    @param gold_bbox: optional bbox override (tests); else live vector map
    @returns: exit code 0 on pass, 1 on fail
    """
    key = load_key(scenario_id, root)
    answer = load_answer_file(answer_path)
    bbox = gold_bbox if gold_bbox is not None else osm_way_bbox(key['osm_way_id'])
    report = grade_answer(answer, key, bbox)
    print(json.dumps(report, indent=2))
    return 0 if report['overall'] == 'pass' else 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry.

    @param argv: optional argument vector
    @returns: process exit code
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', type=Path, default=None, help='override checks root (tests)')
    sub = ap.add_subparsers(dest='cmd', required=True)

    sub.add_parser('list', help='list scenarios')

    p_brief = sub.add_parser('brief', help='print pasteable agent brief')
    p_brief.add_argument('scenario_id', help='e.g. FS-001')

    p_grade = sub.add_parser('grade', help='grade an answer JSON against the gold key')
    p_grade.add_argument('scenario_id', help='e.g. FS-001')
    p_grade.add_argument('--answer', type=Path, required=True, help='agent answer JSON path')

    args = ap.parse_args(argv)
    root = args.root
    if args.cmd == 'list':
        return cmd_list(root)
    if args.cmd == 'brief':
        return cmd_brief(args.scenario_id, root)
    if args.cmd == 'grade':
        return cmd_grade(args.scenario_id, args.answer, root)
    raise SystemExit(f'unknown command: {args.cmd}')


if __name__ == '__main__':
    sys.exit(main())

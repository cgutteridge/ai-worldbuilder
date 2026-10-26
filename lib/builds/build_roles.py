#!/usr/bin/env python3
"""Default designer / builder model roles for place refine and bake-offs.

Every feature, trees, or decor task must name which model designs and which
builds. Defaults live in ``config/build-roles.json``; callers may override
per task or sitting. Lidar ghosts are mechanical and do not use this module.

Same designer and builder id → one session (both roles still declared).
Different ids → designer writes the hand-off only; builder is sole apply owner.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from script_env import TOOL_ROOT

DEFAULT_PATH = TOOL_ROOT / 'config' / 'build-roles.json'


@dataclass(frozen=True)
class BuildRoles:
    """Resolved designer and builder models for one unit of work.

    @param tool: agent host id (e.g. claude-code, cursor)
    @param designer: model id that writes the evidence table / hand-off
    @param builder: model id that applies (sole SITE_OWNER apply owner)
    """

    tool: str
    designer: str
    builder: str

    @property
    def same_model(self) -> bool:
        """True when one model covers both roles."""
        return self.designer == self.builder

    @property
    def split_sessions(self) -> bool:
        """True when designer and builder must run as separate sessions."""
        return not self.same_model


def load_build_roles(path: Path | None = None) -> dict[str, Any]:
    """Load the build-roles config JSON.

    @param path: optional override path (tests); default ``config/build-roles.json``
    @returns: raw config dict
    @raises FileNotFoundError: when the file is missing
    @raises ValueError: when required keys are missing or empty
    """
    cfg_path = path or DEFAULT_PATH
    data = json.loads(cfg_path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError(f'build-roles config must be an object: {cfg_path}')
    for key in ('tool', 'designer', 'builder'):
        val = data.get(key)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f'build-roles config missing non-empty {key!r}')
    return data


def resolve_roles(
    *,
    designer: str | None = None,
    builder: str | None = None,
    tool: str | None = None,
    config: dict[str, Any] | None = None,
    config_path: Path | None = None,
) -> BuildRoles:
    """Resolve designer/builder/tool with config defaults and overrides.

    When ``tool`` matches the config tool (or tool is omitted), missing
    designer/builder fall back to the config. When ``tool`` differs from the
    config tool and designer is omitted, designer defaults to the builder so
    both roles stay named without pulling another host's model ids.

    @param designer: optional designer override
    @param builder: optional builder override
    @param tool: optional tool override
    @param config: optional pre-loaded config (skips disk)
    @param config_path: optional config path when ``config`` is None
    @returns: resolved roles
    @raises ValueError: when builder cannot be resolved
    """
    cfg = config if config is not None else load_build_roles(config_path)
    cfg_tool = str(cfg['tool']).strip()
    tool_out = (tool or cfg_tool).strip()
    builder_out = (builder or str(cfg.get('builder') or '')).strip()
    if not builder_out:
        raise ValueError('builder model id required (pass builder= or set config)')

    if designer is not None:
        designer_out = designer.strip()
        if not designer_out:
            raise ValueError('designer model id must be non-empty when passed')
    elif tool_out == cfg_tool:
        designer_out = str(cfg['designer']).strip()
    else:
        designer_out = builder_out

    return BuildRoles(tool=tool_out, designer=designer_out, builder=builder_out)


def format_roles_line(roles: BuildRoles) -> str:
    """One-line declaration for prompts and sitting cards.

    @param roles: resolved roles
    @returns: human-readable designer/builder line
    """
    mode = 'one session' if roles.same_model else 'designer then builder'
    return (
        f'tool={roles.tool} designer={roles.designer} '
        f'builder={roles.builder} ({mode})'
    )


def format_roles_block(roles: BuildRoles) -> str:
    """Multi-line role brief for orchestrator / redo agent prompts.

    @param roles: resolved roles
    @returns: prompt fragment naming both roles and the session rule
    """
    lines = [
        f'Build roles: {format_roles_line(roles)}',
        f'- Designer model: {roles.designer} — evidence table + hand-off artefact; do not apply.',
        f'- Builder model: {roles.builder} — sole apply owner (SITE_OWNER); do not redesign from scratch.',
    ]
    if roles.same_model:
        lines.append(
            '- Same model for both roles: one session that still names design then build; '
            'do not skip the evidence table.'
        )
    else:
        lines.append(
            '- Different models: run the designer session first (no apply), record the '
            'hand-off path, then run the builder session to apply only.'
        )
    lines.append(
        '- Hand-off: non-house features → site/builds/<name>.json (feature_build); '
        'trees/decor → evidence table then layer script apply; ghosts are not AI work.'
    )
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI: show defaults or resolve overrides.

    @param argv: optional arg list
    @returns: exit code
    """
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        '--config',
        type=Path,
        default=None,
        help='override config/build-roles.json path',
    )
    sub = ap.add_subparsers(dest='cmd', required=True)

    sub.add_parser('show', help='print default roles from config')

    p_res = sub.add_parser('resolve', help='resolve roles with optional overrides')
    p_res.add_argument('--tool', default=None)
    p_res.add_argument('--designer', default=None)
    p_res.add_argument('--builder', default=None)
    p_res.add_argument(
        '--json',
        action='store_true',
        help='print JSON instead of the one-line declaration',
    )

    args = ap.parse_args(argv)
    if args.cmd == 'show':
        roles = resolve_roles(config_path=args.config)
        print(format_roles_line(roles))
        return 0
    if args.cmd == 'resolve':
        roles = resolve_roles(
            designer=args.designer,
            builder=args.builder,
            tool=args.tool,
            config_path=args.config,
        )
        if args.json:
            payload = asdict(roles)
            payload['same_model'] = roles.same_model
            payload['split_sessions'] = roles.split_sessions
            print(json.dumps(payload, indent=2))
        else:
            print(format_roles_line(roles))
        return 0
    return 2


if __name__ == '__main__':
    sys.exit(main())

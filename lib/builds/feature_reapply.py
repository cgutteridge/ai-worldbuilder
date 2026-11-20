#!/usr/bin/env python3
"""Re-run a feature's recorded or explicitly registered current builder without redesign."""
from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys

from script_env import TOOL_ROOT, add_world_arg, activate_world_from_args
from builder_provenance import describe_builder, resolve_builder_path


def prepare(name: str, registry=None) -> list[str]:
    """Validate recorded provenance and spec before any undo; return builder argv."""
    if registry is None:
        from cell_registry import view_registry
        registry = view_registry()
    builder = describe_builder(name, registry.entries(plan=name))
    if builder['source'] not in ('recorded', 'registered'):
        raise SystemExit(f'{name}: no replayable recorded builder; see the unrecorded listing '
                         'from scripts/builder_audit.py --world <world> (#248). Verify and explicitly '
                         'register a legacy spec with scripts/register_builder.py --world <world>, or use redo '
                         '(redesign from evidence). Nothing undone.')
    return builder_command(name, builder)


def builder_command(name: str, builder: dict) -> list[str]:
    """Validate an explicitly identified builder and return argv without executing it."""
    path = resolve_builder_path(builder['path'])
    if not builder['available']:
        raise SystemExit(f'{name}: recorded {builder["kind"]} missing: {path}; restore or correct the recorded builder before reapply. Nothing undone.')
    if builder['kind'] == 'spec':
        builder_name = builder.get('name') or 'feature_build'
        if builder_name not in ('feature_build', 'house_build'):
            raise SystemExit(f'{name}: recorded builder {builder_name!r} is unavailable or unsupported; refusing undo')
        command = TOOL_ROOT / 'scripts' / f'{builder_name}.py'
        if not command.is_file():
            raise SystemExit(f'{name}: recorded builder missing: {command}; refusing undo')
        if builder_name == 'house_build':
            from house_build import load_spec
        else:
            from feature_build import load_spec
        try:
            spec = load_spec(path)
        except (OSError, ValueError, SystemExit, TypeError, KeyError, AttributeError) as exc:
            raise SystemExit(f'{name}: invalid builder spec {path}: {exc}; nothing undone') from exc
        if spec['name'] != name:
            raise SystemExit(f'{name}: recorded spec names {spec["name"]!r}; refusing undo')
        return [sys.executable, str(command), str(path)]
    if path.suffix != '.py':
        raise SystemExit(f'{name}: recorded script is not a Python file; refusing undo')
    try:
        source = ast.parse(path.read_text(), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise SystemExit(f'{name}: invalid builder script {path}: {exc}; refusing undo') from exc
    # Supported scripts declare one literal NAME at module scope. Never execute
    # a builder to discover identity, or guess at dynamic/conditional identities.
    declarations = [node for node in source.body if isinstance(node, (ast.Assign, ast.AnnAssign))
                    and any(isinstance(target, ast.Name) and target.id == 'NAME'
                            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]))]
    writes = [node for node in ast.walk(source)
              if isinstance(node, ast.Name) and node.id == 'NAME' and isinstance(node.ctx, ast.Store)]
    if (len(declarations) != 1 or len(writes) != 1
            or not isinstance(declarations[0].value, ast.Constant)
            or not isinstance(declarations[0].value.value, str)):
        raise SystemExit(f'{name}: recorded script {path} has no supported literal NAME; refusing undo')
    script_name = declarations[0].value.value
    if script_name != name:
        raise SystemExit(f'{name}: recorded script names {script_name!r}; refusing undo')
    return [sys.executable, str(path)]


def main(argv=None) -> int:
    """Preflight and run the builder, optionally undoing first under one world lock."""
    ap = argparse.ArgumentParser(description=__doc__)
    add_world_arg(ap)
    ap.add_argument('feature')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--owner', default=f'reapply-{os.getpid()}')
    ap.add_argument('--locked', action='store_true', help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    activate_world_from_args(args)
    if args.apply and not args.locked:
        import site_lock
        return site_lock.run(args.owner, f'reapply {args.feature}', [
            sys.executable, str(TOOL_ROOT / 'scripts/reapply_feature.py'), '--world', args.world,
            args.feature, '--apply', '--locked', '--owner', args.owner])
    stage = 'preflight'
    undone = False
    try:
        if args.apply:
            import site_lock
            site_lock.check()
            from world_discovery import require_running_world
            require_running_world()
        cmd = prepare(args.feature) + ['--world', args.world]
        # Full builder dry run happens before undo. Invalid/missing evidence or
        # a changed schema must not clear an existing feature.
        stage = 'builder validation'
        subprocess.run(cmd, cwd=TOOL_ROOT, check=True)
        if args.apply:
            from readiness_evidence import require_feature_ready
            require_feature_ready(args.feature)
            stage = 'undo'
            subprocess.run([sys.executable, str(TOOL_ROOT / 'scripts/element_undo.py'),
                            '--world', args.world, args.feature, '--apply'], cwd=TOOL_ROOT, check=True)
            undone = True
            stage = 'rebuild'
            from cell_registry import view_registry
            registry = view_registry()
            before_seq = max((e.seq for e in registry.entries()), default=0)
            subprocess.run(cmd + ['--apply'], cwd=TOOL_ROOT, check=True)
            entries = registry.entries()
            if any(e.status == 'begun' for e in entries):
                raise RuntimeError('builder left an in-flight journal entry')
            builds = [e for e in entries if e.seq > before_seq and e.plan == args.feature
                      and e.kind == 'build' and e.pass_ == 'feature']
            if not builds or any(e.status != 'landed' or e.undone or e.mismatches for e in builds):
                raise RuntimeError('builder did not land a new feature entry with zero mismatches')
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError, SystemExit, KeyboardInterrupt) as exc:
        if undone:
            advice = ('Undo completed; rebuild did not complete cleanly. The feature may remain undone or partly rebuilt. '
                      'Inspect site_recover status for an interrupted write, or reconcile for drift; no rollback attempted.')
        elif stage == 'undo':
            advice = ('Undo did not complete cleanly; inspect site_recover status and the journal before retrying. '
                      'No rebuild attempted.')
        else:
            advice = 'No undo attempted; fix the reported validation problem before retrying.'
        print(f'reapply {args.feature}: stopped during {stage}: {exc}. {advice}', file=sys.stderr)
        return 130 if isinstance(exc, KeyboardInterrupt) else 1


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Re-run a feature's recorded or explicitly registered current builder without redesign."""
from __future__ import annotations

import argparse
import ast
import os
import shlex
import subprocess
import sys
from pathlib import Path

from script_env import TOOL_ROOT, add_world_arg, activate_world_from_args
from builder_provenance import BUILDER_NAMES, FEATURE_BUILDER, HOUSE_BUILDER, describe_builder, resolve_builder_path


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
        builder_name = builder.get('name') or FEATURE_BUILDER
        if builder_name not in BUILDER_NAMES:
            raise SystemExit(f'{name}: recorded builder {builder_name!r} is unavailable or unsupported; refusing undo')
        command = TOOL_ROOT / 'scripts' / f'{builder_name}.py'
        if not command.is_file():
            raise SystemExit(f'{name}: recorded builder missing: {command}; refusing undo')
        if builder_name == 'railway_build':
            from railway_feature import load_spec
        elif builder_name == 'crossing_build':
            from crossing_feature import load_spec
        elif builder_name == HOUSE_BUILDER:
            from house_build import load_spec
        else:
            from feature_spec import load_spec
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


def relay_command(world: str, owner: str, feature: str, cmd: list[str]) -> str:
    """The exact shell line that re-lays ``feature`` under a fresh world lock.

    @param world: world directory name
    @param owner: lock owner to suggest
    @param feature: feature plan name
    @param cmd: builder argv without ``--apply`` (as returned by :func:`prepare` plus ``--world``)
    @returns: one copy-pasteable command
    """
    def short(part):
        if part == sys.executable:
            return 'python3'
        try:
            return str(Path(part).relative_to(TOOL_ROOT))
        except ValueError:
            return part
    builder = shlex.join([short(part) for part in cmd] + ['--apply'])
    return (f'python3 scripts/site_lock.py --world {shlex.quote(world)} run {shlex.quote(owner)} '
            f'{shlex.quote("re-lay " + feature)} -- {builder}')


def _hold_lock(owner, feature):
    """Keep the sequence's world lock: refresh it, or take it again if it lapsed.

    @param owner: lock owner given to the locked worker (``--owner``, or SITE_OWNER)
    @param feature: feature plan name, for the claim text
    @raises SystemExit: another owner holds a fresh lock
    """
    import site_lock
    owner = os.environ.get('SITE_OWNER') or owner
    if site_lock.hold(owner, f'reapply {feature}') == 'reclaimed':
        print(f'reapply {feature}: the world lock had lapsed; claimed it again for {owner}', file=sys.stderr)


def main(argv=None) -> int:
    """Preflight and run the builder, optionally undoing first under one world lock."""
    ap = argparse.ArgumentParser(description=__doc__)
    add_world_arg(ap)
    ap.add_argument('feature')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--owner', default=None,
                    help='lock owner name; the lock itself is taken as <owner>-<pid> so another session '
                         'using the same name cannot release or replace it mid-sequence')
    ap.add_argument('--locked', action='store_true', help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    activate_world_from_args(args)
    base_owner = args.owner or 'reapply'
    if args.apply and not args.locked:
        import site_lock
        lock_owner = f'{base_owner}-{os.getpid()}'
        return site_lock.run(lock_owner, f'reapply {args.feature}', [
            sys.executable, str(TOOL_ROOT / 'scripts/reapply_feature.py'), '--world', args.world,
            args.feature, '--apply', '--locked', '--owner', lock_owner])
    stage = 'preflight'
    undone = False
    cmd = None
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
            stage = 'registry readiness gate (the feature cell)'
            require_feature_ready(args.feature)
            # The same gate element_undo applies to every touched cell, run now so that a blocked
            # neighbouring cell (needs-prep, feature plans unaccounted) refuses before anything is undone.
            from element_undo import require_rebuild_ready
            stage = 'registry readiness gate (every touched cell, as undo requires)'
            require_rebuild_ready(args.feature)
            stage = 'undo'
            _hold_lock(args.owner, args.feature)
            subprocess.run([sys.executable, str(TOOL_ROOT / 'scripts/element_undo.py'),
                            '--world', args.world, args.feature, '--apply'], cwd=TOOL_ROOT, check=True)
            undone = True
            stage = 'rebuild'
            # The world lock can lapse during the undo; take it again before the rebuild runs, or
            # stop with the recovery command rather than let the builder fail on it.
            _hold_lock(args.owner, args.feature)
            from cell_registry import view_registry
            registry = view_registry()
            before_seq = max((e.seq for e in registry.entries()), default=0)
            subprocess.run(cmd + ['--apply'], cwd=TOOL_ROOT, check=True)
            entries = registry.entries()
            # Only this feature, this run's owner, and seqs the rebuild itself wrote.
            # Another session's begun entry (a different plan, owner, or an older seq)
            # is information: it must not fail a rebuild that landed cleanly (#467).
            run_owner = os.environ.get('SITE_OWNER') or args.owner
            ours = [e for e in entries
                    if e.status == 'begun' and e.plan == args.feature
                    and e.owner == run_owner and e.seq > before_seq]
            if ours:
                raise RuntimeError('builder left an in-flight journal entry')
            builds = [e for e in entries if e.seq > before_seq and e.plan == args.feature
                      and e.kind == 'build' and e.pass_ == 'feature']
            if not builds or any(e.status != 'landed' or e.undone or e.mismatches for e in builds):
                raise RuntimeError('builder did not land a new feature entry with zero mismatches')
            for e in entries:
                if e.status != 'begun':
                    continue
                print(f'reapply {args.feature}: another session has an in-flight journal entry '
                      f'(cell {e.cell[0]},{e.cell[1]} stage {e.pass_} plan {e.plan} '
                      f'owner {e.owner} since {e.ts}); this rebuild is unaffected',
                      file=sys.stderr)
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError, SystemExit, KeyboardInterrupt) as exc:
        if undone:
            advice = ('Undo completed; rebuild did not complete cleanly. The feature may remain undone or partly rebuilt. '
                      'Inspect site_recover status for an interrupted write, or reconcile for drift; no rollback attempted. '
                      f'To re-lay it (the plot is left undone until you do), run: '
                      f'{relay_command(args.world, base_owner, args.feature, cmd)}')
        elif stage == 'undo':
            advice = ('Undo did not complete cleanly; inspect site_recover status and the journal before retrying. '
                      'No rebuild attempted.')
        else:
            advice = 'No undo attempted; fix the reported validation problem before retrying.'
        print(f'reapply {args.feature}: stopped during {stage}: {exc}. {advice}', file=sys.stderr)
        return 130 if isinstance(exc, KeyboardInterrupt) else 1


if __name__ == '__main__':
    raise SystemExit(main())

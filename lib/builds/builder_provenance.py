"""Structured feature builders, resolved against the explicit active world."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from script_env import TOOL_ROOT

HOUSE_BUILDER = 'house_build'
FEATURE_BUILDER = 'feature_build'
RAILWAY_BUILDER = 'railway_build'
CROSSING_BUILDER = 'crossing_build'
BUILDER_NAMES = (HOUSE_BUILDER, FEATURE_BUILDER, RAILWAY_BUILDER, CROSSING_BUILDER)    # the spec builders a registration or reapply may name


def resolve_builder_path(path: str | Path) -> Path:
    """Absolute paths stay absolute; relative builder paths belong to the world."""
    from active_world import world_root
    path = Path(path).expanduser()
    return (path if path.is_absolute() else world_root() / path).resolve()


def builder_record(kind: str, path: str | Path) -> dict[str, str]:
    """Represent paths within this world portably, retaining external absolute paths."""
    from active_world import world_root
    resolved = resolve_builder_path(path)
    try:
        stored = str(resolved.relative_to(world_root().resolve()))
    except ValueError:
        stored = str(resolved)
    return {'kind': kind, 'path': stored}


def script_builder(plan_name: str) -> dict | None:
    """Record only an actual world build_*.py entry point declaring this NAME."""
    from active_world import world_root
    main = sys.modules.get('__main__')
    if getattr(main, 'NAME', None) != plan_name:
        return None
    path = Path(getattr(main, '__file__', '')).resolve()
    if (path.is_file() and path.name.startswith('build_') and path.suffix == '.py'
            and path.is_relative_to(world_root().resolve())):
        return builder_record('script', path)
    return None


def describe_builder(name: str, entries, inferred: Path | None = None) -> dict:
    """Use the latest landed builder; distinguish explicit registration from build-time records."""
    for entry in reversed(entries):
        if entry.plan == name and entry.kind == 'build' and entry.status == 'landed' and not entry.undone:
            record = entry.meta.get('builder')
            builder_name = entry.meta.get('builder_name')
            if builder_name and entry.meta.get('spec_path'):
                record = {'kind': 'spec', 'path': entry.meta['spec_path']}
            if (isinstance(record, dict) and record.get('kind') in ('spec', 'script')
                    and isinstance(record.get('path'), str) and record['path']):
                return {**record, 'name': builder_name, 'source': 'recorded',
                        'available': resolve_builder_path(record['path']).is_file()}
            registration = entry.meta.get('builder_registration')
            if registration:
                record = registration['builder']
                return {**record, 'source': 'registered', 'registration': registration,
                        'available': resolve_builder_path(record['path']).is_file()}
            break
    if inferred:
        return {**builder_record('script', inferred), 'source': 'inferred', 'available': inferred.is_file()}
    return {'kind': None, 'path': None, 'source': 'unknown', 'available': False}


def spec_hash(path: str | Path) -> str:
    """Stable sha256 of the spec JSON as the builder read it (key order and whitespace ignored)."""
    spec = json.loads(resolve_builder_path(path).read_text())
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def tool_sha(root: Path = TOOL_ROOT) -> str | None:
    """Git HEAD of the tool checkout (the builder code that runs now); ``+dirty`` if ``lib/`` has local edits."""
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=20)
    try:
        head = git('rev-parse', 'HEAD')
        if head.returncode:
            return None
        dirty = git('status', '--porcelain', '--', 'lib')
    except (OSError, subprocess.SubprocessError):
        return None
    return head.stdout.strip() + ('+dirty' if dirty.stdout.strip() else '')


def provenance(builder_name: str, spec_path: str | Path) -> dict:
    """Journal meta recording who built a feature plan: builder name, builder git sha, spec hash/path.

    Fields that cannot be determined are left out, never invented (the plan then lists as unrecorded).
    """
    out: dict = {'builder_name': builder_name, 'spec_path': builder_record('spec', spec_path)['path']}
    try:
        out['spec_hash'] = spec_hash(spec_path)
    except (OSError, ValueError):
        pass
    sha = tool_sha()
    if sha:
        out['builder_sha'] = sha
    return out


def provenance_status(meta, current_spec_hash: str | None = None, since_ancestor=None) -> dict:
    """Classify one journal entry's recorded provenance.

    @param meta: entry meta
    @param current_spec_hash: hash of the spec file now (None if unreadable)
    @param since_ancestor: callable(sha) -> True if the recorded sha is strictly older than ``--since``
    @returns: {'state': 'unrecorded'|'current'|'stale', 'reasons': [...]}
    """
    sha, recorded = meta.get('builder_sha'), meta.get('spec_hash')
    if not sha or not recorded or not meta.get('builder_name'):
        return {'state': 'unrecorded', 'reasons': []}
    reasons = []
    if since_ancestor is not None and since_ancestor(sha.split('+')[0]):
        reasons.append('builder-older')
    if current_spec_hash is not None and current_spec_hash != recorded:
        reasons.append('spec-changed')
    return {'state': 'stale' if reasons else 'current', 'reasons': reasons}

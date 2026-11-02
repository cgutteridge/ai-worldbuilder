"""Structured feature builders, resolved against the explicit active world."""
from __future__ import annotations

import sys
from pathlib import Path


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
    """Use the latest landed builder; label legacy filename matches as inferred."""
    for entry in reversed(entries):
        if entry.plan == name and entry.kind == 'build' and entry.status == 'landed' and not entry.undone:
            record = entry.meta.get('builder')
            if (isinstance(record, dict) and record.get('kind') in ('spec', 'script')
                    and isinstance(record.get('path'), str) and record['path']):
                return {**record, 'source': 'recorded',
                        'available': resolve_builder_path(record['path']).is_file()}
            break
    if inferred:
        return {**builder_record('script', inferred), 'source': 'inferred', 'available': inferred.is_file()}
    return {'kind': None, 'path': None, 'source': 'unknown', 'available': False}

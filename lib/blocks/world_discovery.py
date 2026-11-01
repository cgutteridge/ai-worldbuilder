"""Read-only identification of the local MCP process's open save.

The bridge does not expose a save path. lsof ties the fixed bridge listener to
its open session.lock, rather than guessing from stale lock files or world names.
Discovery fails closed when process visibility/lsof is unavailable.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


class DiscoveryError(RuntimeError):
    """The running save or matching checkout cannot be established uniquely."""


def _lsof(*args: str) -> str:
    try:
        result = subprocess.run(['lsof', '-nP', *args], text=True, capture_output=True,
                                timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DiscoveryError(f'cannot inspect the local MCP process (lsof required): {exc}') from exc
    if result.returncode not in (0, 1) or result.stderr.strip():
        raise DiscoveryError(f'cannot inspect the local MCP process: {result.stderr.strip()}')
    return result.stdout


def running_save() -> Path:
    """Return the unique save opened by the process listening on world port 8765."""
    listeners = {line[1:] for line in _lsof('-a', '-iTCP:8765', '-sTCP:LISTEN', '-Fp').splitlines()
                 if line.startswith('p') and line[1:].isdigit()}
    if len(listeners) != 1:
        raise DiscoveryError('MCP server unavailable or ambiguous on local port 8765')
    pid = next(iter(listeners))
    paths = {Path(line[1:]).resolve().parent
             for line in _lsof('-a', '-p', pid, '-Fn').splitlines()
             if line.startswith('n') and Path(line[1:]).name == 'session.lock'}
    # Check listener again to reject a process/world transition during inspection.
    current = {line[1:] for line in _lsof('-a', '-iTCP:8765', '-sTCP:LISTEN', '-Fp').splitlines()
               if line.startswith('p') and line[1:].isdigit()}
    if current != listeners or len(paths) != 1:
        raise DiscoveryError('running save identity unknown or ambiguous; no unique open session.lock')
    return next(iter(paths))


def matching_world(save: Path, worlds: Path) -> dict[str, str]:
    """Match a resolved save against checkout configs without selecting a world."""
    from active_world import resolve_save
    matches = []
    for checkout in sorted(worlds.iterdir()) if worlds.is_dir() else []:
        if not checkout.is_dir() or checkout.name.startswith('.'):
            continue
        path = checkout / 'config/world.json'
        if not path.exists():
            continue
        try:
            cfg = json.loads(path.read_text())
            raw = cfg.get('save_path')
            if not isinstance(raw, str) or not raw:
                raise ValueError('missing or invalid save_path')
            candidate = Path(raw).expanduser()
            # Give resolve_save an absolute path so discovery cannot alter global selection.
            resolved = resolve_save(candidate if candidate.is_absolute() else checkout / candidate, True).resolve()
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise DiscoveryError(f'invalid world config {path}: {exc}') from exc
        if resolved == save.resolve():
            matches.append({'world': checkout.name, 'save_path': str(resolved)})
    if len(matches) != 1:
        raise DiscoveryError(f'{len(matches)} matching world checkouts for running save {save}; expected exactly one')
    return matches[0]


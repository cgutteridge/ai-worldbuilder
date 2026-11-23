"""Place identity from the active world's ``config/world.json``.

Plan names, retired ground passes, reverted builds, the stone-fill floor,
extra ledger aliases, fetch hosts and queue keywords are place data. A world
that omits a key gets a neutral default, so one place's names are not baked
into the tool.
"""
from __future__ import annotations

import json
from pathlib import Path

# Stone fill under a floor when ``base_y`` is absent. Worlds that were built
# before the key existed keep this floor until they set it.
DEFAULT_BASE_Y = 37
DEFAULT_PLAN_PREFIX = 'cell'
DEFAULT_STRUCTURE_NAMESPACE = 'worldbuilder'

# Legacy ledger keys for system providers. Place providers belong in world config.
_SYSTEM_LEDGER_ALIASES: dict[str, tuple[str, ...]] = {
    'google-satellite': ('google',),
    'google-streetview': ('streetview',),
}

_CACHE: dict | None = None


def clear_place_config() -> None:
    """Drop the cached ``world.json`` so the next read reloads."""
    global _CACHE
    _CACHE = None


def world_config() -> dict:
    """The active world's ``config/world.json``, or ``{}`` when none is selected.

    @returns: parsed object (empty when unbound or unreadable)
    """
    global _CACHE
    if _CACHE is None:
        _CACHE = _read_world_config()
    return _CACHE


def _read_world_config() -> dict:
    """Load ``config/world.json`` for the selected world.

    @returns: a dict, or ``{}`` when there is no readable config
    """
    try:
        from script_env import world_bound
        if not world_bound():
            return {}
        from active_world import world_root
        path = Path(world_root()) / 'config' / 'world.json'
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, RuntimeError, ImportError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _text(key: str, default: str) -> str:
    """One non-empty string from world config.

    @param key: ``world.json`` field
    @param default: used when the field is missing or blank
    @returns: stripped text
    """
    raw = world_config().get(key, default)
    text = str(raw).strip() if raw is not None else ''
    return text or default


def _name_list(key: str) -> frozenset[str]:
    """A list of strings from world config, or an empty set.

    @param key: ``world.json`` field
    @returns: names
    """
    raw = world_config().get(key) or []
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(name) for name in raw)


def plan_prefix() -> str:
    """Plan-name prefix from ``plan_prefix``.

    @returns: prefix without a trailing underscore (default ``cell``)
    """
    return _text('plan_prefix', DEFAULT_PLAN_PREFIX)


def plan_name(cell: tuple[int, int], layer: str) -> str:
    """Build name for one cell layer, ``<prefix>_<tx>_<tz>_<layer>``.

    Negative tz is written with an ``m`` in place of the minus, matching
    cell-marker file names (``48_m44``).

    @param cell: ``(tx, tz)``
    @param layer: layer or pass suffix (``ground``, ``trees``, ``decor``, ``trees_undo``)
    @returns: plan name
    """
    tx, tz = cell
    zed = f'm{abs(tz)}' if tz < 0 else str(tz)
    return f'{plan_prefix()}_{tx}_{zed}_{layer}'


def grounds_prefix() -> str:
    """Prefix of grounds-marker plans that are not feature owners.

    @returns: ``<plan_prefix>_grounds_``
    """
    return f'{plan_prefix()}_grounds_'


def retired_ground_passes() -> frozenset[str]:
    """Plan names that are not owners for a new ground pass.

    @returns: names from ``retired_ground_passes``, or an empty set
    """
    return _name_list('retired_ground_passes')


def reverted_builds() -> frozenset[str]:
    """Builds whose columns are omitted from touched-column evidence.

    @returns: names from ``reverted_builds``, or an empty set
    """
    return _name_list('reverted_builds')


def base_y() -> int:
    """Y where builders start the stone fill under a floor.

    @returns: ``base_y`` from world config, or :data:`DEFAULT_BASE_Y`
    """
    raw = world_config().get('base_y', DEFAULT_BASE_Y)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_BASE_Y


def structure_namespace() -> str:
    """Structure template namespace from world config.

    @returns: ``structure_namespace``, or :data:`DEFAULT_STRUCTURE_NAMESPACE`
    """
    return _text('structure_namespace', DEFAULT_STRUCTURE_NAMESPACE)


def ledger_aliases() -> dict[str, tuple[str, ...]]:
    """Attempt-ledger keys that count as the same provider.

    System aliases are always included. ``ledger_aliases`` in world config
    adds place providers. Each value is a list of old keys.

    @returns: provider id → old ledger keys
    """
    out = {key: names for key, names in _SYSTEM_LEDGER_ALIASES.items()}
    raw = world_config().get('ledger_aliases') or {}
    if isinstance(raw, dict):
        for key, names in raw.items():
            if isinstance(names, list):
                out[str(key)] = tuple(str(name) for name in names)
    return out


def extra_fetch_hosts() -> frozenset[str]:
    """Place-specific hosts the ref-fetch sidecar may call, from ``fetch_hosts``.

    @returns: hostnames, lowercased
    """
    raw = world_config().get('fetch_hosts') or []
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(host).strip().lower() for host in raw if str(host).strip())


def fetch_probe() -> dict | None:
    """Connectivity probe from ``fetch_probe``, when the world configures one.

    @returns: ``{'url', 'referer'}``, or None
    """
    raw = world_config().get('fetch_probe')
    if not isinstance(raw, dict):
        return None
    url = str(raw.get('url') or '').strip()
    if not url:
        return None
    return {'url': url, 'referer': str(raw.get('referer') or '')}

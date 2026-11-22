#!/usr/bin/env python3
"""Type-facing access to the active world's lidar provider.

Callers ask for **lidar** (composite world, tile client, helpers). Catalogue
rows with ``types: ["lidar"]`` and ``modules`` entrypoints choose the
package; vendor module filenames stay under ``providers/`` and catalogues.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from cell_sources import load_place_sources, provider_package_dir  # noqa: E402

_MODULE_CACHE: dict[str, ModuleType] = {}


class NoLidarProviderError(RuntimeError):
    """No catalogue row supplies lidar; distinct from an uncached tile LookupError."""


def lidar_provider_row(data: dict | None = None) -> dict:
    """Return the first world lidar row, falling back to the system catalogue.

    @param data: optional place-sources dict
    @returns: provider row dict
    @raises NoLidarProviderError: when no provider lists type ``lidar``
    """
    data = load_place_sources() if data is None else data
    rows = [row for row in data.get('providers') or [] if 'lidar' in (row.get('types') or [])]
    for row in rows:
        if row.get('home') == 'world':
            return row
    if rows:
        return rows[0]
    raise NoLidarProviderError('no catalogue provider lists type "lidar"; install a lidar provider')


def _module_rel(role: str, row: dict) -> str:
    modules = row.get('modules') or {}
    if role not in modules:
        raise ValueError(
            f'lidar provider {row.get("id")!r} has no modules.{role} entrypoint'
        )
    return str(modules[role])


def load_lidar_module(role: str, data: dict | None = None) -> ModuleType:
    """Load a lidar entry module by role (world, tiles, query or extremes).

    Uses the shared shim loader for package subdirectories, caching by resolved
    file path so historical system shims and type-facing callers share state.

    @param role: key under the provider's ``modules`` map
    @param data: optional place-sources dict
    @returns: loaded Python module
    """
    row = lidar_provider_row(data)
    rel = _module_rel(role, row)
    # Resolve the chosen row itself: a world can override a system row's id too.
    pkg = provider_package_dir(row['id'], {'providers': [row]})
    if pkg is None:
        raise FileNotFoundError(
            f'lidar provider package missing for {row.get("id")!r}'
        )
    path = (pkg / f'{rel}.py').resolve()
    cache_key = str(path)
    if cache_key in _MODULE_CACHE:
        return _MODULE_CACHE[cache_key]
    parts = rel.split('/', 1)
    if len(parts) == 2:
        from _provider_shim import load_provider
        mod = load_provider(str(row['id']), parts[0], parts[1], package_dir=pkg)
        _MODULE_CACHE[cache_key] = mod
        return mod
    if not path.is_file():
        raise FileNotFoundError(f'lidar entry module not found: {path}')
    stem = Path(rel).name
    existing = sys.modules.get(stem)
    if existing is not None and getattr(existing, '__file__', None) == str(path):
        _MODULE_CACHE[cache_key] = existing
        return existing
    mod_name = (
        f"_lidar_provider.{row.get('id')}.{rel}".replace('-', '_').replace('/', '.')
    )
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f'cannot load lidar module: {path}')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    if stem not in sys.modules:
        sys.modules[stem] = mod
    spec.loader.exec_module(mod)
    _MODULE_CACHE[cache_key] = mod
    return mod


def composite_world(**kwargs: Any):
    """Open the composite lidar world for the active lidar provider.

    The configured world module exports the type-facing ``LidarWorld`` class.

    @param kwargs: forwarded to the world constructor
    @returns: world instance with ``covers`` / ``heightmap`` / ``block``
    """
    mod = load_lidar_module('world')
    return mod.LidarWorld(**kwargs)


def tile_client(*, download: bool = False, **kwargs: Any):
    """Open a lidar tile client (DTM/DSM cache) for the active provider.

    @param download: whether missing tiles may be fetched
    @param kwargs: forwarded to the client constructor
    @returns: tile client instance
    """
    mod = load_lidar_module('tiles')
    return mod.LidarTiles(download=download, **kwargs)


def mc_box_en(*args: Any, **kwargs: Any):
    """Minecraft bbox → easting/northing box via the active tiles module.

    @returns: ``(e0, n0, e1, n1)``
    """
    return load_lidar_module('tiles').mc_box_en(*args, **kwargs)


def mc_heights(*args: Any, **kwargs: Any):
    """Sample composite heights for a Minecraft bbox via the active tiles module.

    @returns: height dict from the provider helper
    """
    return load_lidar_module('tiles').mc_heights(*args, **kwargs)


_FLOAT_FALLBACK_WARNED = False


def _accepts_geocraft(fn: Any) -> bool:
    """True when ``fn`` takes a ``geocraft`` keyword (the provider's float option)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return 'geocraft' in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


def mc_heights_float(*args: Any, **kwargs: Any):
    """Like ``mc_heights`` but asks the provider for its float option.

    The provider's default rounds every DSM/DTM sample to a whole metre, so a
    roof that rises half a block never shows up. This wrapper asks for the
    provider's float option (unrounded heights, NaN for no-data). The provider
    signature is checked up front, so a ``TypeError`` raised inside the provider
    is never mistaken for a missing option. A provider with no such option falls
    back to the whole-metre grid (one warning on stderr, once per run) so
    callers keep working. A caller that passes its own ``geocraft`` keeps it.

    @returns: height dict; ``dsm`` / ``dtm`` are float (NaN = no data) when the
        provider has the float option, whole-metre integers otherwise
    """
    global _FLOAT_FALLBACK_WARNED
    fn = load_lidar_module('tiles').mc_heights
    if 'geocraft' in kwargs or _accepts_geocraft(fn):
        kwargs.setdefault('geocraft', False)
        return fn(*args, **kwargs)
    if not _FLOAT_FALLBACK_WARNED:
        _FLOAT_FALLBACK_WARNED = True
        print(
            'lidar_provider: provider mc_heights has no float option; '
            'using whole-metre heights (roof half-steps will not show)',
            file=sys.stderr,
        )
    return fn(*args, **kwargs)


def default_heights(x0: int, z0: int, x1: int, z1: int) -> dict[str, Any]:
    """Sample cached provider heights for an inclusive Minecraft block box.

    @returns: ``mc_heights`` dict (ground_y, feature/diff, dtm, dsm, top_y)
    """
    client = tile_client(download=False)
    client.ensure(*mc_box_en(x0, z0, x1, z1))
    return mc_heights(client, x0, z0, x1, z1)

#!/usr/bin/env python3
"""Type-facing access to the active world's lidar provider.

Callers ask for **lidar** (composite world, tile client, helpers). Catalogue
rows with ``types: ["lidar"]`` and optional ``modules`` entrypoints choose the
package; vendor module filenames stay under ``providers/`` and catalogues.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from cell_sources import load_place_sources, provider_package_dir  # noqa: E402

_MODULE_CACHE: dict[str, ModuleType] = {}


def lidar_provider_row(data: dict | None = None) -> dict:
    """Return the first world lidar row, falling back to the system catalogue.

    @param data: optional place-sources dict
    @returns: provider row dict
    @raises LookupError: when no provider lists type ``lidar``
    """
    data = load_place_sources() if data is None else data
    rows = [row for row in data.get('providers') or [] if 'lidar' in (row.get('types') or [])]
    for row in rows:
        if row.get('home') == 'world':
            return row
    if rows:
        return rows[0]
    raise LookupError('no catalogue provider lists type "lidar"')


def _module_rel(role: str, row: dict) -> str:
    modules = row.get('modules') or {}
    if role not in modules:
        raise LookupError(
            f'lidar provider {row.get("id")!r} has no modules.{role} entrypoint'
        )
    return str(modules[role])


def load_lidar_module(role: str, data: dict | None = None) -> ModuleType:
    """Load a lidar entry module by role (world, tiles, query or extremes).

    Prefers the shared shim loader (one ``sys.modules`` object per historical
    name) so provider-internal ``import <tiles>`` and callers share state.

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


def _first_attr(mod: ModuleType, names: tuple[str, ...]) -> Any:
    for name in names:
        if hasattr(mod, name):
            return getattr(mod, name)
    raise AttributeError(
        f'module {getattr(mod, "__name__", mod)!r} has none of {names}'
    )


def composite_world(**kwargs: Any):
    """Open the composite lidar world for the active lidar provider.

    Prefer a type-facing class name (``LidarWorld`` / ``CompositeWorld``); fall
    back to the historical class still exported by the provider package.

    @param kwargs: forwarded to the world constructor
    @returns: world instance with ``covers`` / ``heightmap`` / ``block``
    """
    mod = load_lidar_module('world')
    cls = _first_attr(mod, ('LidarWorld', 'CompositeWorld', 'DefraWorld'))
    return cls(**kwargs)


def tile_client(*, download: bool = False, **kwargs: Any):
    """Open a lidar tile client (DTM/DSM cache) for the active provider.

    @param download: whether missing tiles may be fetched
    @param kwargs: forwarded to the client constructor
    @returns: tile client instance
    """
    mod = load_lidar_module('tiles')
    cls = _first_attr(mod, ('LidarTiles', 'DefraLidar'))
    return cls(download=download, **kwargs)


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


def default_heights(x0: int, z0: int, x1: int, z1: int) -> dict[str, Any]:
    """Sample cached provider heights for an inclusive Minecraft block box.

    @returns: ``mc_heights`` dict (ground_y, feature/diff, dtm, dsm, top_y)
    """
    client = tile_client(download=False)
    client.ensure(*mc_box_en(x0, z0, x1, z1))
    return mc_heights(client, x0, z0, x1, z1)

"""Resolve optional place-evidence providers by catalogue role.

Rows in the merged system/world catalogue select a ``role`` independently of
provider id or evidence type. The stable roles used by the shims are
``entrance_register``, ``building_register``, ``tree_preservation`` and
``aerial_mosaic``. A configured role must select exactly one packaged provider;
its ``home`` determines the root. Optional callers may omit an unconfigured
role, but broken or ambiguous configured providers still raise errors.
"""
from __future__ import annotations

import importlib.util
import runpy
import sys
from pathlib import Path
from types import ModuleType

from cell_sources import load_place_sources, provider_package_dir


class MissingProviderError(ImportError):
    """An expected local provider package or module is absent, rather than broken."""


def _missing(message: str) -> MissingProviderError:
    from active_world import world_root
    return MissingProviderError(
        f"{message} (world {world_root().name!r}; inspect config/local-providers.json)")


def world_provider_module_path(role: str, module_file: str, *, optional: bool = False) -> Path | None:
    """Resolve a unique catalogue role to its module, respecting package home.

    @param role: stable catalogue ``role``, independent of provider id/type
    @param module_file: Python filename inside the package
    @param optional: return None only when no row configures this role
    @returns: absolute module path, or None for an unconfigured optional role
    """
    data = load_place_sources()
    rows = [row for row in data.get('providers', []) if row.get('role') == role]
    if not rows and optional:
        return None
    if not rows:
        raise _missing(f"provider role missing: {role!r}")
    if len(rows) != 1:
        raise ValueError(f"Configure exactly one provider for role {role!r}; found {len(rows)}")
    row = rows[0]
    package = provider_package_dir(row['id'], data)
    if package is None:
        raise _missing(f"local provider package missing for role {role!r}: {row.get('package')!r}")
    path = package / module_file
    if not path.is_file():
        raise _missing(f"local provider module missing for role {role!r}: {path}")
    return path


def load_world_provider(role: str, module_file: str, module_name: str,
                        *, optional: bool = False) -> ModuleType | None:
    """Load a provider by role; optional absence is distinct from provider bugs.

    @param role: catalogue role selecting the provider
    @param module_file: filename inside the package directory
    @param module_name: sys.modules key, normally the shim stem
    @param optional: return None only for an unconfigured role
    @returns: loaded module, or None for an unconfigured optional role
    """
    path = world_provider_module_path(role, module_file, optional=optional)
    if path is None:
        return None
    existing = sys.modules.get(module_name)
    if existing is not None and Path(getattr(existing, "__file__", "") or "").resolve() == path:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load provider module: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        # A failed import must not leave a partially initialised provider cached.
        if existing is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = existing
        raise
    return mod


def run_world_provider(role: str, module_file: str) -> None:
    """Execute a required provider role as a CLI, reporting known absence concisely.

    @param role: catalogue role selecting the provider
    @param module_file: filename inside the package directory
    """
    try:
        path = world_provider_module_path(role, module_file)
    except MissingProviderError as exc:
        raise SystemExit(str(exc)) from exc
    sys.argv[0] = str(path)
    runpy.run_path(str(path), run_name="__main__")

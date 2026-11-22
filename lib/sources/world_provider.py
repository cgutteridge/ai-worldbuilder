"""Load place-local provider modules from the active world's ``providers/``.

Local catalogue entries use a ``package`` field (path relative to the world
root, e.g. ``providers/uos-portals``). Catalogue-aware dual-root resolution
lives in ``cell_sources.provider_package_dir`` /
``script_env.resolve_provider_package`` (system + world). Thin ``scripts/``
shims call :func:`load_world_provider` so ``import portals`` and
``python3 shims/portals.py`` keep working for world-home packages.
"""
from __future__ import annotations

import importlib.util
import runpy
import sys
from pathlib import Path
from types import ModuleType

from script_env import resolve_provider_package


class MissingProviderError(ImportError):
    """An expected local provider package or module is absent, rather than broken."""


def _missing(message: str) -> MissingProviderError:
    from active_world import world_root
    return MissingProviderError(
        f"{message} (world {world_root().name!r}; inspect config/local-providers.json)")


def world_provider_module_path(package: str, module_file: str) -> Path:
    """Resolve ``WORLD_ROOT/<package>/<module_file>``.

    @param package: catalogue ``package`` value (e.g. ``providers/uos-portals``)
    @param module_file: Python filename inside that directory (e.g. ``portals.py``)
    @returns: absolute path to the module file
    """
    return resolve_provider_package(package, home='world') / module_file


def load_world_provider(package: str, module_file: str, module_name: str) -> ModuleType:
    """Import a world-local provider module into ``sys.modules`` under ``module_name``.

    @param package: catalogue ``package`` path relative to the active world root
    @param module_file: filename inside the package directory
    @param module_name: ``sys.modules`` key (usually the historical scripts/ stem)
    @returns: the loaded module
    @raises ImportError: if the file is missing under the active world
    """
    try:
        path = world_provider_module_path(package, module_file)
    except FileNotFoundError as exc:
        raise _missing(f"local provider package missing: {package!r}") from exc
    existing = sys.modules.get(module_name)
    if existing is not None and Path(getattr(existing, "__file__", "") or "").resolve() == path:
        return existing
    if not path.is_file():
        raise _missing(f"local provider module missing: {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load local provider module: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_world_provider(package: str, module_file: str) -> None:
    """Execute a world-local provider module as ``__main__`` (CLI entry).

    @param package: catalogue ``package`` path relative to the active world root
    @param module_file: filename inside the package directory
    """
    try:
        path = world_provider_module_path(package, module_file)
        if not path.is_file():
            raise _missing(f"local provider module missing: {path}")
    except FileNotFoundError as exc:
        raise SystemExit(str(_missing(f"local provider package missing: {package!r}"))) from exc
    except MissingProviderError as exc:
        raise SystemExit(str(exc)) from exc
    sys.argv[0] = str(path)
    runpy.run_path(str(path), run_name="__main__")

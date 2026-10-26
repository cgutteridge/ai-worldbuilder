"""Load provider implementation modules for historical CLI / import names.

System provider code lives under ``providers/<id>/{get,review,manage}/``.
Shims in this directory keep the old module names (``defra_lidar``,
``google_aerial``, …) importable and runnable as CLIs.

Each shim loads the implementation and replaces ``sys.modules[shim_name]``
with that module so ``mock.patch.object(defra_lidar, '_http', ...)`` and
similar tests keep working.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

TOOL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = TOOL_ROOT / "scripts"
SHIMS = TOOL_ROOT / "shims"
LIB = TOOL_ROOT / "lib"

if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

import python_runtime  # noqa: E402

python_runtime.ensure_python_runtime()


def _lib_category_dirs() -> list[Path]:
    """Return ``lib/<category>/`` directories that hold library modules."""
    if not LIB.is_dir():
        return []
    return sorted(
        p for p in LIB.iterdir() if p.is_dir() and not p.name.startswith(".")
    )


def ensure_import_paths() -> None:
    """Put ``lib/*/``, ``shims/``, and ``scripts/`` on ``sys.path``.

    Order (highest priority first): ``lib/*/``, ``shims/``, ``scripts/``.
    Libraries win over same-named CLI wrappers; shims win over deprecated
    ``scripts/`` stubs that re-export world providers. Always re-order even
    when the shim directory is already ``sys.path[0]`` (running a shim as a
    script), so inserting ``scripts/`` cannot shadow ``shims/``.
    """
    ordered = [*_lib_category_dirs(), SHIMS, SCRIPTS]
    for path in ordered:
        text = str(path)
        if text in sys.path:
            sys.path.remove(text)
    for path in reversed(ordered):
        sys.path.insert(0, str(path))


def load_provider(provider_id: str, role: str, module_name: str) -> ModuleType:
    """Load ``providers/<id>/<role>/<module_name>.py`` and cache it privately.

    @param provider_id: catalogue provider id (may contain hyphens)
    @param role: one of ``get``, ``review``, ``manage``
    @param module_name: stem of the Python file (historical import name)
    @returns: loaded module object
    """
    ensure_import_paths()
    cache_key = (
        f"_provider_impl.{provider_id}.{role}.{module_name}".replace("-", "_")
    )
    existing = sys.modules.get(cache_key)
    if existing is not None:
        return existing
    path = TOOL_ROOT / "providers" / provider_id / role / f"{module_name}.py"
    if not path.is_file():
        raise FileNotFoundError(f"provider module not found: {path}")
    spec = importlib.util.spec_from_file_location(cache_key, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load provider module: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[cache_key] = mod
    spec.loader.exec_module(mod)
    return mod

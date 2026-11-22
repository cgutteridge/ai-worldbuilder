"""Shared script preamble for the worldbuilder tool.

``ROOT`` is a deferred path for the **active world** checkout (place memory,
world frame, local providers). Module-level joins such as ``ROOT / "site"`` are
safe before ``--world`` is parsed; filesystem use resolves via
:func:`active_world.world_root` after :func:`bind_world`.
``TOOL_ROOT`` is this worldbuilder repo (scripts, skills, system providers).
Provider packages live under two roots:

- ``SYSTEM_PROVIDERS`` — ``TOOL_ROOT / "providers"`` (nationwide+ catalogue)
- ``WORLD_PROVIDERS`` — ``ROOT / "providers"`` (active-world catalogue)

Shared libraries live under ``TOOL_ROOT / "lib" / <category> /`` (bare imports).
CLI entry points live under ``scripts/``. Catalogue ``package`` fields are
relative paths (e.g. ``providers/<id>``); resolve them with
:func:`resolve_provider_package`. Prefer ``from script_env import ROOT, TOOL_ROOT``
and activate a world before reading the filesystem under ``ROOT``.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Iterator, Sequence

TOOL_ROOT = Path(__file__).resolve().parent.parent
if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

import python_runtime  # noqa: E402

python_runtime.ensure_python_runtime()


class DeferredWorldPath:
    """Path under the active world that resolves on filesystem use.

    Joining with ``/`` before a world is selected only records path parts.
    Reading or writing calls :func:`active_world.world_root`.
    """

    __slots__ = ('_parts',)

    def __init__(self, parts: tuple[Any, ...] = ()) -> None:
        """
        @param parts: path segments under the world root (empty = the root itself)
        """
        self._parts = tuple(parts)

    def _resolve(self) -> Path:
        """Resolve to a concrete :class:`~pathlib.Path`.

        @returns: absolute path under the active world
        @raises RuntimeError: when no world is selected
        """
        from active_world import world_root

        path = world_root()
        for part in self._parts:
            path = path / part
        return path

    def __truediv__(self, other: Any) -> DeferredWorldPath:
        """Record another path segment without resolving the world yet."""
        return DeferredWorldPath(self._parts + (other,))

    def __rtruediv__(self, other: Any) -> Path:
        """Allow ``Path(...) / deferred`` once the world is selected."""
        return Path(other) / self._resolve()

    def __fspath__(self) -> str:
        return os.fspath(self._resolve())

    def __str__(self) -> str:
        return str(self._resolve())

    def __repr__(self) -> str:
        if not self._parts:
            return 'DeferredWorldPath()'
        return f'DeferredWorldPath({self._parts!r})'

    def __eq__(self, other: object) -> bool:
        if other is None:
            return False
        try:
            return self._resolve() == Path(other)  # type: ignore[arg-type]
        except (RuntimeError, TypeError, ValueError):
            return NotImplemented

    def __hash__(self) -> int:
        return hash(self._resolve())

    def __bool__(self) -> bool:
        from active_world import WORLD_ROOT

        return WORLD_ROOT is not None

    def __iter__(self) -> Iterator[Path]:
        return iter(self._resolve())

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)


ROOT = DeferredWorldPath()
SCRIPTS = TOOL_ROOT / "scripts"
SHIMS = TOOL_ROOT / "shims"
LIB = TOOL_ROOT / "lib"
SYSTEM_PROVIDERS = TOOL_ROOT / "providers"
WORLD_PROVIDERS = ROOT / "providers"


def lib_category_dirs(lib_root: Path | None = None) -> list[Path]:
    """Return ``lib/<category>/`` directories that hold library modules.

    @param lib_root: library root (default: ``TOOL_ROOT / "lib"``)
    @returns: sorted category directories (only existing dirs, not files)
    """
    root = LIB if lib_root is None else Path(lib_root)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def ensure_import_paths() -> None:
    """Put ``lib/*``, ``shims/``, and ``scripts/`` on ``sys.path``.

    Library category dirs are forced to the front every call so bare imports
    hit ``lib/`` modules even when a caller previously prepended ``scripts/``
    (where same-named CLI wrappers live).
    """
    for _path in (SCRIPTS, SHIMS):
        _text = str(_path)
        if _text not in sys.path:
            sys.path.insert(0, _text)
    for _path in reversed(lib_category_dirs()):
        _text = str(_path)
        if _text in sys.path:
            sys.path.remove(_text)
        sys.path.insert(0, _text)


ensure_import_paths()


def world_bound() -> bool:
    """Return whether an active world has been selected.

    @returns: True after :func:`active_world.set_world` / :func:`set_world_root`
    """
    from active_world import WORLD_ROOT

    return WORLD_ROOT is not None


def bind_world() -> Path:
    """Confirm the selected world and refresh dependent library caches.

    Call after :func:`active_world.set_world` or :func:`active_world.set_world_root`.
    ``ROOT`` is a deferred proxy and does not need reassignment.

    @returns: the bound world root
    @raises RuntimeError: when no world is selected
    """
    from active_world import world_root

    return world_root()


def clear_world_caches() -> None:
    """Drop cached world config in libraries that may already be imported.

    When a world is selected, reloads site_kit / mc_paced so ``OUT`` and friends
    match the newly selected world.
    """
    bound = world_bound()
    site_kit = sys.modules.get("site_kit")
    if site_kit is not None:
        if hasattr(site_kit, "clear_world_config"):
            site_kit.clear_world_config()
        if bound and hasattr(site_kit, "reload_world_config"):
            try:
                site_kit.reload_world_config()
            except (KeyError, FileNotFoundError, RuntimeError, OSError):
                pass
    world_frame = sys.modules.get("world_frame")
    if world_frame is not None and hasattr(world_frame, "clear_frame_cache"):
        world_frame.clear_frame_cache()
    mc_paced = sys.modules.get("mc_paced")
    if mc_paced is not None:
        if hasattr(mc_paced, "clear_world_config"):
            mc_paced.clear_world_config()
        if bound and hasattr(mc_paced, "reload_world_config"):
            try:
                mc_paced.reload_world_config()
            except (KeyError, FileNotFoundError, RuntimeError, OSError):
                pass
    place_config = sys.modules.get("place_config")
    if place_config is not None and hasattr(place_config, "clear_place_config"):
        place_config.clear_place_config()
    footprints = sys.modules.get("osm_footprints")
    if footprints is not None and hasattr(footprints, "_VECTOR_MAP_CLIPS"):
        footprints._VECTOR_MAP_CLIPS = None


def add_world_arg(parser: argparse.ArgumentParser) -> None:
    """Add required ``--world`` (directory name under ``worlds/``).

    @param parser: argument parser to extend
    """
    parser.add_argument(
        "--world",
        required=True,
        metavar="NAME",
        help="world directory under worlds/ (e.g. my-world, bakeoff)",
    )


_ORIGINAL_EXCEPTHOOK = sys.excepthook


def provider_excepthook(exc_type, exc, traceback) -> None:
    """Show only known missing-provider errors concisely at CLI boundaries."""
    from world_provider import MissingProviderError
    if isinstance(exc, MissingProviderError):
        print(str(exc), file=sys.stderr)
    else:
        _ORIGINAL_EXCEPTHOOK(exc_type, exc, traceback)


def activate_world_from_args(args: argparse.Namespace) -> Path:
    """Select ``args.world``, bind ``ROOT``, and clear dependent caches.

    @param args: namespace with a ``world`` attribute (from :func:`add_world_arg`)
    @returns: absolute path to the world checkout
    """
    from active_world import set_world

    sys.excepthook = provider_excepthook
    root = set_world(args.world)
    bind_world()
    clear_world_caches()
    return root


def take_world_arg(
    argv: Sequence[str] | None = None,
) -> tuple[str, list[str]]:
    """Parse required ``--world`` from argv; return name and remaining args.

    @param argv: argument vector without the program name (default: ``sys.argv[1:]``)
    @returns: ``(world_name, remaining_argv)``
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--world", required=True, metavar="NAME")
    args, rest = parser.parse_known_args(list(argv) if argv is not None else None)
    return str(args.world), rest


def prepend_world_activation(argv: Sequence[str] | None = None) -> Path:
    """Parse ``--world`` from ``sys.argv``, activate, and strip it from argv.

    For thin CLI wrappers and shims that then hand the remaining argv to a
    library ``main`` / ``runpy`` entry.

    @param argv: optional override of ``sys.argv[1:]`` (tests)
    @returns: absolute path to the world checkout
    """
    from active_world import set_world

    name, rest = take_world_arg(argv)
    sys.excepthook = provider_excepthook
    root = set_world(name)
    bind_world()
    clear_world_caches()
    if argv is None:
        sys.argv = [sys.argv[0], *rest]
    return root


def resolve_provider_package(package: str, home: str | None = None) -> Path:
    """Resolve a catalogue ``package`` path against system and/or world roots.

    ``package`` is relative to the chosen repo root (e.g. ``providers/local-register``).
    ``home`` selects which root to use:

    - ``"system"`` — under ``TOOL_ROOT``
    - ``"world"`` — under ``ROOT`` (active world)
    - ``None`` — first existing directory, system then world

    @param package: relative package path from the provider catalogue
    @param home: ``system``, ``world``, or ``None`` to search both
    @returns: absolute path to the package directory
    @raises ValueError: if ``package`` or ``home`` is invalid
    @raises FileNotFoundError: if no matching directory exists
    @raises RuntimeError: when ``home`` needs the active world and none is bound
    """
    if not package or not isinstance(package, str):
        raise ValueError(f"invalid package path: {package!r}")
    pkg = Path(package)
    if pkg.is_absolute() or ".." in pkg.parts:
        raise ValueError(f"package must be a relative path without ..: {package!r}")
    if home is not None and home not in ("system", "world"):
        raise ValueError(f"home must be 'system', 'world', or None: {home!r}")

    candidates: list[Path] = []
    if home in (None, "system"):
        candidates.append(TOOL_ROOT / package)
    if home in (None, "world"):
        if not world_bound():
            raise RuntimeError(
                "no world selected; pass --world <name> (a directory under worlds/)"
            )
        candidates.append(Path(ROOT) / package)

    for path in candidates:
        if path.is_dir():
            return path.resolve()
    tried = ", ".join(str(p) for p in candidates)
    raise FileNotFoundError(
        f"provider package not found for {package!r} (home={home!r}); tried: {tried}"
    )


def make_parser(doc: str | None, **kw) -> argparse.ArgumentParser:
    """ArgumentParser that shows the module docstring verbatim as its description.

    @param doc: description text (usually the module docstring)
    @param kw: forwarded to ArgumentParser
    @returns: configured parser
    """
    return argparse.ArgumentParser(
        description=doc,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        **kw,
    )

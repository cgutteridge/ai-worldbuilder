#!/usr/bin/env python3
"""Shim — resolve the active world's packaged ``vector_map`` provider.

Installs the tool-owned cell-extract landscape presets
(``lib/sources/osm_cell_extract``) into the world module's ``LAYER_PRESETS``,
and defaults ``--layers`` to :data:`osm_cell_extract.CELL_EXTRACT_LAYERS` when
the caller omits them so a new cell or place clip gets water, waterway,
barriers, furniture, pedestrian areas, natural and leisure without a hand list.
"""
from __future__ import annotations

import sys
import importlib.util
from pathlib import Path

import _provider_shim as _ps

_ps.ensure_import_paths()

from osm_cell_extract import (  # noqa: E402
    install_cell_extract_layer_presets,
    with_default_cell_extract_layers,
)
from cell_sources import load_place_sources, provider_package_dir  # noqa: E402
_FILE = "osm_extract.py"


def _load_with_cell_extract():
    """Load world osm_extract and install landscape ``LAYER_PRESETS`` entries.

    @returns: loaded world provider module
    """
    data = load_place_sources()
    candidates = [row for row in data.get('providers', [])
                  if 'vector_map' in row.get('types', []) and row.get('package')]
    if len(candidates) != 1:
        raise ImportError(
            'Configure exactly one packaged vector_map provider in '
            'config/local-providers.json (system catalogue also considered); '
            f'found {len(candidates)}'
        )
    row = candidates[0]
    package = provider_package_dir(row['id'], data)
    path = package / _FILE if package is not None else None
    if path is None or not path.is_file():
        raise ImportError(f"vector_map provider {row['id']!r} has no {_FILE}")
    spec = importlib.util.spec_from_file_location('_active_osm_extract', path)
    if spec is None or spec.loader is None:
        raise ImportError(f'cannot load vector_map provider: {path}')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    install_cell_extract_layer_presets(mod.LAYER_PRESETS)
    return mod


if __name__ == "__main__":
    tool = Path(__file__).resolve().parent.parent
    if str(tool) not in sys.path:
        sys.path.insert(0, str(tool))
    import script_env

    script_env.prepend_world_activation()
    _mod = _load_with_cell_extract()
    raise SystemExit(_mod.main(with_default_cell_extract_layers(sys.argv[1:])))
else:
    _mod = _load_with_cell_extract()
    globals().update({k: v for k, v in vars(_mod).items() if not k.startswith("__")})
    _world_main = _mod.main

    def main(argv: list[str] | None = None) -> int:
        """CLI entry: default ``--layers`` to the full cell-extract set when omitted.

        @param argv: optional argv without program name (``None`` → ``sys.argv[1:]``)
        @returns: exit code
        """
        raw = list(sys.argv[1:] if argv is None else argv)
        return _world_main(with_default_cell_extract_layers(raw))

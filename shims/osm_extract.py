#!/usr/bin/env python3
"""Shim — canonical module: active world ``providers/geofabrik-hampshire/osm_extract.py``.

Installs the tool-owned cell-extract landscape presets
(``lib/sources/osm_cell_extract``) into the world module's ``LAYER_PRESETS``,
and defaults ``--layers`` to :data:`osm_cell_extract.CELL_EXTRACT_LAYERS` when
the caller omits them so a new cell or place clip gets water, waterway,
barriers, furniture, pedestrian areas, natural and leisure without a hand list.
"""
from __future__ import annotations

import sys
from pathlib import Path

import _provider_shim as _ps

_ps.ensure_import_paths()

from osm_cell_extract import (  # noqa: E402
    install_cell_extract_layer_presets,
    with_default_cell_extract_layers,
)
from world_provider import load_world_provider  # noqa: E402

_PACKAGE = "providers/geofabrik-hampshire"
_FILE = "osm_extract.py"


def _load_with_cell_extract():
    """Load world osm_extract and install landscape ``LAYER_PRESETS`` entries.

    @returns: loaded world provider module
    """
    mod = load_world_provider(_PACKAGE, _FILE, "osm_extract")
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

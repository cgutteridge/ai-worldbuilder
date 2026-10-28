#!/usr/bin/env python3
"""Read-only checks that the active world's Minecraft instance is set up.

Nothing here writes. The instance root is the parent of ``saves/`` that holds
``config/world.json`` ``save_path``.
"""
from __future__ import annotations

import json
from pathlib import Path

REQUIRED_JARS = ('fabric-api', 'minecraft-fabric-mcp')


def check_instance(save: Path, *, mcp_reachable: bool | None = None,
                   save_open: bool | None = None) -> list[tuple[str, bool, str]]:
    """Check a save and its instance.

    @param save: the save folder from ``world.json``
    @param mcp_reachable: result of the MCP liveness probe (None = not probed)
    @param save_open: whether a running game holds ``session.lock`` (None = not probed)
    @returns: ``(check, ok, detail)`` rows
    """
    rows: list[tuple[str, bool, str]] = []
    rows.append(('save folder', save.is_dir(), str(save)))
    rows.append(('level.dat', (save / 'level.dat').is_file(), 'a Minecraft save has one'))
    rows.append(('region folder', (save / 'dimensions' / 'minecraft' / 'overworld' / 'region').is_dir(),
                 'appears once the world has been opened in the game'))
    instance = save.parent.parent
    mods = instance / 'mods'
    names = [p.name for p in mods.glob('*.jar')] if mods.is_dir() else []
    for jar in REQUIRED_JARS:
        rows.append((f'mod {jar}', any(jar in n for n in names), f'in {mods}'))
    cfg = instance / 'config' / 'minecraft_fabric_mcp' / 'config.json'
    port = None
    if cfg.is_file():
        try:
            port = json.loads(cfg.read_text()).get('port')
        except ValueError:
            pass
    rows.append(('MCP config port', port == 8765, f'{cfg} port={port} (tool expects 8765)'))
    if save_open is not None:
        rows.append(('save open in game', True, 'yes' if save_open else 'no (game closed)'))
    if mcp_reachable is not None:
        closed = save_open is False
        rows.append(('MCP server answers', mcp_reachable or closed,
                     '127.0.0.1:8765' + (' (not expected: game closed)' if closed and not mcp_reachable else '')))
    return rows

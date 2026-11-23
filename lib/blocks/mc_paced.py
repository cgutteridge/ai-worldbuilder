"""Paced MCP access: one client waits ``interval`` (default 1.1 s) between calls.

The Fabric MCP limiter is a shared per-IP token bucket (loaded default 600 RPM).
A single paced instance stays near 55 RPM; concurrent clients on the same IP
share the budget — see ``docs/archive/389-minecraft-rate-limit-waits.md``.
"""
import json
import time
from pathlib import Path

from mcp import Client

import script_env  # noqa: E402

_CFG = None
UUID = None

D = 'minecraft:overworld'
_DEFAULT_UUID = 'f89f7477-c4ee-4997-a8ee-be75b4d8b75a'


def clear_world_config() -> None:
    """Drop cached player UUID from ``world.json``."""
    global _CFG, UUID
    _CFG = None
    UUID = None


def reload_world_config() -> None:
    """Load player UUID from the active world's ``config/world.json``.

    @raises RuntimeError: when no world is selected
    """
    global _CFG, UUID
    if not script_env.world_bound():
        raise RuntimeError(
            'no world selected; pass --world <name> (a directory under worlds/)'
        )
    root = Path(script_env.ROOT)
    _CFG = json.loads((root / 'config/world.json').read_text())
    UUID = _CFG.get('player_uuid', _DEFAULT_UUID)


def _ensure_world_config() -> None:
    """Reload world config when it has not been loaded yet."""
    if _CFG is None:
        reload_world_config()


if script_env.world_bound():
    reload_world_config()


class Paced:
    """Rate-limited MCP world client."""

    def __init__(self, port=8765, interval=1.1):
        """
        @param port: world (8765) or view (8766) MCP port
        @param interval: minimum seconds between calls
        """
        self.client = Client(port, resume_stateless=True)
        self.interval = interval
        self.last = 0.0

    def call(self, name, args):
        """Call an MCP tool and return the raw result dict.

        @param name: tool name
        @param args: tool arguments
        @returns: MCP tools/call result
        """
        time.sleep(max(0, self.interval - (time.monotonic() - self.last)))
        try:
            return self.client.call(name, args)
        finally:
            self.last = time.monotonic()

    def tell(self, message):
        """Chat the player (before and after any tool moves them). A failed message never stops the work.

        @param message: text shown in the player's chat, prefixed [AI-builder]
        """
        try:
            _ensure_world_config()
            self.call('player_send_message', {
                'uuid': UUID,
                'message': f'[AI-builder] {message}',
            })
        except Exception as e:  # noqa: BLE001
            print(f'  (could not message player: {e})')

    def text(self, name, args):
        """Call an MCP tool and join text content blocks.

        @param name: tool name
        @param args: tool arguments
        @returns: concatenated text
        """
        r = self.call(name, args)
        return '\n'.join(x['text'] for x in r['content'] if x['type'] == 'text')


def xyz(p):
    """Convert [x,y,z] to an MCP position dict.

    @param p: sequence of three numbers
    @returns: {'x','y','z'} dict
    """
    return dict(zip('xyz', p))


def box(lo, hi):
    """Build an MCP axis-aligned box from two corners.

    @param lo: low corner [x,y,z]
    @param hi: high corner [x,y,z]
    @returns: {'from','to'} dict
    """
    return {'from': xyz(lo), 'to': xyz(hi)}

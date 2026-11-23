"""Paced MCP access: one client waits ``interval`` (default 1.1 s) between calls.

The Fabric MCP limiter is a shared per-IP token bucket (loaded default 600 RPM).
A single paced instance stays near 55 RPM; concurrent clients on the same IP
share the budget — see ``docs/archive/389-minecraft-rate-limit-waits.md``.
"""
import json
import re
import time
from pathlib import Path

from mcp import Client

import script_env  # noqa: E402

_CFG = None
UUID = None

D = 'minecraft:overworld'
_UUID_TEXT = re.compile(
    r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
)


def clear_world_config() -> None:
    """Drop cached player UUID from ``world.json``."""
    global _CFG, UUID
    _CFG = None
    UUID = None


def reload_world_config() -> None:
    """Load player UUID from the active world's ``config/world.json``.

    A missing or blank ``player_uuid`` leaves :data:`UUID` unset. There is no
    built-in player. :func:`require_player_uuid` names the key.

    @raises RuntimeError: when no world is selected
    """
    global _CFG, UUID
    if not script_env.world_bound():
        raise RuntimeError(
            'no world selected; pass --world <name> (a directory under worlds/)'
        )
    root = Path(script_env.ROOT)
    _CFG = json.loads((root / 'config/world.json').read_text())
    raw = _CFG.get('player_uuid')
    text = raw.strip() if isinstance(raw, str) else ''
    UUID = text or None


def require_player_uuid() -> str:
    """Return ``player_uuid`` from the active world's ``config/world.json``.

    @returns: non-empty player UUID
    @raises RuntimeError: when ``player_uuid`` is missing or blank
    """
    _ensure_world_config()
    if not UUID:
        raise RuntimeError('world.json is missing player_uuid')
    return UUID


def online_player_uuids(text: str) -> list[str]:
    """UUIDs in ``player_list_online`` text, first occurrence, lowercased.

    @param text: tool text
    @returns: unique UUIDs in order
    """
    found: list[str] = []
    for match in _UUID_TEXT.findall(text or ''):
        uuid = match.lower()
        if uuid not in found:
            found.append(uuid)
    return found


def fill_player_uuid_from_online(text: str, path: Path) -> str:
    """Write the single online player's UUID into ``world.json``.

    Leaves an existing ``player_uuid`` unchanged when it is the same player.
    Refuses when the list is empty, lists several players, or disagrees with
    the UUID already stored.

    @param text: ``player_list_online`` text
    @param path: ``config/world.json`` to update
    @returns: UUID written, or the one already stored
    @raises RuntimeError: when the list cannot choose exactly one player
    """
    found = online_player_uuids(text)
    if not found:
        raise RuntimeError(
            'player_list_online listed no player; '
            'world.json player_uuid stays unset until you are in the world'
        )
    if len(found) > 1:
        listed = ', '.join(found)
        raise RuntimeError(
            f'player_list_online listed more than one player ({listed}); '
            'set player_uuid to the player this build should move'
        )
    uuid = found[0]
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'{path} is not valid JSON ({exc})') from exc
    if not isinstance(data, dict):
        raise RuntimeError(f'{path} is not a JSON object')
    current = data.get('player_uuid')
    current_text = current.strip() if isinstance(current, str) else ''
    if current_text:
        if current_text.casefold() != uuid.casefold():
            raise RuntimeError(
                f'world.json player_uuid is {current_text}; '
                f'player_list_online reports {uuid}'
            )
        return current_text
    data['player_uuid'] = uuid
    from atomic_json import write_json
    write_json(path, data)
    return uuid


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

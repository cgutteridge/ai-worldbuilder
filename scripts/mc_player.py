"""Player control with a required explanation: tell in Minecraft chat, then move.

Library-first. Scripts import ``move_player`` / ``restore_player``. Agents use the CLI:

  python3 scripts/mc_player.py move --why "…" --pos x,y,z [--facing x,y,z] [--gamemode creative]
  python3 scripts/mc_player.py tell --why "…"
"""
from __future__ import annotations

import argparse
import sys

from mc_paced import D, UUID, Paced, xyz

RESTORE_WHY = 'Put you back where you were.'


def _require_why(why: str) -> str:
    """Return stripped why or raise ValueError if blank.

    @param why: explanation shown in Minecraft chat
    @returns: stripped non-empty string
    @raises ValueError: when why is missing or blank
    """
    text = (why or '').strip()
    if not text:
        raise ValueError('why is required (non-empty explanation for the player)')
    return text


def move_player(*, why: str, position=None, facing=None, gamemode=None, m=None):
    """Explain in Minecraft chat, then teleport and/or set gamemode.

    At least one of ``position`` or ``gamemode`` must be set. Empty ``why`` is refused
    before any MCP call.

    @param why: non-empty reason shown as ``[AI-builder] …``
    @param position: ``[x,y,z]`` eye position, or None to leave place unchanged
    @param facing: optional look-at ``[x,y,z]``
    @param gamemode: optional ``creative`` / ``spectator`` / ``survival`` / ``adventure``
    @param m: optional ``Paced`` client (default: new world client)
    @returns: the paced client used
    @raises ValueError: blank why, or neither position nor gamemode
    """
    why = _require_why(why)
    if position is None and gamemode is None:
        raise ValueError('move_player needs position and/or gamemode')
    client = m or Paced()
    client.tell(why)
    if gamemode is not None:
        client.call('player_set_gamemode', {'uuid': UUID, 'gamemode': gamemode})
    if position is not None:
        args = {
            'uuid': UUID,
            'dimension': D,
            'position': xyz(position),
        }
        if facing is not None:
            args['facing'] = xyz(facing)
        client.call('entity_teleport', args)
    return client


def restore_player(*, position, facing=None, gamemode=None, m=None):
    """Put the player back after a temporary hop; chats a fixed restore line.

    Teleports first, then restores gamemode (same order as the old apply hop).

    @param position: ``[x,y,z]`` to restore
    @param facing: optional look-at ``[x,y,z]``
    @param gamemode: optional mode to restore
    @param m: optional ``Paced`` client
    @returns: the paced client used
    """
    client = m or Paced()
    client.tell(RESTORE_WHY)
    args = {
        'uuid': UUID,
        'dimension': D,
        'position': xyz(position),
    }
    if facing is not None:
        args['facing'] = xyz(facing)
    client.call('entity_teleport', args)
    if gamemode is not None:
        client.call('player_set_gamemode', {'uuid': UUID, 'gamemode': gamemode})
    return client


def tell_player(why: str, m=None):
    """Chat the player with ``[AI-builder]`` prefix; why must be non-empty.

    @param why: non-empty message body
    @param m: optional ``Paced`` client
    @returns: the paced client used
    @raises ValueError: blank why
    """
    why = _require_why(why)
    client = m or Paced()
    client.tell(why)
    return client


def _parse_xyz(s: str) -> list[float]:
    """Parse ``x,y,z`` into three floats.

    @param s: comma-separated triple
    @returns: [x, y, z]
    """
    parts = [p.strip() for p in s.split(',')]
    if len(parts) != 3:
        raise SystemExit(f'want x,y,z; got {s!r}')
    try:
        return [float(p) for p in parts]
    except ValueError as e:
        raise SystemExit(f'want numeric x,y,z; got {s!r}') from e


def main(argv=None) -> int:
    """CLI: ``move`` / ``tell`` with required ``--why``.

    @param argv: argument list (default: ``sys.argv[1:]``)
    @returns: process exit code
    """
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='cmd', required=True)

    move = sub.add_parser('move', help='tell then teleport and/or set gamemode')
    move.add_argument('--why', required=True, help='explanation shown in Minecraft chat')
    move.add_argument('--pos', help='x,y,z eye position')
    move.add_argument('--facing', help='optional look-at x,y,z')
    move.add_argument('--gamemode', choices=('creative', 'spectator', 'survival', 'adventure'))

    tell = sub.add_parser('tell', help='chat only (still requires --why)')
    tell.add_argument('--why', required=True, help='message body shown in Minecraft chat')

    args = p.parse_args(argv)
    try:
        if args.cmd == 'tell':
            tell_player(args.why)
            return 0
        if not args.pos and not args.gamemode:
            p.error('move needs --pos and/or --gamemode')
        move_player(
            why=args.why,
            position=_parse_xyz(args.pos) if args.pos else None,
            facing=_parse_xyz(args.facing) if args.facing else None,
            gamemode=args.gamemode,
        )
        return 0
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

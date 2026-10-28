"""Refuse region-file writes until the player has left the world (ticket #32).

Only commands that write, delete or replace ``.mca`` files use this gate, and they must
pass ``--confirm-logged-out``. "Logged out" means the world is closed (exit to the
title screen, which releases ``session.lock`` and stops the MCP bridge); the game
itself can stay open. Reads never use the gate: planning reads of ``.mca`` files are
fine with the world open.
"""
from __future__ import annotations

import fcntl
import subprocess
import sys
from pathlib import Path


class LogoutGateError(RuntimeError):
    """Region write refused: player must leave the world."""


def play_need_you() -> None:
    """Ask the user (soundboard) to leave the world (title screen).

    Best-effort: missing soundboard must not block the refusal path.
    """
    script = Path.home() / 'Projects' / 'agent-soundboard' / 'scripts' / 'play.py'
    if not script.is_file():
        return
    try:
        subprocess.Popen(  # noqa: S603 — fixed local soundboard path
            [sys.executable, str(script), 'need-you'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def session_lock_held(lock: Path) -> bool:
    """Whether a running Minecraft holds ``lock`` (a POSIX lock, as Java takes).

    Minecraft leaves the file behind after the world closes, so existence proves nothing.

    @param lock: path to ``session.lock``
    @returns: True when another process holds it; False when absent or free
    """
    try:
        with open(lock, 'r+b') as fh:
            try:
                fcntl.lockf(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.lockf(fh, fcntl.LOCK_UN)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return False


def mcp_world_reachable(timeout: float = 1.5) -> bool:
    """Whether the Minecraft MCP bridge answers (world likely open).

    @param timeout: connect timeout seconds
    @returns: True when something listens on the bridge
    """
    import mcp_probe

    return mcp_probe.mcp_world_reachable(timeout=timeout)


def resolve_save_path(save_path: Path | None = None) -> Path | None:
    """Active-world save directory (parent of ``session.lock``).

    @param save_path: override (tests)
    @returns: save path or None when unknown
    """
    if save_path is not None:
        return Path(save_path)
    try:
        from active_world import save_path as _save_path

        return _save_path(required=False)
    except Exception:
        return None


def require_logged_out(
    *,
    confirm_logged_out: bool,
    save_path: Path | None = None,
    play_cue: bool = True,
) -> None:
    """Refuse a region write unless the operator confirmed logout and the save looks idle.

    @param confirm_logged_out: CLI ``--confirm-logged-out``
    @param save_path: override save directory (tests)
    @param play_cue: play ``need-you`` when the confirm flag is missing
    @raises LogoutGateError: when rewrite must not proceed
    """
    if not confirm_logged_out:
        if play_cue:
            play_need_you()
        raise LogoutGateError(
            'region write refused: leave the world (exit to the title screen; the game can '
            'stay open), then pass --confirm-logged-out. Sound cue: need-you.'
        )
    save = resolve_save_path(save_path)
    if save is not None:
        lock = save / 'session.lock'
        if session_lock_held(lock):
            raise LogoutGateError(
                f'region write refused: {lock} is held (Minecraft still holds the save). '
                'Exit the world to the title screen, then retry with --confirm-logged-out.'
            )
    if mcp_world_reachable():
        raise LogoutGateError(
            'region write refused: Minecraft MCP bridge is reachable (world likely open). '
            'Exit the world to the title screen, then retry with --confirm-logged-out.'
        )

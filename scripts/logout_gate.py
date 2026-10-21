"""Refuse region-file rewrites until the player has left the Minecraft save (ticket #32).

Commands that swap ``.mca`` files must pass ``--confirm-logged-out``. Even with that
flag, a live ``session.lock`` or a reachable MCP bridge still refuses — the world
must be fully idle.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


class LogoutGateError(RuntimeError):
    """Region rewrite refused: player must leave the save."""


def play_need_you() -> None:
    """Ask the user (soundboard) to log out of the world.

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


def mcp_world_reachable(timeout: float = 1.5) -> bool:
    """Whether the Minecraft MCP bridge answers (world likely open).

    @param timeout: connect timeout seconds
    @returns: True when something listens on the bridge
    """
    import region_backup as rb

    return rb.mcp_world_reachable(timeout=timeout)


def resolve_save_path(save_path: Path | None = None) -> Path | None:
    """Active-world save directory (parent of ``session.lock``).

    @param save_path: override (tests)
    @returns: save path or None when unknown
    """
    if save_path is not None:
        return Path(save_path)
    try:
        from active_world import load_world_config
        from script_env import ROOT

        cfg = load_world_config()
        raw = cfg.get('save_path')
        if not raw:
            region = cfg.get('region_path')
            if region:
                # .../dimensions/minecraft/overworld/region → save root is 4 parents up
                p = Path(region)
                if not p.is_absolute():
                    p = Path(ROOT) / p
                return p.parents[3] if len(p.parents) >= 4 else p.parent
            return None
        path = Path(raw)
        return path if path.is_absolute() else Path(ROOT) / path
    except Exception:
        return None


def require_logged_out(
    *,
    confirm_logged_out: bool,
    save_path: Path | None = None,
    play_cue: bool = True,
) -> None:
    """Refuse unless the operator confirmed logout and the save looks idle.

    @param confirm_logged_out: CLI ``--confirm-logged-out``
    @param save_path: override save directory (tests)
    @param play_cue: play ``need-you`` when the confirm flag is missing
    @raises LogoutGateError: when rewrite must not proceed
    """
    if not confirm_logged_out:
        if play_cue:
            play_need_you()
        raise LogoutGateError(
            'region rewrite refused: pass --confirm-logged-out after quitting '
            'the Minecraft save (title screen / exit). Sound cue: need-you.'
        )
    save = resolve_save_path(save_path)
    if save is not None:
        lock = save / 'session.lock'
        if lock.is_file():
            raise LogoutGateError(
                f'region rewrite refused: {lock} is present (Minecraft still holds the save). '
                'Quit to title / exit completely, then retry with --confirm-logged-out.'
            )
    if mcp_world_reachable():
        raise LogoutGateError(
            'region rewrite refused: Minecraft MCP bridge is reachable (world likely open). '
            'Quit the save completely, then retry with --confirm-logged-out.'
        )

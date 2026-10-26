#!/usr/bin/env python3
"""Liveness probe for the Minecraft world MCP bridge (127.0.0.1:8765).

Kept apart from any file-touching module so ``site_kit`` and ``logout_gate`` can ask
"is a world open?" without importing anything else.
"""
from __future__ import annotations

import urllib.error
import urllib.request


def mcp_world_reachable(timeout: float = 1.5) -> bool:
    """True when the world MCP bridge answers (world likely open).

    @param timeout: connect timeout seconds
    @returns: whether http://127.0.0.1:8765/mcp responds at all
    """
    req = urllib.request.Request(
        'http://127.0.0.1:8765/mcp',
        data=b'{}',
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(64)
        return True
    except urllib.error.HTTPError:
        # HTTP error still means something is listening on the bridge port.
        return True
    except OSError:
        return False

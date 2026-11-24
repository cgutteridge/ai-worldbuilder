#!/usr/bin/env python3
"""Read one shop's fascia colour from street-level imagery and print the signs patch (#475).

  python3 scripts/fascia_read.py --world <w> --way <map way id> [--spec <spec.json>]

One designer ask (cached); frames go to the world's gitignored instance/fascia-frames. Never edits a spec.
Implementation: ``lib/builds/fascia_read.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":
    tool = Path(__file__).resolve().parent.parent
    if str(tool) not in sys.path:
        sys.path.insert(0, str(tool))
    import script_env

    script_env.ensure_import_paths()
    script_env.prepend_world_activation()
    from fascia_read import main

    raise SystemExit(main())

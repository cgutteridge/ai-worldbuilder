#!/bin/bash
# OPTIONAL — not part of handoff. Prefer one-shot in Terminal:
#   python3 shims/aerial_mosaic.py --at X,Z --place … --dir aerial/… --add
# Use this only if you want the agent to download Hub tiles itself.
#
# Usage: scripts/start-ref-fetch.command <world>
cd "$(dirname "$0")/.." || exit 1
if [ -z "${1:-}" ]; then
  echo "ERROR: --world is required" >&2
  echo "  scripts/start-ref-fetch.command <world>" >&2
  echo "  python3 scripts/ref_fetch_agent.py --world <name>" >&2
  exit 2
fi
echo "Worldbuilder — optional ref_fetch_agent for $1 (Ctrl+C to stop)"
echo
exec python3 scripts/ref_fetch_agent.py --world "$1"

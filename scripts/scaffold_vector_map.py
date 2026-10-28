#!/usr/bin/env python3
"""Scaffold a world-local Geofabrik vector_map provider without downloading PBF.

  python3 scripts/scaffold_vector_map.py --world NAME --extract ID \
      --bbox-wgs84 west,south,east,north [--index-file index-v1.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import urlopen

from script_env import add_world_arg, activate_world_from_args
from geofabrik_provider import INDEX_URL, parse_bbox, scaffold


def main(argv: list[str] | None = None) -> int:
    """Validate published extract coverage and write a provider plus catalogue row."""
    ap = argparse.ArgumentParser(description=__doc__)
    add_world_arg(ap)
    ap.add_argument('--extract', required=True, help='exact Geofabrik index id, e.g. isle-of-wight')
    ap.add_argument('--bbox-wgs84', required=True, help='west,south,east,north')
    ap.add_argument('--index-file', type=Path, help='offline Geofabrik index-v1.json with geometry')
    args = ap.parse_args(argv)
    root = activate_world_from_args(args)
    try:
        bbox = parse_bbox(args.bbox_wgs84)
        if args.index_file:
            index = json.loads(args.index_file.read_text())
        else:
            with urlopen(INDEX_URL, timeout=60) as response:
                index = json.load(response)
        print(scaffold(root, args.extract, bbox, index))
    except (ValueError, OSError) as exc:
        ap.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

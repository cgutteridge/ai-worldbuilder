#!/usr/bin/env python3
"""Students' Union B42 (vector-map way 33830305) — bake-off R-001 evidence redesign.

Evidence table (sources only; not prior Minecraft build):

| Fact | Source | Reading |
| --- | --- | --- |
| Footprint | vector-map way 33830305 (building=university, loc_ref=42); ~3924 cells | L-shaped mass south of Library Square; axis-aligned readable box, not 1:1 jagged map stairs |
| Storeys | map building:levels=4; lidar bbox MC 6145..6240 × -5575..-5495 | ground_y min 42 / top_y max 64 → four 3-block storeys + pitched roof (feature_build STOREY_H=3) |
| Roof form | lidar DSM/DTM PNG + aerial sheet z19 at 6182,-5536 | Main ridge runs E–W on north wing; lower flat/green terraces south; spec pitched smooth_stone grey |
| N front (Library Square) | Portal 42-A photo; building 42_2025 | Recessed concourse: full-height glazing, white fascia band; terracotta SUSU panel on alternate entrance |
| S front (University Road) | Portals 42-B, 42-C | Black-framed café storefronts (piazz@soton, Lattes); floor-to-ceiling glass ground floor |
| W (steps / café) | Portal 42-D | White concrete plinth, brick mid, dark louvre band, dark-framed upper windows |
| Doors | portals.py building 42 (42-A..D) | Four registered portals → iron_door map in spec |
| Greeblies | aerial + 42-D streetlevel | Roof: small plant/HVAC boxes on rear flat roof (aerial); street: horizontal dark louvre above café (42-D) — placed in ``add_greeblies`` |

Greeblies list (street/aerial scale):
- Dark louvre band on W/S café level (black_concrete slab row)
- Low parapet plant boxes on south flat roof inset (oak_slab + short_grass)

Usage::

  python3 scripts/susu_42_r001.py              # dry run (shell + greeblies plan)
  SITE_OWNER=<owner> python3 scripts/susu_42_r001.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from script_env import ROOT  # noqa: E402

import feature_build as fb  # noqa: E402
from site_kit import Plan, apply, preview  # noqa: E402

SPEC = ROOT / 'site/builds/susu_42_r001.json'
BUILD_NAME = 'susu_42_r001'


def add_greeblies(plan: Plan) -> None:
    """Add street-scale roof and façade greeblies on top of the shell plan.

    @param plan: merged feature plan (mutated in place)
    """
    # Café louvre band — west arcade below upper windows (portal 42-D reading).
    for x in range(6178, 6188):
        plan.set(x, 47, -5572, 'minecraft:black_concrete')
    for x in range(6200, 6228):
        plan.set(x, 47, -5564, 'minecraft:black_concrete')
    # Flat-roof plant boxes — south inset visible on aerial (approx MC).
    for x, z in ((6195, -5510), (6208, -5505), (6215, -5512)):
        plan.set(x, 54, z, 'minecraft:oak_slab[type=bottom]')
        plan.set(x, 55, z, 'minecraft:short_grass')


def run(*, do_apply: bool) -> Plan:
    """Build shell from spec, attach greeblies, preview, and apply.

    @param do_apply: live apply when True
    @returns: final Plan
    """
    spec = fb.load_spec(SPEC)
    shell = fb.build(spec)
    add_greeblies(shell)
    out = ROOT / f'verification/site/{BUILD_NAME}-preview.png'
    preview(shell, out, scale=4)
    apply(shell, dry=not do_apply, modifies=tuple(spec['modifies']))
    return shell


def main(argv: list[str] | None = None) -> int:
    """CLI entry.

    @param argv: optional argument vector
    @returns: process exit code
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--apply', action='store_true', help='live apply (requires SITE_OWNER lock)')
    args = ap.parse_args(argv)
    run(do_apply=args.apply)
    return 0


if __name__ == '__main__':
    sys.exit(main())

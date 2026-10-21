# ADR 001: Evidence types + world frame

**Status:** Accepted (2026-10-17)  
**Context:** Southampton Refine mixed vendor names (Google), checklist family keys, and evidence questions. Coordinate math was scattered (`OFFSET_E`, private gdaltransform, duplicate mercator).

## Decision

1. **Types** are stable evidence classes (`vector_map`, `poi_register`, `aerial`, `streetlevel`, `lidar`) documented in `reference/TYPES.txt`. Rules name types; providers are place-local.
2. **Providers** live in `config/place-sources.json` (provider-primary rows with `types: [...]`). Cell checklist / `--record` keys are provider ids; `google` aliases to `google-satellite`.
3. **World frame** is canonical in `config/world-frame.json`, accessed via `scripts/world_frame.py` (WGS84, OSGB/EPSG, MC, cell, web-mercator XYZ, Y offset). Legacy `osgb.py` / `geodetic.py` wrap it.
4. Review language: **Aerial Review** (registry issues / layer reopen), not Google Review. (`site/AERIAL-REVIEW.txt` retired with the cell-registry cut-over.)
5. New-world Y offset is a human checkpoint after `lidar_extremes` over an agreed bbox (≥ 5 km × 5 km recommended).

## Consequences

- Agents can trust evidence-order without naming Google.
- Adding a provider is config + SOURCES via skills `evaluate-data-source` / `add-data-source`.
- Hot-path scripts import `world_frame`; noisy artifact renames (`trees-google-crowns.json`, `instance/google-aerial/`) wait.
- Auto stale-source refresh (~6 months) is implemented in ``cell_sources``
  (``STALE_DAYS``, checklist ``STALE``, ``--todo`` / ``--record``); readiness is
  documented in TYPES.txt.

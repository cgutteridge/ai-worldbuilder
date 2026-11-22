---
name: evaluate-data-source
description: >-
  Score a candidate evidence feed for this place: coverage, licence, CRS,
  cadence, which types it fills, overlap, wire cost. Use when the user asks
  whether a source is worth it, compares candidates, or before add-data-source.
---

Decide add / skip / look-only-only, and which **type** slot(s). Do not write fetch code here.

## 0. Load the frame

1. Read `reference/TYPES.txt` (five types).
2. Read `config/system-providers.json` and the active world's `config/local-providers.json` (what already fills each type).
3. Skim the active world's `reference/SOURCES.txt` for similar providers.
4. Note dual provider roots: system code under tool `providers/<id>/`; local under active-world `providers/<id>/`. Confirm with `cell_sources.provider_package_dir` / `provider_home` when a row already has `package`.
5. Decide **local vs system** up front (ask if unclear). Rule of thumb: below national (UK / England) → local.

Done when you can name the candidate's likely type(s), local-vs-system home, and the existing providers in those slots.

## 1. Score

For the candidate, record one line each:

| Factor | Ask |
| --- | --- |
| Types | Which of vector_map / poi_register / aerial / streetlevel / lidar? One provider may fill several. |
| Coverage | Does it cover the place bbox / campus? Gaps? |
| Licence | keep vs look-only; redistributable? |
| CRS | Matches `config/world-frame.json` CRS, or needs a transform via `world_frame`? |
| Cadence | How often updated? Stale risk? |
| Overlap | Duplicate of an existing checklist provider? |
| Effort | New script under the matching `providers/<id>/` root vs config-only? Host allowlist? |

Done when every row has a short answer (unknown → say unknown).

## 2. Recommend

Output exactly:

1. **Verdict:** add | skip | look-only-only
2. **Home:** local | system (with one-line why)
3. **Type slot(s):** from TYPES.txt
4. **Provider id** suggestion (kebab-case)
5. **Why** (3 bullets max)
6. **Next:** if add → invoke `add-data-source`; else stop

Optional cues follow `docs/agents/world-and-live-commands.md#sound-cues`.

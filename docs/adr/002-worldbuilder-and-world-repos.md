# ADR 002: AI worldbuilder + per-world repos

**Status:** Accepted (2026-10-17)

This tree (“Southampton Refine”) is a **donor**, not the long-term home. We split into two **new** git repos: an **AI worldbuilder** (reusable tool) and **southampton-build** (one world). We rejected keeping one monorepo or renaming this repo in place — world memory and shared tooling must not share a tracker or a default path.

## Decision

1. **Worldbuilder** repo (`ai-worldbuilder`): generic AI worldbuilding — shared scripts, skills, evidence types, system providers, Matt **system tracker** (`tasks/`). No world QUEUE/ISSUES.
2. **World** repos (first: `southampton-build`): game-world build only — `site/` (QUEUE, ISSUES, notes), world frame / offsets / gameworld id, feature/tree/decor build scripts, **local** providers. **World tracker** only.
3. **N worlds, one active:** one worldbuilder checkout can address many world checkouts (sibling repos). Exactly one is active, selected by a **system-level Python** config (import-friendly) that points at the active world’s root and reads that world’s offsets and gameworld id.
4. **Trackers are distinct:** if it is unclear whether work is tool or world, **ask** — do not auto-prefer system tasks over the world queue.
5. **Providers:** on add, ask **local vs system**. Rule of thumb: below national (UK / England) → local. System catalogue holds nationwide+; each world extends with local feeds.
6. **Extract timing:** record here and seed a holding system task list in this donor; create the two repos and move files in a **dedicated later session** (not mid dirty-tree).

## Consequences

- `site/` is per-world (in the world repo), not a global folder for every place.
- World-build scripts stay on that world’s QUEUE; they never belong in the system tracker.
- Adding a Hampshire-only feed to the system catalogue requires an explicit override of the rule of thumb.
- Donor `tasks/` is temporary; after extract it moves to `ai-worldbuilder` and this repo is retired/archived.

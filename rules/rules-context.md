# Rules to read for registry work

Start place work with `python3 scripts/cell_registry.py --world <world> next`.
Use the returned task and the work below to choose the relevant rules. This is a
manual reading index; progress and dispatch belong to the registry and the
[cell orchestrator](../.agents/skills/cell-orchestrator/SKILL.md).

Read a rule once per chat, then use targeted sections when checking a detail.
For the tool's branch, tracker, model and execution rules, follow the pointers
in [AGENTS.md](../AGENTS.md).

## Evidence

Before opening or fetching imagery, read [reference-token-budget.md](reference-token-budget.md)
and [evidence-order.md](evidence-order.md). Consult `reference/TYPES.txt` when
choosing a provider, and the active world's committed cell SOURCES file and
reference manifest before fetching another asset.

Check aerial coverage for the whole cell. Use the wider mosaic when the detailed
image leaves a gap; the provider and available files come from the cell's evidence.

## Build work

| Work | Read |
| --- | --- |
| Any live edit | [radial-good-enough.md](radial-good-enough.md), [process.md](process.md) cadence and undo sections |
| Ground | [handoff-layered-passes.md](handoff-layered-passes.md), [layered-pass decisions](../docs/adr/010-layered-pass-decisions.md), [craftsmanship.md](craftsmanship.md) ground palette; the cell's ground-finish evidence |
| Trees | Ground references above, then [foliage.md](foliage.md) |
| Decor | [process.md](process.md) per-cell pass flow, [frontage-and-evidence.md](frontage-and-evidence.md) for frontage work |
| House | [house-builds.md](house-builds.md), [frontage-and-evidence.md](frontage-and-evidence.md), [craftsmanship.md](craftsmanship.md) |
| Other feature | [feature-builds.md](feature-builds.md), [craftsmanship.md](craftsmanship.md); water features also use [water-body-builds.md](water-body-builds.md) |
| Issue or snag | The `snag` or `sherlock` skill and `scripts/issues.py` |

Designer and builder model choices are in [build-roles.md](build-roles.md).
Read the configured entrance and building evidence on the footprint when planning
facades; the provider catalogue roles determine their source. Ground, trees and
decor remain separate from a house feature's volume and follow the pass rules.

Tool-only changes need build rules only when their scope touches that behavior.
Provider catalogues and fetching are covered by `evaluate-data-source` and
`add-data-source`; a new world starts with `new-world-build`.

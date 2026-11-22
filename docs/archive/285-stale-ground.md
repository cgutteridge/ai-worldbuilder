# #285: readiness flags a cell's ground stale when a feature landed after it (2026-11-14)

## Diagnosis (read-only, southampton-build, cell 48,-44)

- What was already enforced: the journal fold (`cell_journal._derive`) clears trees and decor when
  any feature lands in a touched cell, and decor when trees land. `next` orders ground (if not
  generated) → features → trees → decor. Nothing ever said a ground was out of date: the fold only
  asks whether a landed ground exists, and readiness (`cell_readiness.check`) only reads layer flags.
- The 48,-44 journal (2026-10-29): ground seq 445 began 14:55 and landed. Jubilee (48,-43, touches
  48,-44) landed 14:58, and SUSU was undone at 16:27. The ground redo at 16:31 (seq 469) was
  **abandoned** (rolled back), so the last landed ground is still the 14:55 one. SUSU landed again
  at 17:21. The cell's ground therefore predates two live features.
- A trap in the evidence: today's `reconcile --fix` (seq 918) upgraded the 10-27 ground entry
  seq 217 to landed, with finish seq 919. Ordering by landing would call that the newest ground and
  hide the problem. Staleness is ordered by **begin** order (when the plan was made) instead.
- Trees/decor over a later ground: not caught, and that is deliberate in the current code.
  `test_ground_redo_stales_unexecuted_plans_but_preserves_current_completion` and the
  cell-orchestrator skill both say a ground redo preserves trees/decor completion. Yet
  `layer_common.ignored_builds` does not count the cell's own trees/decor as owners. Left as is;
  open question below.

## Rule

1. `cell_readiness.stale_ground(reg, cell)`: the ground is stale when any live feature entry touching
   the cell (a neighbour's included) began after the cell's latest landed ground. Undone and
   abandoned entries are ignored. Mismatched and begun ones count, and each cause names its status.
   Each cause reads `ground <ts> is older than feature <plan> <landed|applied mismatched|begun
   (unfinished)> <ts>`.
2. `check()` returns `ground_stale` (causes) and `ground_redo`, and stale ground makes the report
   not `complete` (not DONE). It is a blocker only for trees/decor operations (`ground stale: ...`
   with a `clear_with` command), unless the ground plan is in `redoing`. It never blocks ground or
   feature work.
3. The redo command is `redo_layer.py --cell <c> --layers ground[,trees][,decor] --apply`. Trees and
   decor are included when they have landed entries, because the ground pass does not own around them.
4. `next` offers `ground-redo` after the cell's unbuilt features and before trees. It prints the
   causes and `clear: ground stale -> <command>` (#284 form), and the redo is never started. A
   needs-prep cell carries the same lines. `next --cell TX,TZ` shows the result for one cell.
5. The redo preflight (`layer_redo`) prints each cause when redoing ground. The ground plan counts
   as redone for the trees/decor checks in the same sequence. The morning report has a
   STALE GROUND section.

## Open questions for the owner

- **Order inside a cell.** `next` still builds ground before features, so every fresh cell now
  needs one ground redo after its features (I-040). Features only need ghost ground, so `next`
  could build features first and save that redo. Should the order become features → ground?
- **Trees/decor after a ground redo.** Should a later ground also make trees/decor stale? That
  reverses the documented "ground redo preserves trees/decor completion".
- **Undo after ground.** An undo of a feature that lands after the ground restores pre-feature
  blocks (16:27 → 16:31 in 48,-44). That is not counted as a cause here.

# #284: every preflight blocker names the command that clears it (2026-11-14)

## Diagnosis (read-only, southampton-build, cell 48,-44)

- The blocker is the latest West entry, `west_w34391673` seq 431 (2026-10-29), status `mismatched`
  with 2 mismatches. Seq 419 (2026-10-28) failed the same way and was undone. Both mismatches are
  the same spruce door pair at 6205,51..52,-5605: the journal wants `open=false` and the world holds
  `open=true`. Seq 431 still owns both positions, so no later plan supersedes them.
- `reconcile 48,-44` (dry run) now reports 35 foreign positions (the task saw 30). Two are those
  doors. The other 33 belong to ground plans (seqs 188, 238, 359, 445), which want `grass_block`
  where the world holds `dirt`. Every one of the 33 has a block on top: dirt from a later ground
  plan (the x 6144 y 36 rows at the cell's west edge), an oak log from trees, or spruce stairs from
  decor. The game turns covered grass into dirt, so re-writing grass cannot hold. None of the 33 is
  a preflight blocker; they only keep reconcile from reporting clean.
- The journal shows a `reconcile --fix` repair today (seq 918, 894 positions in 48,-44). It upgraded
  an old ground entry but not seq 431, so the doors still read back open after the repair.
- The deadlock: preflight blocks every build in the cell on the West mismatch, including West's own
  re-apply. `reapply_feature.py` calls `require_feature_ready`, and `element_undo.py` runs a
  readiness check before undoing, so the plan that could replace the mismatch was refused as well.
  When `reconcile --fix` cannot make the blocks hold, no sanctioned command is left.

## Rule (task options (c) plus (b), with named commands)

1. **A plan may be redone past its own mismatch.** Readiness takes `redoing` (plan names). Only
   that plan's own mismatched apply is skipped; every other blocker still applies. The redo's undo
   and new readback replace the entry: a landed redo clears the blocker, and a mismatched one blocks
   again. The callers are `enforce` (the build's own plan), `require_feature_ready` and
   `reapply_feature.py` (the feature), `element_undo.py` (the plans it undoes) and `redo_layer.py`
   (the layer's plans in the cell).
2. **A wholly superseded mismatch blocks nothing.** If later landed entries own every position of
   a mismatched entry (`CellRegistry.owns_any` is false), it is not a blocker. This check uses the
   journal fold only and reads no world blocks. A partly superseded mismatch still blocks.
3. **Each blocker names its clearing command.** `check()` returns `clear_with`, which maps each
   blocker to a command. `format_readiness` prints `clear: <blocker> -> <command>`. Refusals
   (`needs-prep ...`) print `<blocker> [clear: <command>]`.
   - For a mismatched apply, the command is `cell_registry.py reconcile <cell> --fix`. If that run
     still reports the entry mismatched, the next command is `reapply_feature.py <plan> --apply`
     for a feature, or `redo_layer.py --cell <cell> --layers <pass> --apply` for a layer.
4. **Reconcile reports each mismatched entry as its own finding** (kind `mismatched`), so its output
   says which blocker cleared and which did not:
   - A dry run names `reconcile <cell> --fix`.
   - In `--fix` output, an upgraded entry appears in `upgraded N mismatched entries to landed:
     <plan> (seq N) (preflight no longer blocks on it)`.
   - An entry still mismatched after the fix prints `still mismatched after repair` with the
     re-apply command and every cell the entry touches.

Option (a) was already in the code: `reconcile --fix` re-asserts foreign positions and then upgrades
the entry. It was not enough on its own, because game-reshaped blocks cannot be made to hold.

## Cost

`owns_any` folds a cell's ownership (a few seconds cold) only for a cell with a live mismatched
entry, and it stops at the first owned position. Warm checks are unchanged (about 0.9 s on 48,-44).

## Not done (open questions for the owner)

- **Door state.** Should `open` and `powered` on doors be treated as play state in `same_state`,
  like stair `shape` (#161)? The existing tests deliberately keep fence-gate `open` significant, so
  that tolerance was not added here. If the doors re-open after a re-apply, West will mismatch again.
- **Covered grass.** Ground ownership of grass that a later plan's block now covers is stale (the
  game makes it dirt). Fixing it needs a change to the fold (a ground supersession rule like the
  ghost one). That is journal semantics, so it belongs in its own task.

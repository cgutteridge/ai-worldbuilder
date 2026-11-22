---
name: sherlock
description: Investigator and case-closer for the issues backlog. Works through stages: diagnose causes, housekeep snags and tasks, run the owner's fix reviews until none are left, then decide whether code, builds or both come next (rebuilds wait for the tasks that would change them), hand off to new threads and mark the thread complete. Use when the user says sherlock, asks what's wrong with the issues, what to fix next, or wants the backlog worked through to a clean stop.
---

Tone: Sherlock Holmes, lightly. An occasional "the game is afoot" or "elementary", one deduction per report, no costume drama. Never in the data, never in a question the owner has to answer.

**Never** run design, builds, purges or undos, and never change code. Those belong to the handoff threads at the end. You read live `registry/issues.json` and archived `registry/issues-closed.json` (`issues.py list|show|match|digest|verify`; list/digest use live records, show/match also find archived ones), the cell journal, the code and the evidence. You may write to the issue records (`diagnose`, `move`, `merge`, `wontfix`) and the task tracker **only with the owner's permission**, one proposal at a time. `confirmed` and `wontfix` are the owner's calls; propose them. Logging a merged task's fix (`fix`, stage 2) and archiving already closed issues (Close the case, step 2) run without asking; they record completed decisions.

The job is: do everything the AI can do by itself, in this order, then stop and hand off. Work each stage until it is finished before moving on.

## Stages and thread name

The thread name is always an emoji, a space, then a short plain name for the case (e.g. `🔍 Cell 3,-2 roofs and ghost fixes`). Set it with `set_session_title` and `"self"` (skip quietly where the tool is missing). The emoji is the stage:

- 🔍 **Investigating.** Finding causes. Set it first, when the run starts.
- 🔧 **Reviewing task and record changes.** Housekeeping of snags and tasks: proposals for diagnoses, moves, merges, wontfix, and for tracker tasks, taken to the owner one at a time.
- 🕵️ **Checking fixes.** Issues at `fix-landed` are walked through with the owner in game (the `snag` skill, section B), until none is left.
- ✅ **Complete.** Everything the AI can do without a build or a task running is done and committed, and the gate's verdict and the handoffs it calls for are given.

Stages can be revisited: a review that fails, a new snag or a new finding sends you back to 🔍 or 🔧; set the emoji to match and carry on. Keep the name part current as the case sharpens; change the emoji only when the stage changes.

## 1. 🔍 Investigate

1. **Orient.** Name the thread 🔍. `issues.py digest`, `list`, then `show` the issues in play. Regressions (reopened issues, snags flagged REGRESSION) come first.
2. **Find causes.** For each `open` issue without a cause: read the code path named by the stage, the plan, the evidence. State the cause plainly, with the evidence that supports it and what you could not verify. Propose it via `issues.py diagnose --cause ... --stage ...`. Stages are free text (e.g. "feature windows", "ground classify"). The proposal waits for stage 2.

### Escalation

When available evidence still leaves competing causes or an unexplained result,
follow `docs/agents/model-and-effort.md` before requesting a stronger model.
Give the owner the checked evidence, unresolved question, proposed model/effort,
cost and observable success criterion. Await approval; continue independent
issues while the diagnosis remains unresolved. Missing evidence calls for the
capture or measurement first. The investigation brief carries the issue/snags,
code paths, findings, hypotheses and failed checks; check returned reasoning
against the evidence before proposing a diagnosis.

## 2. 🔧 Housekeeping: snags, issues and tasks

Switch the thread to 🔧 as soon as you have proposals. Take them to the owner one at a time, each on its own question:

- Diagnoses to record (`diagnose`).
- **Regroup:** this snag has a different cause, move it out (`move`); these two issues are the same case, merge them (`merge`); this issue is not worth fixing (`wontfix`); this one looks resolved by work already merged (check its fix log and `sync`).
- **Fixes logged and tasks landed:** a task now merged whose fix is not yet logged on its issue: **log it yourself, no approval** (`fix` with the merge time, the ticket, and a check line per snag, including agent notes). It only records that the issue was waiting on the task before a rebuild and the owner's recheck; it changes no build and settles nothing, so do not spend the owner's time on it. Report what you logged in one line each. Ask only if the task's link to the issue is unclear. A fix logged but the element not redesigned since: say which element needs a `redo`/`refine` (the gate's rebuild list, stage 4).
- **Tasks:** for causes with no fix yet, draft system-tracker tasks (title, cause, which snags/issue they cover, the check line per snag). Review the open task list for stale, duplicate or unblocked tasks. Tool tasks go to `tasks/`; place work does not.

  **Independent review before the owner sees a draft.** Every new or changed task is reviewed by a fresh subagent that has not seen your reasoning, before you put it to the owner. Give it only the draft, the issue and snag text, and the paths to read (`tasks/index.json`, `tasks/done.json`, the code the cause names, the rules and skills it would touch); do not hand it your conclusion. Ask it to check: the cause is stated from evidence, and what is not verified is marked as such; the deliverable is specific and testable, one task not several; scope and files named, with cleanup of old paths included where the task replaces something; no duplicate of or conflict with an open or archived task (`blockedBy` right); the check line per snag is observable in game or by a named command; size and priority fit; wording is plain, title first. It returns a verdict (ready / revise) with concrete edits. Apply the edits, say what the reviewer changed, and then put the revised draft to the owner. If it finds the premise wrong, say so and drop or reshape the task. Add or change with `worldbuilder-tasks` only when the owner says yes to the reviewed draft.

Apply each accepted change. Commit nothing yet beyond what Close the case says.

## 3. 🕵️ Check fixes

Switch to 🕵️. `issues.py sync`, then `digest`. For every issue at `fix-landed`, run the `snag` skill's survey (section B): grouping check first, then one card per snag, one question at a time. Keep going until no issue is left at `fix-landed`, or the owner stops. A "not fixed" verdict that shows a new cause sends you back to 🔍; a task idea sends you to 🔧.

Issues that are only `open` are never reviewed; if their fix is logged but the element is `not-rebuilt`, they enter the gate's rebuild list (stage 4).

## 4. The gate, then handoffs, then ✅

When nothing is left that the AI can do without a build or a task running, run the **gate**: decide whether the owner's next move is code, builds, both or neither. A rebuild is **held** while a pending task will change what it builds; building then spends the owner's review on output about to be replaced.

1. **List the rebuilds wanted**: every element with a landed fix not yet rebuilt (`not-rebuilt`), every element a diagnosis names for rebuild, and the redesigns the open issues need. Done when you hold a named list, even if empty.
2. **List the pending work**: tasks in `tasks/index.json` that are `todo`, `doing` or `blocked`, plus any task you added this run. Done when the list is read from the file, even if empty.
3. **Match.** A task **reaches** an element when it names the element's issue, or edits the code stage the issue's `stages` field names (tasks name their files and issues in their notes). Where a match is unclear, ask the owner one question per pair, one at a time, with both titles: "will [task title] (#id) change how [element title] is built?" Done when every pending task is checked against every rebuild and every pair is clear.
4. **Sort each rebuild** into one class:
   - **evidence**: a diagnosis or task needs fresh output to test a hypothesis, even with work pending; name the question the rebuild answers. Evidence wins over held. When a pending task also reaches the element, add a **rebuild again** entry to the held list, released by that task.
   - **held**: a pending task reaches it; name that task, title first, then ID. A held element is rebuilt once, after the last task that reaches it. When every task holding it is `blocked` on something outside the tool (owner input, a data source), ask the owner whether to wait or build; a "build" moves it to the build set and adds a **rebuild again** entry to the held list, released by that task.
   - **ready**: no pending task reaches it.

   Done when each rebuild sits in exactly one class.
5. **Verdict.** The **build set** is the ready and evidence rebuilds. The **task set** is the pending tasks that reach a rebuild or fix an open issue, plus tasks added this run. Tasks in progress (`doing`) are named in the tasks handoff as running elsewhere, for the owner to wait on; when every task in the set is `doing`, the tasks handoff is that wait list plus the held list, and the owner needs no new thread for it. Backlog tasks tied to no issue in play stay out of the task set.
   - **builds first**: build set non-empty, task set empty.
   - **code first**: task set non-empty, build set empty (every rebuild held, or none wanted).
   - **both**: both sets non-empty; no pending task reaches the ready rebuilds, so tasks and builds can run in parallel; evidence rebuilds are built now and rebuilt again after their task, and the held list waits for its tasks.
   - **neither**: both sets empty; say "no handoffs; the case is closed".

   Give the verdict with one line of reason, titles first (e.g. "code first: the shop street-face rules (#331) and fit-straight-runs (#332) change every shop front"). If the owner chooses a different verdict, re-sort the rebuilds and give the handoffs the chosen verdict calls for; any held entry or task that handoff set leaves out is named in the reply as set aside, with what releases it.

Give only the handoffs the verdict calls for, as plain copyable text in the reply (one fenced block each), for the owner to paste into new threads. Keep them in the reply; files are for the records and tracker only. Keep them short: most of the detail already lives in the tasks and issues, so reference them by title, then ID. Plain titles first, always.

1. **Builds handoff** (the build set, for a thread that orchestrates rebuilds): the rebuilds in order (feature plans, ground/trees/decor per cell), what each is for (the issues and check lines it makes reviewable, or the hypothesis it tests), cautions (known faults that will recur, other sessions touching the world, site lock). End with "after the rebuilds, run `sherlock` again to check the fixes".
2. **Tasks handoff** (the task set, for a thread that orchestrates the tool backlog, usually with `parallel-task-wave`): the tasks to work, in order, with blocked-by relations, which can run in parallel, which are spikes, and which issues each one unblocks or fixes. The thread logs each fix on its issue (`issues.py fix`) with a check line per snag. Whenever the **held** list has entries (the held class plus the rebuild-again follow-ups), add it, each entry with the task that releases it, and the line "when these merge, run `sherlock` again: it re-runs the gate and releases the held rebuilds".

Each return to `sherlock` re-runs the gate, so held rebuilds become ready as their tasks merge.

## Close the case

Committing your own records is part of the job, not a code change.

1. **Verify.** `issues.py verify` must say OK; if not, report it and stop here, and say the handoffs given in stage 4 are provisional until verify passes.
2. **Archive closed issues automatically.** Run `python3 scripts/issues.py --world <world> archive`, then `python3 scripts/issues.py --world <world> verify` again. If either fails, report it and stop here. Report one line: "archived N closed issues". This moves confirmed/wontfix records to `registry/issues-closed.json` and their pictures to `registry/issues/img-closed/`; open/fix-landed records stay live.
3. **Commit the world's issue records only.** The world checkout is shared with other sessions and its index may already hold their changes. From `registry/issues.json`, `registry/issues-closed.json`, `registry/issues/img`, and `registry/issues/img-closed`, select only paths that exist, then name that exact set in both `git -C <world> add <paths>` and `git -C <world> commit -m "Issues: <what this run changed>" -- <paths>`. Leave `_intake-pending.png` (the snag skill's scratch) and everything else alone. Push only to an explicitly authorised world destination; a configured remote alone is not authorisation.
4. **Commit tracker edits** (tasks you added with the owner's yes) straight to `main` in the tool repo. Do not commit tracker files other sessions have left modified. Push only to an explicitly authorised destination. A push the hooks or remote refuse is reported, not forced.
5. **Rename the thread** with ✅ once steps 1-4 have committed everything and the gate's handoffs are given (for "neither", once the verdict is stated).
6. **Wrap up in Holmes's voice, lightly**: one deduction, then what the owner does next, by name, following the verdict: paste the tasks handoff into a new thread (code first), the builds handoff (builds first), or both into separate threads, then call sherlock back to re-run the gate and check the fixes. For "neither": "no further work; the case is closed".
7. Play the `done` sound cue.

## Name things, never just number them

An ID alone (I-004, S-011, #118, "task 2") means nothing to the owner. Every mention, in reports, sequences, handoffs and above all in a question the owner must answer, gives the plain title first and the ID after: "log the vector-map fix (#113) on the plaza-and-paths issue (I-001)?", not "log #113 on I-001?". A question must stand on its own: say what the action does and what it touches, so the owner can answer without scrolling back.

## Grouping check before a review

When `snag` is about to review an issue, check each snag still belongs: same cause, same fix, same element family. Report any misfiled snag and any issue that should merge; apply nothing without a yes.

## Report shape

Short: the digest line, then findings by issue (plain title first, ID small), each with the evidence, the proposed action, and what you could not establish. If the backlog is empty or all confirmed, say so and run Close the case so verification and automatic archiving still happen.

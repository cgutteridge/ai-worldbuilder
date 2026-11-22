import { mkdir, mkdtemp, readFile, writeFile, rm, readdir } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { fileURLToPath } from "node:url"
import { spawnSync } from "node:child_process"
import { afterEach, beforeEach, describe, it } from "node:test"
import assert from "node:assert/strict"
import {
  addTask,
  checkTaskRun,
  closeTask,
  defaultTrackerDir,
  editSpans,
  io,
  listReadyTasks,
  parseTaskId,
  readTrackerFile,
  replaceTrackerFile,
  setTaskStatus,
  showTask,
  updateTask,
} from "./tasks.mjs"

const script = fileURLToPath(new URL("./tasks.mjs", import.meta.url))
// Adapted from the local-tasks kit tests; all trackers and commits are synthetic.
function git(directory, ...args) {
  const result = spawnSync("git", args, { cwd: directory, encoding: "utf8" })
  assert.equal(result.status, 0, result.stderr)
  return result.stdout.trim()
}

const directories = []
async function temporaryDirectory(prefix) {
  const directory = await mkdtemp(join(tmpdir(), prefix))
  directories.push(directory)
  return directory
}
afterEach(async () => {
  await Promise.all(
    directories
      .splice(0)
      .map((path) => rm(path, { recursive: true, force: true })),
  )
})
function cli(args, cwd) {
  return spawnSync(process.execPath, [script, ...args], {
    cwd,
    encoding: "utf8",
  })
}
// Checks a rejection's message contains `text`, like Vitest's toThrow(text).
const rejectsWith = (promise, text) =>
  assert.rejects(promise, (error) => {
    assert.ok(
      error.message.includes(text),
      `expected "${error.message}" to contain "${text}"`,
    )
    return true
  })
const rejectsWithExit = (promise, exitCode) =>
  assert.rejects(promise, (error) => {
    assert.equal(error.exitCode, exitCode)
    return true
  })

const claim = { app: "cursor", workingIn: "/tmp/ai-worldbuilder" }
const claimArgs = ["--app", "cursor", "--working-in", "/tmp/ai-worldbuilder"]

describe("task reads and status changes", () => {
  const task = (id, extra = {}) => ({
    id,
    title: `Task ${id}`,
    status: "todo",
    priority: "high",
    complexity: "s",
    blockedBy: [],
    tags: ["ready-for-agent"],
    notes: "Made-up notes",
    ...extra,
  })
  async function tracker(tasks = [task(1)], done) {
    const directory = await temporaryDirectory("task-cli-")
    await writeFile(
      join(directory, "index.json"),
      JSON.stringify({ _comment: "Keep this", nextTaskId: 100, tasks }),
    )
    if (done)
      await writeFile(
        join(directory, "done.json"),
        JSON.stringify({ tasks: done }),
      )
    return directory
  }
  const contents = (directory) =>
    readFile(join(directory, "index.json"), "utf8")
  const run = (directory, args, cwd) =>
    cli(["--tracker-dir", directory, ...args], cwd)

  describe("compact task reads", () => {
    it("prints bounded ready lines without notes or reading done.json", async () => {
      const directory = await tracker([
        task(1, {
          title: `Long\n${"title".repeat(1000)}`,
          notes: "secretly long".repeat(10000),
        }),
        task(2, { blockedBy: [3] }),
        task(3, { status: "doing" }),
        task(4, { tags: [] }),
        task(5, { status: "blocked" }),
        task(6, { blockedBy: [80] }),
      ])
      // A broken completed file cannot affect active reads.
      await writeFile(join(directory, "done.json"), "not JSON")
      const before = await contents(directory)
      assert.deepEqual(
        (await listReadyTasks(directory)).map((t) => t.id),
        [1],
      )
      const result = run(directory, ["list", "--ready"], tmpdir())
      assert.equal(result.status, 0)
      const lines = result.stdout.trim().split("\n")
      assert.equal(lines.length, 1)
      assert.ok(lines[0].length <= 127)
      assert.ok(!result.stdout.includes("secretly long"))
      assert.equal(await contents(directory), before)
      assert.deepEqual(await readdir(directory), ["done.json", "index.json"])
    })

    it("does not require triage tags when no active task has a tags field", async () => {
      const untagged = (id, extra = {}) => {
        const { tags, ...rest } = task(id, extra)
        return rest
      }
      const directory = await tracker([
        untagged(1),
        untagged(2, { blockedBy: [1] }),
        untagged(3, { status: "doing" }),
      ])
      assert.deepEqual(
        (await listReadyTasks(directory)).map((t) => t.id),
        [1],
      )
    })

    it("shows exactly one complete active or completed task, including unknown fields", async () => {
      const active = task(1, {
        custom: { detail: "retained" },
        notes: "long".repeat(2000),
      })
      const done = task(2, { status: "done" })
      const directory = await tracker([active], [done])
      assert.deepEqual(await showTask(directory, 1), active)
      assert.deepEqual(JSON.parse(run(directory, ["show", "1"]).stdout), active)
      assert.deepEqual(await showTask(directory, 2), done)
      await rejectsWithExit(showTask(directory, 3), 3)
      await writeFile(join(directory, "done.json"), "broken")
      assert.deepEqual(await showTask(directory, 1), active)
      await rejectsWith(showTask(directory, 2), "not valid JSON")
    })

    for (const value of [
      "",
      "0",
      "-1",
      "1.5",
      "1e2",
      "01",
      " 1",
      "9007199254740992",
    ]) {
      it(`rejects invalid id ${JSON.stringify(value)}`, () => {
        assert.throws(() => parseTaskId(value), /positive whole number/)
      })
    }
  })

  describe("validated tracker snapshots", () => {
    for (const source of [
      "{",
      "null",
      "[]",
      '{"tasks":[]}',
      '{"nextTaskId":100,"tasks":[{"id":1}]}',
      JSON.stringify({ nextTaskId: 100, tasks: [task(1), task(1)] }),
      JSON.stringify({ nextTaskId: 1, tasks: [task(1)] }),
      JSON.stringify({ nextTaskId: 100, tasks: [task(1, { status: "done" })] }),
      JSON.stringify({
        nextTaskId: 100,
        tasks: [task(1, { blockedBy: ["2"] })],
      }),
      '{"nextTaskId":100,"tasks":[],"tasks":[]}',
      '{"nextTaskId":100,"tasks":[],"extra":{"id":1,"id":2}}',
    ]) {
      it(`refuses malformed JSON, schema and duplicates: ${source}`, async () => {
        const directory = await tracker()
        await writeFile(join(directory, "index.json"), source)
        await assert.rejects(readTrackerFile(directory, "index.json"))
        assert.equal(await contents(directory), source)
      })
    }

    it("rejects invalid completed status, duplicate ids and invalid UTF-8", async () => {
      const directory = await tracker([], [task(1)])
      await rejectsWith(readTrackerFile(directory, "done.json"), "lifecycle")
      await writeFile(
        join(directory, "done.json"),
        JSON.stringify({
          tasks: [task(1, { status: "done" }), task(1, { status: "done" })],
        }),
      )
      await rejectsWith(readTrackerFile(directory, "done.json"), "duplicate")
      await writeFile(join(directory, "index.json"), Buffer.from([0xff]))
      await rejectsWith(readTrackerFile(directory, "index.json"), "UTF-8")
    })

    it("refuses stale snapshots, cleans temporary files and preserves intervening changes", async () => {
      const directory = await tracker()
      const snapshot = await readTrackerFile(directory, "index.json")
      const changed = snapshot.text.replace("Keep this", "Keep THAT")
      await writeFile(snapshot.path, changed)
      await rejectsWith(
        replaceTrackerFile(snapshot, snapshot.text),
        "changed since",
      )
      assert.equal(await contents(directory), changed)
      assert.deepEqual(await readdir(directory), ["index.json"])
    })

    it("serialises writers and refuses invalid replacements without changing the file", async () => {
      const directory = await tracker()
      const snapshot = await readTrackerFile(directory, "index.json")
      await rejectsWith(replaceTrackerFile(snapshot, "{}"), "schema")
      const writes = await Promise.allSettled([
        replaceTrackerFile(
          snapshot,
          snapshot.text.replace('"todo"', '"doing"'),
        ),
        replaceTrackerFile(
          snapshot,
          snapshot.text.replace('"todo"', '"doing"'),
        ),
      ])
      assert.deepEqual(writes.map((result) => result.status).sort(), [
        "fulfilled",
        "rejected",
      ])
      assert.equal(
        (await readTrackerFile(directory, "index.json")).tasks[0].status,
        "doing",
      )
      assert.deepEqual(await readdir(directory), ["index.json"])
    })

    it("refuses an existing write lock without removing it", async () => {
      const directory = await tracker()
      const before = await contents(directory)
      await writeFile(join(directory, "index.json.lock"), "another writer")
      await rejectsWith(setTaskStatus(directory, 1, "doing", undefined, undefined, claim), "is locked")
      assert.equal(await contents(directory), before)
      assert.equal(
        await readFile(join(directory, "index.json.lock"), "utf8"),
        "another writer",
      )
    })

    it("rejects overlapping edits", () => {
      assert.throws(
        () =>
          editSpans("abcdef", [
            { span: { start: 0, end: 4 }, text: "x" },
            { span: { start: 3, end: 5 }, text: "y" },
          ]),
        /overlapping/,
      )
    })
  })

  describe("precise status changes", () => {
    for (const blocker of [80, 100, 101]) {
      it(`keeps a missing or unallocated blocker ${blocker} unresolved`, async () => {
        const directory = await tracker([task(1, { blockedBy: [blocker] })])
        const before = await contents(directory)
        assert.deepEqual(await listReadyTasks(directory), [])
        await rejectsWith(
          setTaskStatus(directory, 1, "doing", undefined, undefined, claim),
          "unresolved blockers",
        )
        assert.equal(await contents(directory), before)
      })
    }

    it("preserves every unrelated byte, comments, fields, ordering and nextTaskId", async () => {
      const directory = await tracker()
      const source =
        '{\r\n "_comment":"Café 😀", "nextTaskId":100, "unknown": [true, null, -2.1e3],\r\n "tasks" : [ {"title":"A \\"quote\\"", "id":1, "status" : "todo", "odd": {"x":[]}},\r\n {"id":2,"title":"Other","status":"todo","notes":"doing"} ]\r\n}\r\n'
      await writeFile(join(directory, "index.json"), source)
      await setTaskStatus(directory, 1, "doing", undefined, undefined, claim)
      assert.equal(
        await contents(directory),
        source
          .replace('"status" : "todo"', '"status" : "doing"')
          .replace(
            '"odd": {"x":[]}}',
            `"odd": {"x":[]}, "claimedBy": ${JSON.stringify(claim.app)}, "workingIn": ${JSON.stringify(claim.workingIn)}}`,
          ),
      )
    })

    it("requires a short return reason and appends attributed notes without losing existing notes", async () => {
      const directory = await tracker([task(1, { status: "doing" }), task(2)])
      const before = await contents(directory)
      for (const reason of [undefined, "   ", "x".repeat(301)]) {
        await rejectsWithExit(setTaskStatus(directory, 1, "todo", reason), 2)
        assert.equal(await contents(directory), before)
      }
      const updated = await setTaskStatus(
        directory,
        1,
        "todo",
        " Waiting for owner input. ",
      )
      assert.equal(
        updated.notes,
        "Made-up notes\n\nReturned to todo: Waiting for owner input.\n\n[tasks CLI] Status update recorded; subject to human review.",
      )
      assert.equal(
        await contents(directory),
        before
          .replace('"status":"doing"', '"status":"todo"')
          .replace(
            '"notes":"Made-up notes"',
            `"notes":${JSON.stringify(updated.notes)}`,
          ),
      )
    })

    it("can add notes, handles escaped property names and leaves same-status updates untouched", async () => {
      const directory = await tracker()
      const source =
        `{"nextTaskId":100,"tasks":[{"id":1,"title":"A","sta\\u0074us":"doing","claimedBy":${JSON.stringify(claim.app)},"workingIn":${JSON.stringify(claim.workingIn)}  }]}\n`
      await writeFile(join(directory, "index.json"), source)
      const before = await readTrackerFile(directory, "index.json")
      await setTaskStatus(directory, 1, "doing", undefined, undefined, claim)
      assert.equal(
        (await readTrackerFile(directory, "index.json")).identity,
        before.identity,
      )
      await setTaskStatus(directory, 1, "todo", "Reprioritised.")
      assert.ok((await showTask(directory, 1)).notes.includes("Reprioritised."))
      const after = await showTask(directory, 1)
      assert.equal(after.claimedBy, undefined)
      assert.equal(after.workingIn, undefined)
      assert.ok(
        (await contents(directory)).includes(
          '"sta\\u0074us":"todo"',
        ),
      )
    })

    it("refuses review findings, blocked starts, unsupported statuses and missing ids", async () => {
      const directory = await tracker([
        task(1, { kind: "review-finding" }),
        task(2, { blockedBy: [1] }),
        task(3, { status: "blocked" }),
      ])
      const before = await contents(directory)
      await rejectsWith(setTaskStatus(directory, 1, "doing", undefined, undefined, claim), "reviewer workflow")
      await rejectsWith(setTaskStatus(directory, 1, "todo"), "reviewer workflow")
      await rejectsWith(
        setTaskStatus(directory, 2, "doing", undefined, undefined, claim),
        "unresolved blockers",
      )
      await rejectsWith(
        setTaskStatus(directory, 3, "todo", "Ready"),
        "Only todo and doing",
      )
      await rejectsWithExit(setTaskStatus(directory, 4, "doing", undefined, undefined, claim), 3)
      await rejectsWithExit(setTaskStatus(directory, 0, "doing", undefined, undefined, claim), 2)
      assert.equal(await contents(directory), before)
    })

    it("insists on claim app and working path when starting, and clears them on cancel", async () => {
      const directory = await tracker([task(1)])
      const before = await contents(directory)
      for (const bad of [
        undefined,
        {},
        { app: "cursor" },
        { workingIn: "/tmp/x" },
        { app: "claude-code", workingIn: "/tmp/x" },
        { app: "cursor", workingIn: "  " },
      ]) {
        await rejectsWithExit(
          setTaskStatus(directory, 1, "doing", undefined, undefined, bad),
          2,
        )
        assert.equal(await contents(directory), before)
      }
      const started = await setTaskStatus(
        directory,
        1,
        "doing",
        undefined,
        undefined,
        { app: "codex", workingIn: "/tmp/worktree-438" },
      )
      assert.equal(started.claimedBy, "codex")
      assert.equal(started.workingIn, "/tmp/worktree-438")
      assert.deepEqual(await showTask(directory, 1), started)
      const cancelled = await setTaskStatus(
        directory,
        1,
        "todo",
        "Paused",
        undefined,
        { branch: "task/438-claim-status-fields" },
      )
      assert.equal(cancelled.claimedBy, undefined)
      assert.equal(cancelled.workingIn, undefined)
      assert.match(
        cancelled.notes,
        /Returned to todo: Paused Half-done on branch task\/438-claim-status-fields\./,
      )
      const shown = await showTask(directory, 1)
      assert.equal(shown.claimedBy, undefined)
      assert.equal(shown.workingIn, undefined)
      assert.equal(
        run(directory, ["status", "1", "doing"]).status,
        2,
      )
      assert.equal(
        run(directory, [
          "status",
          "1",
          "doing",
          "--app",
          "claude",
          "--working-in",
          "/Users/cjg/Projects/ai-worldbuilder",
        ]).status,
        0,
      )
      assert.equal((await showTask(directory, 1)).claimedBy, "claude")
    })

    it("uses predictable CLI exit codes without printing raw contents or stack traces", async () => {
      const directory = await tracker([], [])
      assert.equal(run(directory, ["show", "1"]).status, 3)
      assert.equal(run(directory, ["show", "bad"]).status, 2)
      assert.equal(run(directory, ["status", "1", "done"]).status, 2)
      assert.equal(run(directory, ["unknown"]).status, 2)
      assert.equal(run(directory, ["--help"]).status, 0)
      await writeFile(
        join(directory, "index.json"),
        "sensitive malformed content",
      )
      const result = run(directory, ["list", "--ready"])
      assert.equal(result.status, 1)
      assert.equal(result.stderr, "index.json is not valid JSON.\n")
      assert.equal(result.stdout, "")
    })
  })
})

describe("task addition", () => {
  const fields = {
    title: "New task",
    priority: "medium",
    complexity: "s",
    notes: "Details",
    builderTask: false,
  }
  async function fixture(tasks = [{ id: 1, title: "Existing", status: "todo" }]) {
    const directory = await temporaryDirectory("task-add-")
    await writeFile(
      join(directory, "index.json"),
      JSON.stringify({ _comment: "Keep active", nextTaskId: 100, tasks }, null, 2) + "\n",
    )
    await writeFile(
      join(directory, "done.json"),
      '{"_comment":"Keep done", "tasks":[{"id":9,"title":"Completed","status":"done"}]}\n',
    )
    return directory
  }
  const text = (directory, name = "index.json") => readFile(join(directory, name), "utf8")
  const run = (directory, extra = ["--notes", "Details"]) => cli([
    "--tracker-dir", directory, "add",
    "--title", fields.title, "--priority", fields.priority,
    "--complexity", fields.complexity, "--builder-task", "false", ...extra,
  ], tmpdir())

  it("assigns ids, advances nextTaskId, preserves field order and round-trips through show", async () => {
    const directory = await fixture()
    const done = await text(directory, "done.json")
    const result = run(directory)
    assert.equal(result.status, 0, result.stderr)
    assert.equal(result.stdout, "100\n")
    const shown = cli(["--tracker-dir", directory, "show", "100"], tmpdir())
    assert.equal(shown.status, 0, shown.stderr)
    const task = JSON.parse(shown.stdout)
    assert.deepEqual(task, { id: 100, title: fields.title, status: "todo", priority: "medium", complexity: "s", blockedBy: [], builderTask: false, notes: "Details" })
    assert.deepEqual(Object.keys(task), ["id", "title", "status", "priority", "complexity", "blockedBy", "builderTask", "notes"])
    assert.equal(run(directory).stdout, "101\n")
    assert.equal(JSON.parse(await text(directory)).nextTaskId, 102)
    assert.equal(await text(directory, "done.json"), done)
    assert.deepEqual((await readdir(directory)).sort(), ["done.json", "index.json"])
  })

  it("changes only nextTaskId and the appended bytes, retaining comments, escapes and whitespace", async () => {
    const directory = await fixture()
    const source = '{\r\n "_comment":"Café 😀", "nextTaskId" : 100, "extra":-2.1e3,\r\n "tasks":[ {"id":1,"title":"Keep \\"quotes\\"","status":"todo", "unknown":[true,null]} ]\r\n}\r\n'
    await writeFile(join(directory, "index.json"), source)
    const result = run(directory)
    assert.equal(result.status, 0, result.stderr)
    const appended = ',\n    {\n      "id": 100,\n      "title": "New task",\n      "status": "todo",\n      "priority": "medium",\n      "complexity": "s",\n      "blockedBy": [],\n      "builderTask": false,\n      "notes": "Details"\n    }'
    const after = await text(directory)
    assert.ok(after.includes(appended))
    assert.equal(after.replace(appended, "").replace('"nextTaskId" : 101', '"nextTaskId" : 100'), source)
  })

  it("can append to an empty array and read notes from a file without trimming", async () => {
    const directory = await fixture([])
    const notes = 'Long notes\nwith "quotes" and Café 😀\n' + "detail".repeat(3000)
    const path = join(directory, "notes.txt")
    await writeFile(path, notes)
    const result = run(directory, ["--notes-file", path])
    assert.equal(result.status, 0, result.stderr)
    assert.equal((await showTask(directory, 100)).notes, notes)
    assert.equal(JSON.parse(await text(directory)).nextTaskId, 101)
  })

  it("preserves the existing validator's title and notes length rules", async () => {
    const directory = await fixture()
    const title = "Title".repeat(2000)
    const task = await addTask(directory, { ...fields, title, notes: "" })
    assert.equal(task.title, title)
    assert.equal(task.notes, "")
    assert.deepEqual(await showTask(directory, task.id), task)
  })

  it("recognises completed blockers as resolved and retains active blockers", async () => {
    const directory = await fixture()
    const result = run(directory, ["--notes", "Details", "--blocked-by", "1,9"])
    assert.equal(result.status, 0, result.stderr)
    const task = await showTask(directory, 100)
    assert.deepEqual(task.blockedBy, [1])
    assert.equal(task.status, "todo")
    assert.ok(!(await listReadyTasks(directory)).some((item) => item.id === 100))
  })

  it("makes tasks with only completed blockers ready and able to start", async () => {
    const directory = await fixture([])
    const task = await addTask(directory, { ...fields, blockedBy: [9] })
    assert.deepEqual(task.blockedBy, [])
    assert.deepEqual((await showTask(directory, task.id)).blockedBy, [])
    assert.deepEqual((await listReadyTasks(directory)).map((item) => item.id), [task.id])
    assert.equal((await setTaskStatus(directory, task.id, "doing", undefined, undefined, claim)).status, "doing")
    assert.equal((await showTask(directory, task.id)).status, "doing")
  })

  for (const value of ["", "0", "-1", "1.5", "1,,9", "99", "100", "999"]) {
    it(`refuses invalid or unknown blockers ${JSON.stringify(value)} without writes`, async () => {
      const directory = await fixture()
      const before = await text(directory)
      const done = await text(directory, "done.json")
      const result = run(directory, ["--notes", "Details", "--blocked-by", value])
      assert.equal(result.status, 2)
      assert.equal(result.stdout, "")
      assert.match(result.stderr, /positive whole number|Unknown blocker/)
      assert.equal(await text(directory), before)
      assert.equal(await text(directory, "done.json"), done)
      assert.deepEqual((await readdir(directory)).sort(), ["done.json", "index.json"])
    })
  }

  for (const extra of [
    [],
    ["--notes", "Details", "--notes-file", "some-file"],
    ["--notes", "Details", "--title", ""],
    ["--notes", "Details", "--priority", "critical"],
    ["--notes", "Details", "--priority", "invalid"],
    ["--notes", "Details", "--complexity", "xs"],
    ["--notes", "Details", "--complexity", "invalid"],
  ]) {
    it(`refuses invalid addition arguments ${JSON.stringify(extra)} without writes`, async () => {
      const directory = await fixture()
      const before = await text(directory)
      const result = run(directory, extra)
      assert.equal(result.status, 2)
      assert.equal(result.stdout, "")
      assert.equal(await text(directory), before)
    })
  }

  it("refuses an unreadable notes file without allocating an id", async () => {
    const directory = await fixture()
    const before = await text(directory)
    const result = run(directory, ["--notes-file", join(directory, "missing.txt")])
    assert.equal(result.status, 1)
    assert.equal(result.stderr, "Could not read notes file.\n")
    assert.equal(await text(directory), before)
  })

  it("refuses a stale counter or duplicate ids across trackers", async () => {
    const directory = await fixture()
    for (const id of [1, 100]) {
      await writeFile(join(directory, "done.json"), JSON.stringify({ tasks: [{ id, title: "Done", status: "done" }] }))
      const before = await text(directory)
      const result = run(directory)
      assert.equal(result.status, 1)
      assert.match(result.stderr, /both tracker|nextTaskId/)
      assert.equal(await text(directory), before)
    }
  })

  it("uses atomic replacement and removes temporary files after an interrupted write", async () => {
    const directory = await fixture()
    const before = await text(directory)
    const done = await text(directory, "done.json")
    const original = io.rename
    try {
      io.rename = async () => { throw new Error("simulated rename failure") }
      await rejectsWith(addTask(directory, fields), "Could not replace")
    } finally {
      io.rename = original
    }
    assert.equal(await text(directory), before)
    assert.equal(await text(directory, "done.json"), done)
    assert.deepEqual((await readdir(directory)).sort(), ["done.json", "index.json"])
    assert.equal((await addTask(directory, fields)).id, 100)
  })

  it("serialises concurrent additions without allocating an id twice", async () => {
    const directory = await fixture()
    const results = await Promise.allSettled([addTask(directory, fields), addTask(directory, fields)])
    assert.deepEqual(results.map((item) => item.status).sort(), ["fulfilled", "rejected"])
    const active = JSON.parse(await text(directory))
    assert.equal(active.nextTaskId, 101)
    assert.deepEqual(active.tasks.map((item) => item.id), [1, 100])
    assert.equal((await addTask(directory, fields)).id, 101)
  })

  it("refuses an existing lock or pending closure without changing tracker bytes", async () => {
    const directory = await fixture()
    const before = await text(directory)
    const lock = join(directory, "index.json.lock")
    await writeFile(lock, "another writer")
    await rejectsWith(addTask(directory, fields), "is locked")
    assert.equal(await readFile(lock, "utf8"), "another writer")
    await rm(lock)
    await writeFile(join(directory, "close-task.pending.json"), "pending")
    await rejectsWith(addTask(directory, fields), "pending closure")
    assert.equal(await text(directory), before)
  })

  it("refuses to exhaust the safe integer id range", async () => {
    const directory = await fixture()
    const before = (await text(directory)).replace('"nextTaskId": 100', `"nextTaskId": ${Number.MAX_SAFE_INTEGER}`)
    await writeFile(join(directory, "index.json"), before)
    await rejectsWith(addTask(directory, fields), "No safe next task id")
    assert.equal(await text(directory), before)
  })
})

describe("task closure", () => {
  // Simulated failures, as the source tests did by mocking node:fs/promises.
  const failure = { target: "", changeActive: false, removeIntent: false }
  const original = { rename: io.rename, unlink: io.unlink }
  beforeEach(() => {
    io.rename = async (from, to) => {
      if (to.endsWith(failure.target) && failure.target)
        throw new Error("simulated write failure")
      await original.rename(from, to)
      if (failure.changeActive && to.endsWith("done.json")) {
        const path = join(to, "..", "index.json")
        await writeFile(
          path,
          (await readFile(path, "utf8")).replace("keep", "changed"),
        )
      }
    }
    io.unlink = async (path) => {
      if (failure.removeIntent && path.endsWith("close-task.pending.json"))
        throw new Error("simulated cleanup failure")
      await original.unlink(path)
    }
  })
  afterEach(() => {
    failure.target = ""
    failure.changeActive = false
    failure.removeIntent = false
    Object.assign(io, original)
  })

  let sha
  const note = "Merged and pushed after independent approval."
  const task = (id, extra = {}) => ({
    id,
    title: `Task ${id}`,
    status: "doing",
    ...extra,
  })
  async function fixture(
    tasks = [task(1), task(2)],
    done = [task(3, { status: "done" })],
  ) {
    const root = await temporaryDirectory("task-close-")
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Task CLI Test")
    git(root, "config", "user.email", "tasks@example.test")
    git(root, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "Synthetic commit")
    sha = git(root, "rev-parse", "HEAD")
    const directory = join(root, "tasks")
    await mkdir(directory)
    await writeFile(
      join(directory, "index.json"),
      JSON.stringify({ _comment: "keep", nextTaskId: 100, tasks }, null, 2) +
        "\n",
    )
    await writeFile(
      join(directory, "done.json"),
      JSON.stringify({ _comment: "done comment", tasks: done }, null, 2) + "\n",
    )
    return directory
  }
  const text = (dir, name) => readFile(join(dir, name), "utf8")
  const data = async (dir, name) => JSON.parse(await text(dir, name))

  for (const kind of ["unknown", "tree", "unreachable"]) {
    it(`refuses a ${kind} SHA without changing either tracker`, async () => {
      const directory = await fixture()
      let rejected = "f".repeat(40)
      if (kind === "tree") rejected = git(directory, "rev-parse", "HEAD^{tree}")
      if (kind === "unreachable") {
        git(directory, "checkout", "-b", "other")
        git(directory, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "Other branch")
        rejected = git(directory, "rev-parse", "HEAD")
        git(directory, "checkout", "main")
      }
      const before = await text(directory, "index.json")
      const completed = await text(directory, "done.json")
      const result = cli(
        ["--tracker-dir", directory, "close", "1", "--sha", rejected, "--note", note],
        tmpdir(),
      )
      assert.equal(result.status, 2)
      assert.equal(result.stdout, "")
      assert.match(result.stderr, kind === "unreachable" ? /git merge-base/ : /git cat-file/)
      assert.equal(result.stderr.trim().split("\n").length, 1)
      assert.equal(await text(directory, "index.json"), before)
      assert.equal(await text(directory, "done.json"), completed)
      assert.deepEqual((await readdir(directory)).sort(), ["done.json", "index.json"])
    })
  }

  it("accepts a commit reachable from the current branch even before merge to main", async () => {
    const directory = await fixture()
    git(directory, "checkout", "-b", "work")
    git(directory, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "Current branch")
    const current = git(directory, "rev-parse", "HEAD")
    await closeTask(directory, 1, current, note)
    assert.ok((await data(directory, "done.json")).tasks.at(-1).notes.includes(current))
  })

  it("accepts an ancestor commit reachable from HEAD", async () => {
    const directory = await fixture()
    git(directory, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "Later commit")
    await closeTask(directory, 1, sha, note)
    assert.ok((await data(directory, "done.json")).tasks.at(-1).notes.includes(sha))
  })

  it("moves once, preserves unrelated bytes and unblocks only resolved dependencies", async () => {
    const directory = await fixture([
      task(1, { notes: "Existing notes", builderTask: true, custom: { retained: true } }),
      task(2, { status: "blocked", blockedBy: [1] }),
      task(4, { status: "blocked", blockedBy: [1, 8] }),
      task(5, { notes: "Unrelated", blockedBy: [] }),
    ])
    const before = await text(directory, "index.json")
    const other = JSON.stringify(
      task(5, { notes: "Unrelated", blockedBy: [] }),
      null,
      2,
    )
      .split("\n")
      .map((line, n) => (n ? `    ${line}` : line))
      .join("\n")
    assert.ok(before.includes(other))
    await closeTask(directory, 1, sha, note)
    const active = await data(directory, "index.json")
    const done = await data(directory, "done.json")
    assert.equal(active.nextTaskId, 100)
    assert.equal(active._comment, "keep")
    assert.equal(done._comment, "done comment")
    assert.deepEqual(
      active.tasks.map((item) => item.id),
      [2, 4, 5],
    )
    assert.equal(active.tasks[0].status, "todo")
    assert.deepEqual(active.tasks[0].blockedBy, [])
    assert.equal(active.tasks[1].status, "blocked")
    assert.deepEqual(active.tasks[1].blockedBy, [8])
    assert.ok((await text(directory, "index.json")).includes(other))
    assert.equal(done.tasks[1].id, 1)
    assert.equal(done.tasks[1].status, "done")
    assert.equal(done.tasks[1].builderTask, true)
    assert.deepEqual(done.tasks[1].custom, { retained: true })
    assert.ok(
      done.tasks[1].notes.includes(
        `Existing notes\n\nClosed at ${sha}: ${note}`,
      ),
    )
    const after = await text(directory, "done.json")
    await rejectsWithExit(closeTask(directory, 1, sha, note), 3)
    assert.equal(await text(directory, "done.json"), after)
    assert.deepEqual((await readdir(directory)).sort(), [
      "done.json",
      "index.json",
    ])
  })

  for (const position of [0, 1, 2]) {
    it(`handles task array position ${position} and empty done arrays`, async () => {
      const directory = await fixture([task(1), task(2), task(3)], [])
      await closeTask(directory, position + 1, sha, note)
      assert.equal((await data(directory, "index.json")).tasks.length, 2)
      assert.equal(
        (await data(directory, "done.json")).tasks[0].id,
        position + 1,
      )
    })
  }

  it("handles a singleton, escaped names, absent notes and repeated dependencies", async () => {
    const directory = await fixture(
      [task(1), task(2, { blockedBy: [1, 1], status: "blocked" })],
      [],
    )
    const before = await text(directory, "index.json")
    await writeFile(
      join(directory, "index.json"),
      before.replace('"status"', '"sta\\u0074us"'),
    )
    await closeTask(directory, 1, sha, note)
    const first = (await data(directory, "index.json")).tasks[0]
    assert.equal(first.status, "todo")
    assert.deepEqual(first.blockedBy, [])
    await closeTask(directory, 2, sha, note)
    assert.deepEqual((await data(directory, "index.json")).tasks, [])
    assert.equal((await data(directory, "done.json")).tasks.length, 2)
  })

  it("refuses findings, invalid ids/SHA/notes, missing and duplicate ids without writes", async () => {
    const directory = await fixture([task(1, { kind: "review-finding" })])
    const before = await text(directory, "index.json")
    const completed = await text(directory, "done.json")
    for (const args of [
      [1, sha, note],
      [99, sha, note],
      [0, sha, note],
      [1, "abc", note],
      [1, sha, " "],
    ])
      await assert.rejects(closeTask(directory, args[0], args[1], args[2]))
    assert.equal(await text(directory, "index.json"), before)
    assert.equal(await text(directory, "done.json"), completed)
    const duplicate = await fixture([task(1)], [task(1, { status: "done" })])
    await rejectsWith(closeTask(duplicate, 1, sha, note), "both tracker")
    await writeFile(join(duplicate, "done.json"), "not json")
    await rejectsWith(closeTask(duplicate, 1, sha, note), "valid JSON")
  })

  for (const target of ["done.json", "index.json"]) {
    it(`recovers an interrupted replacement of ${target} with the original arguments`, async () => {
      const directory = await fixture()
      failure.target = target
      await rejectsWith(closeTask(directory, 1, sha, note), "Could not replace")
      assert.equal(
        (await data(directory, "index.json")).tasks.some((t) => t.id === 1),
        true,
      )
      await rejectsWith(
        setTaskStatus(directory, 2, "todo", "Wait"),
        "pending closure",
      )
      failure.target = ""
      await rejectsWith(
        closeTask(directory, 1, sha, "different note"),
        "original arguments",
      )
      await closeTask(directory, 1, sha, note)
      assert.equal(
        (await data(directory, "done.json")).tasks.filter((t) => t.id === 1)
          .length,
        1,
      )
      assert.equal(
        (await data(directory, "index.json")).tasks.some((t) => t.id === 1),
        false,
      )
      assert.deepEqual((await readdir(directory)).sort(), [
        "done.json",
        "index.json",
      ])
    })
  }

  it("finishes recovery if only intent cleanup was interrupted", async () => {
    const directory = await fixture()
    failure.removeIntent = true
    await assert.rejects(closeTask(directory, 1, sha, note))
    failure.removeIntent = false
    await closeTask(directory, 1, sha, note)
    assert.equal(
      (await data(directory, "done.json")).tasks.filter((t) => t.id === 1)
        .length,
      1,
    )
  })

  it("refuses concurrent changes and leaves recovery evidence intact", async () => {
    const directory = await fixture()
    failure.changeActive = true
    await rejectsWith(closeTask(directory, 1, sha, note), "changed since")
    failure.changeActive = false
    await rejectsWith(
      closeTask(directory, 1, sha, note),
      "changed during interrupted",
    )
    assert.ok((await text(directory, "index.json")).includes("changed"))
    assert.ok((await readdir(directory)).includes("close-task.pending.json"))
  })

  it("serialises closure and status writers without removing an existing lock", async () => {
    const directory = await fixture()
    await writeFile(join(directory, "done.json.lock"), "another writer")
    await rejectsWith(closeTask(directory, 1, sha, note), "is locked")
    assert.ok(!(await readdir(directory)).includes("index.json.lock"))
    assert.equal(await text(directory, "done.json.lock"), "another writer")
  })

  it("offers the close command through the CLI", async () => {
    const directory = await fixture()
    const result = cli(
      ["--tracker-dir", directory, "close", "1", "--sha", sha, "--note", note],
      tmpdir(),
    )
    assert.equal(result.status, 0)
    assert.equal(result.stdout, "Task 1: done\n")
  })
})

describe("Builder task policy", () => {
  const task = (extra = {}) => ({ id: 1, title: "Build trial", status: "todo",
    builderTask: true, notes: "Keep these notes", custom: { keep: true }, ...extra })
  async function fixture(extra = {}) {
    const directory = await temporaryDirectory("task-model-")
    await writeFile(join(directory, "index.json"), JSON.stringify({ _comment: "Keep", nextTaskId: 10, tasks: [task(extra)] }, null, 2) + "\n")
    await writeFile(join(directory, "done.json"), '{"tasks":[]}\n')
    return directory
  }
  const source = (directory) => readFile(join(directory, "index.json"), "utf8")
  const run = (directory, ...args) => cli(["--tracker-dir", directory, ...args], tmpdir())

  for (const [label, runner] of [
    ["missing runner", undefined],
    ["non-Claude model", { tool: "cursor", model: "composer", externalModelBudget: "available" }],
    ["wrong model family", { tool: "cursor", model: "gpt-6.2", externalModelBudget: "available" }],
    ["auto model", { tool: "cursor", model: "auto", externalModelBudget: "available" }],
    ["unknown host", { tool: "codex", model: "sonnet" }],
    ["missing Cursor budget", { tool: "cursor", model: "sonnet" }],
    ["unknown Cursor budget", { tool: "cursor", model: "opus", externalModelBudget: "unknown" }],
    ["exhausted Cursor budget", { tool: "cursor", model: "sonnet", externalModelBudget: "unavailable" }],
  ]) {
    it(`refuses ${label} before claiming or resuming, without writes`, async () => {
      const directory = await fixture()
      const before = await source(directory)
      await rejectsWithExit(checkTaskRun(directory, 1, runner), 2)
      await rejectsWithExit(setTaskStatus(directory, 1, "doing", undefined, runner, claim), 2)
      assert.equal(await source(directory), before)
      await writeFile(join(directory, "index.json"), before.replace('"status": "todo"', '"status": "doing"'))
      const doing = await source(directory)
      await rejectsWithExit(setTaskStatus(directory, 1, "doing", undefined, runner, claim), 2)
      assert.equal(await source(directory), doing)
    })
  }

  it("accepts Claude Code and funded Cursor with named Claude models", async () => {
    for (const [tool, model, externalModelBudget] of [
      ["claude-code", "sonnet", undefined],
      ["claude-code", "claude-opus-5-5-low", undefined],
      ["cursor", "sonnet-4", "available"],
      ["cursor", "sonnet-4-thinking", "available"],
      ["cursor", "opus", "available"],
      ["cursor", "claude-sonnet-5-5-medium", "available"],
      ["cursor", "anthropic/claude-opus-4-6", "available"],
    ]) {
      const directory = await fixture()
      const before = await source(directory)
      const options = ["--tool", tool, "--model", model,
        ...(externalModelBudget ? ["--external-model-budget", externalModelBudget] : [])]
      const checked = run(directory, "check-run", "1", ...options)
      assert.equal(checked.status, 0, checked.stderr)
      assert.equal(await source(directory), before)
      const started = run(directory, "status", "1", "doing", ...claimArgs, ...options)
      assert.equal(started.status, 0, started.stderr)
      assert.equal((await showTask(directory, 1)).builderTask, true)
      await setTaskStatus(directory, 1, "todo", "Resume later")
      assert.equal((await showTask(directory, 1)).builderTask, true)
    }
  })

  it("shows the requirement in the ready list and leaves tool-only tasks runnable", async () => {
    const directory = await fixture()
    assert.match(run(directory, "list", "--ready").stdout, /builder task/)
    await writeFile(join(directory, "index.json"), await source(directory).then((s) => s.replace('"builderTask": true', '"builderTask": false')))
    assert.equal(run(directory, "check-run", "1").status, 0)
    assert.equal(run(directory, "status", "1", "doing", ...claimArgs).status, 0)
  })

  it("keeps blocked and completed tasks out of dispatch even with a valid runner", async () => {
    const directory = await fixture({ blockedBy: [2] })
    const runner = { tool: "claude-code", model: "sonnet" }
    await rejectsWith(checkTaskRun(directory, 1, runner), "not ready")
    await writeFile(join(directory, "index.json"), '{"nextTaskId":10,"tasks":[]}')
    await writeFile(join(directory, "done.json"), JSON.stringify({ tasks: [task({ status: "done" })] }))
    await rejectsWith(checkTaskRun(directory, 1, runner), "not ready")
  })

  it("requires explicit classification on creation and scope updates", async () => {
    const directory = await fixture()
    const before = await source(directory)
    const add = ["add", "--title", "Trial", "--priority", "low", "--complexity", "s", "--notes", "Scope"]
    for (const args of [add, [...add, "--builder-task", "yes"],
      ["update", "1", "--notes", "New scope"],
      ["update", "1", "--builder-task", "null"]]) {
      assert.equal(run(directory, ...args).status, 2)
      assert.equal(await source(directory), before)
    }
    const added = run(directory, ...add, "--builder-task", "true")
    assert.equal(added.status, 0, added.stderr)
    assert.equal((await showTask(directory, 10)).builderTask, true)
  })

  it("rejects the renamed --requires-claude flag with a pointer to --builder-task", async () => {
    const directory = await fixture()
    const before = await source(directory)
    for (const args of [["update", "1", "--requires-claude", "true"],
      ["add", "--title", "T", "--priority", "low", "--complexity", "s", "--notes", "N", "--requires-claude", "true"]]) {
      const result = run(directory, ...args)
      assert.equal(result.status, 2)
      assert.match(result.stderr, /renamed to '--builder-task'/)
    }
    assert.equal(await source(directory), before)
  })

  it("updates scope without changing other fields and records intentional downgrades", async () => {
    const directory = await fixture()
    const before = await source(directory)
    const updated = run(directory, "update", "1", "--builder-task", "true", "--notes", "New scope")
    assert.equal(updated.status, 0, updated.stderr)
    assert.equal(await source(directory), before.replace('"Keep these notes"', '"New scope"'))
    const scoped = await source(directory)
    assert.equal(run(directory, "update", "1", "--builder-task", "false").status, 2)
    assert.equal(await source(directory), scoped)
    const result = await updateTask(directory, 1, { builderTask: false, reason: "Build trial moved to a separate task" })
    assert.deepEqual(result.custom, { keep: true })
    assert.match(result.notes, /builderTask cleared: Build trial moved/)
    assert.equal((await showTask(directory, 1)).builderTask, false)
  })

  it("persists a flag on a legacy task and reads it back through the CLI", async () => {
    const directory = await fixture({ builderTask: undefined })
    const before = JSON.parse(await source(directory))
    const result = run(directory, "update", "1", "--builder-task", "true")
    assert.equal(result.status, 0, result.stderr)
    const expected = { ...before.tasks[0], builderTask: true }
    const shown = run(directory, "show", "1")
    assert.equal(shown.status, 0, shown.stderr)
    assert.deepEqual(JSON.parse(shown.stdout), expected)
    assert.deepEqual(JSON.parse(await source(directory)), { ...before, tasks: [expected] })
  })

  for (const order of [
    ["notes", "title", "builderTask"],
    ["builderTask", "notes", "title"],
    ["title", "builderTask", "notes"],
  ]) {
    it(`updates flag, title and notes together with source order ${order.join(", ")}`, async () => {
      const directory = await fixture()
      const original = task({ builderTask: false })
      const reordered = Object.fromEntries(
        ["id", ...order, "status", "custom"].map((key) => [key, original[key]]),
      )
      const tracker = { _comment: "Keep", nextTaskId: 10, tasks: [reordered,
        task({ id: 2, title: "Unchanged neighbour", builderTask: false })] }
      const before = JSON.stringify(tracker, null, 2) + "\n"
      await writeFile(join(directory, "index.json"), before)
      const title = 'Renamed "trial" with a longer title'
      const notes = "Short\nnotes"
      const result = run(directory, "update", "1", "--builder-task", "true",
        "--title", title, "--notes", notes)
      assert.equal(result.status, 0, result.stderr)
      const shown = run(directory, "show", "1")
      assert.equal(shown.status, 0, shown.stderr)
      assert.deepEqual(JSON.parse(shown.stdout), { ...reordered, builderTask: true, title, notes })
      assert.equal(await source(directory), before
        .replace('"builderTask": false', '"builderTask": true')
        .replace(JSON.stringify(original.title), JSON.stringify(title))
        .replace(JSON.stringify(original.notes), JSON.stringify(notes)))
    })
  }

  it("rejects nonboolean stored flags", async () => {
    const directory = await fixture()
    for (const builderTask of ["true", null, 1, {}, []]) {
      await writeFile(join(directory, "index.json"), JSON.stringify({ nextTaskId: 10, tasks: [task({ builderTask })] }))
      await rejectsWith(showTask(directory, 1), "invalid tracker schema")
    }
  })
})

// Tests below cover the hand-written argument parser and default directory,
// which replace the source's commander and repository-relative default.
describe("command-line arguments", () => {
  const tracker = async () => {
    const directory = await temporaryDirectory("task-args-")
    await writeFile(
      join(directory, "index.json"),
      JSON.stringify({
        nextTaskId: 10,
        tasks: [
          { id: 1, title: "One", status: "todo", tags: ["ready-for-agent"] },
        ],
      }),
    )
    await writeFile(join(directory, "done.json"), '{"tasks":[]}')
    return directory
  }

  for (const [args, message] of [
    [["list"], "required option '--ready' not specified"],
    [["list", "--ready=yes"], "option '--ready' does not take a value"],
    [["show"], "missing required argument 'id'"],
    [["show", "1", "2"], "too many arguments for 'show'"],
    [["add"], "required option '--title' not specified"],
    [["add", "--title", "New"], "required option '--priority' not specified"],
    [["add", "--title", "New", "--priority", "low"], "required option '--complexity' not specified"],
    [["status", "1"], "missing required argument 'status'"],
    [["status", "1", "doing", "--bogus"], "unknown option '--bogus'"],
    [["status", "1", "todo", "--reason"], "option '--reason' argument missing"],
    [["close", "1"], "required option '--sha' not specified"],
    [["close", "1", "--sha", "a"], "required option '--note' not specified"],
    [["unknown"], "unknown command 'unknown'"],
    [["--tracker-dir"], "option '--tracker-dir' argument missing"],
  ]) {
    it(`reports a usage error for ${args.join(" ")}`, async () => {
      const directory = await tracker()
      const before = await readFile(join(directory, "index.json"), "utf8")
      const result = cli(
        args[0] === "--tracker-dir" ? args : ["--tracker-dir", directory, ...args],
      )
      assert.equal(result.status, 2)
      assert.ok(result.stderr.startsWith(`error: ${message}`), result.stderr)
      assert.equal(result.stdout, "")
      assert.equal(
        await readFile(join(directory, "index.json"), "utf8"),
        before,
      )
    })
  }

  it("prints usage to stderr with exit 2 when no command is given", () => {
    const result = cli([])
    assert.equal(result.status, 2)
    assert.ok(result.stderr.startsWith("Usage: tasks"))
  })

  it("prints help for the program and each command", () => {
    for (const args of [["-h"], ["help"], ["help", "close"], ["help", "add"], ["show", "--help"]]) {
      const result = cli(args)
      assert.equal(result.status, 0)
      assert.ok(result.stdout.startsWith("Usage: tasks"))
    }
  })

  it("accepts --tracker-dir and option values in either position and form", async () => {
    const directory = await tracker()
    const result = cli(["list", "--ready", `--tracker-dir=${directory}`])
    assert.equal(result.stdout, "1\t-\tOne\n")
    const started = cli(["status", "1", "doing", ...claimArgs, "--tracker-dir", directory])
    assert.equal(started.stdout, "Task 1: doing\n")
    const returned = cli([
      "--tracker-dir",
      directory,
      "status",
      "1",
      "todo",
      "--reason=-Waiting",
    ])
    assert.equal(returned.status, 0)
    assert.ok(
      (await showTask(directory, 1)).notes.includes(
        "Returned to todo: -Waiting",
      ),
    )
  })

  it("defaults to tasks/ under the git root, else ./tasks", async () => {
    const root = await temporaryDirectory("task-root-")
    const nested = join(root, "a", "b")
    await mkdir(nested, { recursive: true })
    // The system temporary directory is not normally inside a git work tree.
    assert.equal(defaultTrackerDir(nested), join(nested, "tasks"))
    await writeFile(join(root, ".git"), "gitdir: elsewhere\n")
    assert.equal(defaultTrackerDir(nested), join(root, "tasks"))
  })
})

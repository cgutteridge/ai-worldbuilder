import { mkdir, mkdtemp, readFile, writeFile, rm, readdir } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { fileURLToPath } from "node:url"
import { spawnSync } from "node:child_process"
import { afterEach, beforeEach, describe, it } from "node:test"
import assert from "node:assert/strict"
import {
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
      await rejectsWith(setTaskStatus(directory, 1, "doing"), "is locked")
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
          setTaskStatus(directory, 1, "doing"),
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
      await setTaskStatus(directory, 1, "doing")
      assert.equal(
        await contents(directory),
        source.replace('"status" : "todo"', '"status" : "doing"'),
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
        '{"nextTaskId":100,"tasks":[{"id":1,"title":"A","sta\\u0074us":"doing"  }]}\n'
      await writeFile(join(directory, "index.json"), source)
      const before = await readTrackerFile(directory, "index.json")
      await setTaskStatus(directory, 1, "doing")
      assert.equal(
        (await readTrackerFile(directory, "index.json")).identity,
        before.identity,
      )
      await setTaskStatus(directory, 1, "todo", "Reprioritised.")
      assert.ok((await showTask(directory, 1)).notes.includes("Reprioritised."))
      assert.ok(
        (await contents(directory)).includes(
          '"sta\\u0074us":"todo"  , "notes":',
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
      await rejectsWith(setTaskStatus(directory, 1, "doing"), "reviewer workflow")
      await rejectsWith(setTaskStatus(directory, 1, "todo"), "reviewer workflow")
      await rejectsWith(
        setTaskStatus(directory, 2, "doing"),
        "unresolved blockers",
      )
      await rejectsWith(
        setTaskStatus(directory, 3, "todo", "Ready"),
        "Only todo and doing",
      )
      await rejectsWithExit(setTaskStatus(directory, 4, "doing"), 3)
      await rejectsWithExit(setTaskStatus(directory, 0, "doing"), 2)
      assert.equal(await contents(directory), before)
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
      task(1, { notes: "Existing notes", custom: { retained: true } }),
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
    for (const args of [["-h"], ["help"], ["help", "close"], ["show", "--help"]]) {
      const result = cli(args)
      assert.equal(result.status, 0)
      assert.ok(result.stdout.startsWith("Usage: tasks"))
    }
  })

  it("accepts --tracker-dir and option values in either position and form", async () => {
    const directory = await tracker()
    const result = cli(["list", "--ready", `--tracker-dir=${directory}`])
    assert.equal(result.stdout, "1\t-\tOne\n")
    const started = cli(["status", "1", "doing", "--tracker-dir", directory])
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

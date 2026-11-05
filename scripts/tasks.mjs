#!/usr/bin/env node
// Installed from agent-kits local-tasks@0.2.0. Change it in the kit, not here.
// Reads a local task tracker (tasks/index.json and tasks/done.json) and
// updates implementation status without disturbing unrelated tracker bytes.
//
// This file has no dependencies: copy it into any repository that keeps its
// work in a local task tracker. Requires Node 20 or later.
//
// Usage: node tasks.mjs [--tracker-dir DIR] <command>
//   list --ready
//   show <id>
//   status <id> <todo|doing> [--reason TEXT]
//   close <id> --sha FULL_COMMIT_SHA --note TEXT
// Default tracker directory: tasks/ under the git root of the current
// directory, or ./tasks when there is no git root.
// Exit codes: 0 success, 1 invalid files or failed writes, 2 invalid arguments
// or refused lifecycle changes, 3 missing task.

import { createHash, randomUUID } from "node:crypto"
import { existsSync, realpathSync } from "node:fs"
import * as fs from "node:fs/promises"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

export class TaskError extends Error {
  constructor(message, exitCode = 1) {
    super(message)
    this.exitCode = exitCode
  }
}

// File operations go through this object so tests can simulate interrupted
// writes. Code outside tests should leave it alone.
export const io = {
  open: fs.open,
  readFile: fs.readFile,
  rename: fs.rename,
  stat: fs.stat,
  unlink: fs.unlink,
}

const statuses = ["todo", "doing", "blocked", "done"]
const priorities = ["critical", "high", "medium", "low"]
const complexities = ["xs", "s", "m", "l", "xl"]
const kinds = ["task", "review-finding"]
const shaPattern = /^[0-9a-f]{40}$/
const pendingName = "close-task.pending.json"

const isId = (value) =>
  typeof value === "number" && Number.isSafeInteger(value) && value > 0
const isPlainObject = (value) =>
  typeof value === "object" && value !== null && !Array.isArray(value)
const optional = (value, check) => value === undefined || check(value)
const oneOf = (values) => (value) => values.includes(value)
const arrayOf = (check) => (value) => Array.isArray(value) && value.every(check)
const isString = (value) => typeof value === "string"

// Known fields are checked; any other fields are allowed and kept as they are.
function isValidTask(task) {
  return (
    isPlainObject(task) &&
    isId(task.id) &&
    typeof task.title === "string" &&
    task.title.length > 0 &&
    statuses.includes(task.status) &&
    optional(task.priority, oneOf(priorities)) &&
    optional(task.complexity, oneOf(complexities)) &&
    optional(task.blockedBy, arrayOf(isId)) &&
    optional(task.tags, arrayOf(isString)) &&
    optional(task.kind, oneOf(kinds)) &&
    optional(task.notes, isString)
  )
}

/** JSON.parse checks grammar; this second pass records exact editable ranges.
 * Reject repeated object keys rather than silently editing an ambiguous field.
 * A span is { start, end, properties?: Map<string, span>, items?: span[] }.
 */
function locate(text) {
  const tokens = [
    ...text.matchAll(
      /"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null|[{}\[\]:,]/g,
    ),
  ]
  let cursor = 0
  function value() {
    const token = tokens[cursor++]
    const span = { start: token.index, end: token.index + token[0].length }
    if (token[0] === "{") {
      span.properties = new Map()
      while (tokens[cursor][0] !== "}") {
        const key = JSON.parse(tokens[cursor++][0])
        if (span.properties.has(key))
          throw new TaskError("Tracker contains a repeated object key.")
        cursor++ // colon
        span.properties.set(key, value())
        if (tokens[cursor][0] !== ",") break
        cursor++
      }
      span.end = tokens[cursor++].index + 1
    } else if (token[0] === "[") {
      span.items = []
      while (tokens[cursor][0] !== "]") {
        span.items.push(value())
        if (tokens[cursor][0] !== ",") break
        cursor++
      }
      span.end = tokens[cursor++].index + 1
    }
    return span
  }
  return value()
}

export function parseTaskId(value) {
  if (!/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(Number(value))) {
    throw new TaskError("Task id must be a positive whole number.", 2)
  }
  return Number(value)
}

function identity(info) {
  return `${info.dev}:${info.ino}:${info.size}:${info.mtimeMs}:${info.ctimeMs}`
}

function parseTracker(text, name) {
  let data
  try {
    data = JSON.parse(text)
  } catch {
    throw new TaskError(`${name} is not valid JSON.`)
  }
  const root = locate(text)
  if (
    !isPlainObject(data) ||
    !Array.isArray(data.tasks) ||
    !data.tasks.every(isValidTask) ||
    (name === "index.json" && !isId(data.nextTaskId))
  )
    throw new TaskError(`${name} has an invalid tracker schema.`)
  const ids = new Set()
  for (const task of data.tasks) {
    if (ids.has(task.id))
      throw new TaskError(`${name} contains duplicate task id ${task.id}.`)
    ids.add(task.id)
    if ((name === "done.json") !== (task.status === "done")) {
      throw new TaskError(
        `${name} contains a task with an invalid lifecycle status.`,
      )
    }
    if (name === "index.json" && task.id >= data.nextTaskId) {
      throw new TaskError(
        "nextTaskId must be greater than every active task id.",
      )
    }
  }
  // Return the original objects so unfamiliar keys survive `show` unchanged.
  return { tasks: data.tasks, root }
}

/** Read a named tracker file; never follow doneRef or discover other trackers.
 * Resolves to { path, name, bytes, identity, text, root, tasks }.
 */
export async function readTrackerFile(directory, name) {
  const path = join(directory, name)
  try {
    const before = await io.stat(path)
    const bytes = await io.readFile(path)
    const after = await io.stat(path)
    if (identity(before) !== identity(after))
      throw new TaskError(`${name} changed while reading; retry.`)
    let text
    try {
      text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(
        bytes,
      )
    } catch {
      throw new TaskError(`${name} is not valid UTF-8.`)
    }
    return {
      path,
      name,
      bytes,
      identity: identity(after),
      text,
      ...parseTracker(text, name),
    }
  } catch (error) {
    if (error instanceof TaskError) throw error
    throw new TaskError(`Could not read ${name}.`)
  }
}

/** Apply disjoint value edits without changing surrounding bytes. */
export function editSpans(text, edits) {
  let boundary = text.length
  for (const edit of [...edits].sort((a, b) => b.span.start - a.span.start)) {
    if (
      edit.span.start < 0 ||
      edit.span.end > boundary ||
      edit.span.start > edit.span.end
    ) {
      throw new TaskError("Invalid or overlapping tracker edits.")
    }
    text = text.slice(0, edit.span.start) + edit.text + text.slice(edit.span.end)
    boundary = edit.span.start
  }
  return text
}

async function withLocks(paths, run) {
  const held = []
  try {
    for (const path of paths) {
      try {
        const lock = await io.open(`${path}.lock`, "wx", 0o600)
        held.push(`${path}.lock`)
        await lock.close()
      } catch {
        throw new TaskError(
          "Tracker is locked; check for another task writer before retrying.",
        )
      }
    }
    return await run()
  } finally {
    for (const path of held.reverse()) await io.unlink(path)
  }
}

/** Validate the replacement and refuse stale snapshots before an atomic
 * rename. Single-file operation; closeTask owns two-file recovery.
 */
async function replaceLocked(snapshot, text) {
  parseTracker(text, snapshot.name)
  const temporary = `${snapshot.path}.${randomUUID()}.tmp`
  let created = false
  try {
    const info = await io.stat(snapshot.path)
    const file = await io.open(temporary, "wx", info.mode & 0o777)
    created = true
    try {
      await file.writeFile(text, "utf8")
      await file.sync()
    } finally {
      await file.close()
    }
    const current = await io.readFile(snapshot.path)
    const latest = await io.stat(snapshot.path)
    if (
      identity(latest) !== snapshot.identity ||
      !current.equals(snapshot.bytes)
    )
      throw new TaskError(`${snapshot.name} changed since it was read; retry.`)
    await io.rename(temporary, snapshot.path)
  } catch (error) {
    if (error instanceof TaskError) throw error
    throw new TaskError(
      `Could not replace ${snapshot.name}; no update completed.`,
    )
  } finally {
    if (created)
      await io.unlink(temporary).catch((error) => {
        if (error.code !== "ENOENT")
          throw new TaskError("Could not remove temporary tracker file.")
      })
  }
}

export async function replaceTrackerFile(snapshot, text) {
  parseTracker(text, snapshot.name)
  await withLocks([snapshot.path], async () => {
    try {
      await io.stat(join(snapshot.path, "..", pendingName))
      throw new TaskError(
        "Finish the pending closure before changing task status.",
      )
    } catch (error) {
      if (error.code !== "ENOENT") throw error
    }
    await replaceLocked(snapshot, text)
  })
}

function unresolvedBlockers(task) {
  // Closure removes resolved references. Absence from index alone cannot prove
  // completion: an id might be missing or never have been allocated.
  return task.blockedBy ?? []
}

export async function listReadyTasks(directory) {
  const { tasks } = await readTrackerFile(directory, "index.json")
  // A tracker that uses triage tags lists only tasks tagged ready-for-agent.
  // A tracker with no tags on any active task does not use triage, so tags are
  // not required there.
  const usesTags = tasks.some((task) => Array.isArray(task.tags))
  return tasks.filter(
    (task) =>
      task.status === "todo" &&
      (!usesTags || task.tags?.includes("ready-for-agent")) &&
      unresolvedBlockers(task).length === 0,
  )
}

export async function showTask(directory, id) {
  if (!isId(id))
    throw new TaskError("Task id must be a positive whole number.", 2)
  const active = await readTrackerFile(directory, "index.json")
  const task = active.tasks.find((item) => item.id === id)
  if (task) return task
  const completed = await readTrackerFile(directory, "done.json")
  const done = completed.tasks.find((item) => item.id === id)
  if (!done) throw new TaskError(`Task ${id} was not found.`, 3)
  return done
}

export async function setTaskStatus(directory, id, status, reason) {
  if (!isId(id) || !["todo", "doing"].includes(status))
    throw new TaskError("Use a valid task id and status todo or doing.", 2)
  const snapshot = await readTrackerFile(directory, "index.json")
  const index = snapshot.tasks.findIndex((task) => task.id === id)
  const task = snapshot.tasks[index]
  if (!task) throw new TaskError(`Active task ${id} was not found.`, 3)
  if (task.kind === "review-finding")
    throw new TaskError(
      "Review findings must use the independent reviewer workflow.",
      2,
    )
  if (task.status !== "todo" && task.status !== "doing")
    throw new TaskError(
      "Only todo and doing implementation tasks can change status here.",
      2,
    )
  if (status === "doing" && unresolvedBlockers(task).length)
    throw new TaskError("Task has unresolved blockers.", 2)
  if (task.status === status) return task
  const trimmed = reason?.trim()
  if (status === "todo" && (!trimmed || trimmed.length > 300))
    throw new TaskError(
      "Returning to todo requires a reason of 1–300 characters.",
      2,
    )
  const taskSpan = snapshot.root.properties.get("tasks").items[index]
  const fields = taskSpan.properties
  const edits = [{ span: fields.get("status"), text: JSON.stringify(status) }]
  let notes = task.notes
  if (status === "todo") {
    notes = `${notes ? `${notes}\n\n` : ""}Returned to todo: ${trimmed}\n\n[tasks CLI] Status update recorded; subject to human review.`
    const notesSpan = fields.get("notes")
    if (notesSpan) edits.push({ span: notesSpan, text: JSON.stringify(notes) })
    else
      edits.push({
        span: { start: taskSpan.end - 1, end: taskSpan.end - 1 },
        text: `, "notes": ${JSON.stringify(notes)}`,
      })
  }
  await replaceTrackerFile(snapshot, editSpans(snapshot.text, edits))
  return { ...task, status, ...(notes === undefined ? {} : { notes }) }
}

const digest = (text) => createHash("sha256").update(text).digest("hex")

const intentKeys = [
  "version",
  "id",
  "sha",
  "note",
  "indexBefore",
  "indexAfter",
  "doneBefore",
  "doneAfter",
]

/** Return the recovery record with a trimmed note, or throw if it is invalid. */
function parseIntent(data) {
  const valid =
    isPlainObject(data) &&
    Object.keys(data).length === intentKeys.length &&
    intentKeys.every((key) => Object.hasOwn(data, key)) &&
    data.version === 1 &&
    isId(data.id) &&
    typeof data.sha === "string" &&
    shaPattern.test(data.sha) &&
    typeof data.note === "string" &&
    data.note.trim().length >= 1 &&
    data.note.trim().length <= 4000 &&
    intentKeys.slice(4).every((key) => typeof data[key] === "string")
  if (!valid) throw new Error("invalid closure record")
  return { ...data, note: data.note.trim() }
}

function removeItem(text, array, index) {
  const items = array.items
  const item = items[index]
  if (items.length === 1) return { span: item, text: "" }
  if (index < items.length - 1) {
    const comma = text.indexOf(",", item.end)
    return { span: { start: item.start, end: comma + 1 }, text: "" }
  }
  const comma = text.indexOf(",", items[index - 1].end)
  return { span: { start: comma, end: item.end }, text: "" }
}

/** Close ordinary tasks only. A hash-only intent file permits deterministic
 * recovery; the two tracker files remain the only source of task records.
 */
export async function closeTask(directory, id, sha, note) {
  if (
    !isId(id) ||
    typeof sha !== "string" ||
    !shaPattern.test(sha) ||
    typeof note !== "string" ||
    !note.trim() ||
    note.trim().length > 4000
  )
    throw new TaskError(
      "Closure requires a task id, full lowercase commit SHA and a note of 1–4000 characters.",
      2,
    )
  note = note.trim()
  await withLocks(
    [join(directory, "index.json"), join(directory, "done.json")],
    async () => {
      const active = await readTrackerFile(directory, "index.json")
      const completed = await readTrackerFile(directory, "done.json")
      const intentPath = join(directory, pendingName)
      let intent
      try {
        intent = parseIntent(
          JSON.parse(await io.readFile(intentPath, "utf8")),
        )
      } catch (error) {
        if (error.code !== "ENOENT")
          throw new TaskError(
            "Closure recovery record is damaged; preserve both trackers and ask the owner.",
          )
      }
      if (
        intent &&
        (intent.id !== id || intent.sha !== sha || intent.note !== note)
      )
        throw new TaskError(
          "Finish the pending closure with its original arguments before another close.",
          2,
        )
      if (
        intent &&
        digest(active.text) === intent.indexAfter &&
        digest(completed.text) === intent.doneAfter
      ) {
        await io.unlink(intentPath)
        return
      }
      if (
        intent &&
        (digest(active.text) !== intent.indexBefore ||
          ![intent.doneBefore, intent.doneAfter].includes(
            digest(completed.text),
          ))
      )
        throw new TaskError(
          "Trackers changed during interrupted closure; preserve the recovery record and ask the owner.",
        )
      const index = active.tasks.findIndex((task) => task.id === id)
      const task = active.tasks[index]
      if (!task)
        throw new TaskError(
          `Active task ${id} was not found; repeat close is refused.`,
          3,
        )
      if (task.kind === "review-finding")
        throw new TaskError(
          "Review findings require independent reviewer closure.",
          2,
        )
      const doneIds = new Set(completed.tasks.map((item) => item.id))
      for (const item of completed.tasks) {
        if (item.id >= Number(JSON.parse(active.text).nextTaskId))
          throw new TaskError("nextTaskId must exceed completed task ids.")
      }
      for (const item of active.tasks) {
        if (
          doneIds.has(item.id) &&
          !(
            intent &&
            item.id === id &&
            digest(completed.text) === intent.doneAfter
          )
        )
          throw new TaskError("Task id occurs in both tracker files.")
      }
      const array = active.root.properties.get("tasks")
      const taskSpan = array.items[index]
      const fields = taskSpan.properties
      const closureNote = `${task.notes ? `${task.notes}\n\n` : ""}Closed at ${sha}: ${note}\n\n[tasks CLI] Delivery evidence recorded; subject to human review.`
      const taskEdits = [{ span: fields.get("status"), text: '"done"' }]
      if (fields.has("notes"))
        taskEdits.push({
          span: fields.get("notes"),
          text: JSON.stringify(closureNote),
        })
      else
        taskEdits.push({
          span: { start: taskSpan.end - 1, end: taskSpan.end - 1 },
          text: `, "notes": ${JSON.stringify(closureNote)}`,
        })
      const closedText = editSpans(active.text, taskEdits).slice(
        taskSpan.start,
        taskSpan.end +
          taskEdits.reduce(
            (sum, edit) =>
              sum + edit.text.length - (edit.span.end - edit.span.start),
            0,
          ),
      )
      const edits = [removeItem(active.text, array, index)]
      active.tasks.forEach((dependent, position) => {
        if (position === index || !dependent.blockedBy?.includes(id)) return
        const properties = array.items[position].properties
        const blockers = properties.get("blockedBy")
        // Remove all matching ids while retaining the original spelling of other ids.
        let blockedText = active.text.slice(blockers.start, blockers.end)
        for (let n = dependent.blockedBy.length - 1; n >= 0; n--) {
          if (dependent.blockedBy[n] !== id) continue
          const local = locate(blockedText)
          blockedText = editSpans(blockedText, [
            removeItem(blockedText, local, n),
          ])
        }
        edits.push({ span: blockers, text: blockedText })
        if (
          dependent.status === "blocked" &&
          dependent.blockedBy.every((blocker) => blocker === id)
        )
          edits.push({ span: properties.get("status"), text: '"todo"' })
      })
      const indexText = editSpans(active.text, edits)
      const doneArray = completed.root.properties.get("tasks")
      const doneText =
        intent && digest(completed.text) === intent.doneAfter
          ? completed.text
          : editSpans(completed.text, [
              {
                span: { start: doneArray.end - 1, end: doneArray.end - 1 },
                text: `${completed.tasks.length ? "," : ""}\n    ${closedText}\n  `,
              },
            ])
      parseTracker(indexText, "index.json")
      parseTracker(doneText, "done.json")
      const proposed = {
        version: 1,
        id,
        sha,
        note,
        indexBefore: digest(active.text),
        indexAfter: digest(indexText),
        doneBefore: digest(completed.text),
        doneAfter: digest(doneText),
      }
      if (intent) {
        if (
          proposed.indexAfter !== intent.indexAfter ||
          proposed.doneAfter !== intent.doneAfter
        )
          throw new TaskError(
            "Recovery does not match the saved closure intent.",
          )
      } else {
        const file = await io.open(intentPath, "wx", 0o600)
        try {
          await file.writeFile(JSON.stringify(proposed) + "\n")
          await file.sync()
        } finally {
          await file.close()
        }
      }
      // Append first: interruption never loses a task. Recovery removes its active copy.
      if (doneText !== completed.text) await replaceLocked(completed, doneText)
      // Recheck done before removing the active record, including recovery runs.
      if (
        digest((await readTrackerFile(directory, "done.json")).text) !==
        proposed.doneAfter
      )
        throw new TaskError(
          "done.json changed during closure; preserve the recovery record.",
        )
      await replaceLocked(active, indexText)
      await io.unlink(intentPath)
    },
  )
}

/** tasks/ under the git root containing `start`, or start/tasks without one. */
export function defaultTrackerDir(start = process.cwd()) {
  let directory = resolve(start)
  for (;;) {
    if (existsSync(join(directory, ".git"))) return join(directory, "tasks")
    const parent = dirname(directory)
    if (parent === directory) return resolve(start, "tasks")
    directory = parent
  }
}

// Command-line interface.

class UsageError extends Error {}

const commands = {
  list: {
    usage: "list --ready",
    summary: "List ready todo tasks: id, priority and title.",
    positional: [],
    flags: ["--ready"],
    values: [],
    required: ["--ready"],
  },
  show: {
    usage: "show <id>",
    summary: "Print one complete active or completed task as JSON.",
    positional: ["id"],
    flags: [],
    values: [],
    required: [],
  },
  status: {
    usage: "status <id> <todo|doing> [--reason TEXT]",
    summary:
      "Start a task (doing) or return it to todo with a reason of 1–300 characters.",
    positional: ["id", "status"],
    flags: [],
    values: ["--reason"],
    required: [],
  },
  close: {
    usage: "close <id> --sha FULL_COMMIT_SHA --note TEXT",
    summary:
      "Move a task to done.json with delivery evidence (note of 1–4000 characters).",
    positional: ["id"],
    flags: [],
    values: ["--sha", "--note"],
    required: ["--sha", "--note"],
  },
}

export function usage(name) {
  if (name)
    return `Usage: tasks [--tracker-dir DIR] ${commands[name].usage}\n\n${commands[name].summary}\n`
  const lines = Object.values(commands).map(
    (command) => `  ${command.usage}\n      ${command.summary}`,
  )
  return [
    "Usage: tasks [--tracker-dir DIR] <command>",
    "",
    "Read local tasks and update implementation status.",
    "",
    "Commands:",
    ...lines,
    "",
    "Options:",
    "  --tracker-dir DIR  tracker directory holding index.json and done.json",
    "                     (default: tasks/ under the git root, else ./tasks)",
    "  -h, --help         show help",
    "",
    "Exit codes: 0 success, 1 invalid files or failed writes,",
    "2 invalid arguments or refused lifecycle changes, 3 missing task.",
    "",
  ].join("\n")
}

/** Parse argv (without node and script) into { help } or a command request. */
export function parseArguments(argv) {
  let trackerDir
  let name
  let help = false
  const positional = []
  const options = {}
  const args = [...argv]
  let optionsEnded = false
  while (args.length) {
    const arg = args.shift()
    if (optionsEnded || !arg.startsWith("-") || arg === "-") {
      if (name === undefined) {
        if (arg === "help") {
          help = true
          name = args.length ? args.shift() : null
          if (name && !commands[name])
            throw new UsageError(`unknown command '${name}'`)
          if (args.length) throw new UsageError("too many arguments for 'help'")
          break
        }
        if (!commands[arg]) throw new UsageError(`unknown command '${arg}'`)
        name = arg
      } else positional.push(arg)
      continue
    }
    if (arg === "--") {
      optionsEnded = true
      continue
    }
    if (arg === "-h" || arg === "--help") {
      help = true
      continue
    }
    const equals = arg.indexOf("=")
    const flag = equals === -1 ? arg : arg.slice(0, equals)
    const inline = equals === -1 ? undefined : arg.slice(equals + 1)
    const command = name ? commands[name] : undefined
    if (flag === "--tracker-dir" || command?.values.includes(flag)) {
      const value = inline ?? args.shift()
      if (value === undefined)
        throw new UsageError(`option '${flag}' argument missing`)
      if (flag === "--tracker-dir") trackerDir = value
      else options[flag] = value
    } else if (command?.flags.includes(flag) && inline === undefined) {
      options[flag] = true
    } else if (command?.flags.includes(flag)) {
      throw new UsageError(`option '${flag}' does not take a value`)
    } else throw new UsageError(`unknown option '${arg}'`)
  }
  if (help) return { help: true, command: name ?? null }
  if (!name) return { help: true, command: null, missing: true }
  const command = commands[name]
  if (positional.length < command.positional.length)
    throw new UsageError(
      `missing required argument '${command.positional[positional.length]}'`,
    )
  if (positional.length > command.positional.length)
    throw new UsageError(
      `too many arguments for '${name}'; expected ${command.positional.length}`,
    )
  for (const option of command.required)
    if (options[option] === undefined)
      throw new UsageError(`required option '${option}' not specified`)
  return {
    help: false,
    command: name,
    trackerDir: trackerDir ?? defaultTrackerDir(),
    positional,
    options,
  }
}

async function run(request) {
  const directory = request.trackerDir
  const [id, status] = request.positional
  if (request.command === "list") {
    for (const task of await listReadyTasks(directory)) {
      const title = task.title.replace(/\s+/g, " ").trim()
      console.log(
        `${task.id}\t${task.priority ?? "-"}\t${title.length > 120 ? `${title.slice(0, 117)}...` : title}`,
      )
    }
  } else if (request.command === "show") {
    console.log(
      JSON.stringify(await showTask(directory, parseTaskId(id)), null, 2),
    )
  } else if (request.command === "status") {
    if (status !== "todo" && status !== "doing")
      throw new TaskError("Status must be todo or doing.", 2)
    const task = await setTaskStatus(
      directory,
      parseTaskId(id),
      status,
      request.options["--reason"],
    )
    console.log(`Task ${task.id}: ${task.status}`)
  } else if (request.command === "close") {
    await closeTask(
      directory,
      parseTaskId(id),
      request.options["--sha"],
      request.options["--note"],
    )
    console.log(`Task ${id}: done`)
  }
}

export async function main(argv = process.argv.slice(2)) {
  let request
  try {
    request = parseArguments(argv)
  } catch (error) {
    if (!(error instanceof UsageError)) throw error
    console.error(`error: ${error.message}\nRun 'tasks --help' for usage.`)
    process.exitCode = 2
    return
  }
  if (request.help) {
    if (request.missing) {
      process.stderr.write(usage())
      process.exitCode = 2
    } else process.stdout.write(usage(request.command ?? undefined))
    return
  }
  try {
    await run(request)
  } catch (error) {
    console.error(
      error instanceof TaskError ? error.message : "Task command failed.",
    )
    process.exitCode = error instanceof TaskError ? error.exitCode : 1
  }
}

function isEntryPoint() {
  if (!process.argv[1]) return false
  try {
    return (
      realpathSync(process.argv[1]) ===
      realpathSync(fileURLToPath(import.meta.url))
    )
  } catch {
    return false
  }
}

if (isEntryPoint()) await main()

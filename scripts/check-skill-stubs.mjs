#!/usr/bin/env node
// Copied from ras-skills tooling/standalone/check-skill-stubs.mjs. Keep in step with that file; do not edit here.
// Checks, and optionally writes, "see here" stubs for agent skills.
//
// One folder holds the canonical skills; another holds stubs that point to
// them, so each AI tool finds every skill in the folder it reads, with no
// symlinks (which fail on Windows). This file has no dependencies: copy it
// into any repository that needs the check.
//
// Usage: node check-skill-stubs.mjs [--root DIR] [--canonical DIR]
//        [--stubs DIR] [--write [--force]]
// Defaults: root = current directory, canonical = .agents/skills,
// stubs = .claude/skills.

import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs"
import { join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const frontmatterRegex = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/

// Fields a stub must repeat exactly so the tool treats it like the original.
const mirroredFields = ["name", "description", "disable-model-invocation"]

function squash(text) {
  return text.replace(/\s+/g, " ").trim()
}

function unquote(raw) {
  if (raw.startsWith('"') && raw.endsWith('"') && raw.length >= 2) {
    try {
      return JSON.parse(raw)
    } catch {
      return raw.slice(1, -1)
    }
  }
  if (raw.startsWith("'") && raw.endsWith("'") && raw.length >= 2) {
    return raw.slice(1, -1).replace(/''/g, "'")
  }
  return raw
}

/** Splits a SKILL.md into frontmatter fields and body. Handles folded (>) and literal (|) blocks. */
export function parseSkillText(text) {
  const match = text.match(frontmatterRegex)
  if (!match) {
    throw new Error("missing YAML frontmatter")
  }

  const lines = match[1].split(/\r?\n/)
  const fields = {}
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i]
    const separator = line.indexOf(":")
    if (
      line.trim() === "" ||
      line.trimStart().startsWith("#") ||
      /^\s/.test(line) ||
      separator < 1
    ) {
      continue
    }

    const key = line.slice(0, separator).trim()
    const raw = line.slice(separator + 1).trim()
    const block = raw.match(/^([>|])[+-]?$/)
    const continuation = []
    while (
      i + 1 < lines.length &&
      (lines[i + 1].trim() === "" || /^\s/.test(lines[i + 1]))
    ) {
      i += 1
      continuation.push(lines[i].trim())
    }

    if (block) {
      fields[key] = squash(continuation.join(" "))
    } else {
      fields[key] = squash(unquote([raw, ...continuation].join(" ")))
    }
  }

  return { body: match[2], fields }
}

/** The text of a stub that points to a canonical skill. */
export function renderStub({ canonicalDir, fields, folder }) {
  const lines = [
    "---",
    `name: ${fields.name}`,
    `description: ${JSON.stringify(fields.description)}`,
  ]
  if (fields["disable-model-invocation"] !== undefined) {
    lines.push(
      `disable-model-invocation: ${fields["disable-model-invocation"]}`,
    )
  }
  lines.push(
    "---",
    "",
    `Read and follow \`${pointerFor(canonicalDir, folder)}\` from the repository root. That file is the canonical source; this stub contains no separate instructions.`,
    "",
  )
  return lines.join("\n")
}

function pointerFor(canonicalDir, folder) {
  return `${canonicalDir}/${folder}/SKILL.md`
}

function listSkillFolders(dir) {
  if (!existsSync(dir)) {
    return []
  }
  return readdirSync(dir, { withFileTypes: true })
    .filter(
      (entry) =>
        entry.isDirectory() && existsSync(join(dir, entry.name, "SKILL.md")),
    )
    .map((entry) => entry.name)
    .sort()
}

function readSkill(dir, folder, problems, label) {
  try {
    return parseSkillText(readFileSync(join(dir, folder, "SKILL.md"), "utf8"))
  } catch (error) {
    problems.push(`${label}/${folder}/SKILL.md: ${error.message}`)
    return undefined
  }
}

/** Compares every canonical skill with its stub. Returns { problems, canonical, stubs }. */
export function checkStubs({ canonicalDir, root, stubDir }) {
  const problems = []
  const canonicalRoot = join(root, canonicalDir)
  const stubRoot = join(root, stubDir)
  const canonicalFolders = listSkillFolders(canonicalRoot)
  const stubFolders = listSkillFolders(stubRoot)
  let stubCount = 0

  for (const folder of canonicalFolders) {
    const canonical = readSkill(canonicalRoot, folder, problems, canonicalDir)
    if (!canonical) {
      continue
    }
    if (canonical.fields.name !== folder) {
      problems.push(
        `${canonicalDir}/${folder}/SKILL.md: name "${canonical.fields.name}" does not match the folder name`,
      )
      continue
    }
    if (!stubFolders.includes(folder)) {
      problems.push(
        `missing stub: ${stubDir}/${folder}/SKILL.md (run with --write)`,
      )
      continue
    }

    const stub = readSkill(stubRoot, folder, problems, stubDir)
    if (!stub) {
      continue
    }
    const pointer = pointerFor(canonicalDir, folder)
    if (!stub.body.includes(pointer)) {
      problems.push(
        `${stubDir}/${folder}/SKILL.md is a full copy, not a stub; it should point to ${pointer}`,
      )
      continue
    }
    stubCount += 1
    for (const field of mirroredFields) {
      if ((canonical.fields[field] ?? "") !== (stub.fields[field] ?? "")) {
        problems.push(
          `${stubDir}/${folder}/SKILL.md: ${field} differs from the canonical skill`,
        )
      }
    }
  }

  // A stub whose canonical skill has gone is a dead pointer. A skill that
  // names no canonical path is just a project-only skill, so it is left alone.
  const deadPointer = new RegExp(
    `${canonicalDir.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}/([^/\\s\`]+)/SKILL\\.md`,
  )
  for (const folder of stubFolders) {
    if (canonicalFolders.includes(folder)) {
      continue
    }
    const stub = readSkill(stubRoot, folder, problems, stubDir)
    const target = stub?.body.match(deadPointer)
    if (target) {
      problems.push(
        `${stubDir}/${folder}/SKILL.md points to ${target[0]}, which does not exist`,
      )
    }
  }

  return {
    canonical: canonicalFolders.length,
    problems,
    stubs: stubCount,
  }
}

/** Creates or updates stubs. A full copy is only replaced with force. Returns { written, skipped }. */
export function writeStubs({ canonicalDir, force = false, root, stubDir }) {
  const written = []
  const skipped = []
  const canonicalRoot = join(root, canonicalDir)
  const stubRoot = join(root, stubDir)

  for (const folder of listSkillFolders(canonicalRoot)) {
    const problems = []
    const canonical = readSkill(canonicalRoot, folder, problems, canonicalDir)
    if (!canonical) {
      skipped.push(problems[0])
      continue
    }

    const target = join(stubRoot, folder, "SKILL.md")
    const text = renderStub({
      canonicalDir,
      fields: { ...canonical.fields, name: folder },
      folder,
    })
    if (existsSync(target)) {
      const current = readFileSync(target, "utf8")
      if (current === text) {
        continue
      }
      if (!current.includes(pointerFor(canonicalDir, folder)) && !force) {
        skipped.push(
          `${stubDir}/${folder}/SKILL.md is a full copy; not replaced without --force`,
        )
        continue
      }
    }
    mkdirSync(join(stubRoot, folder), { recursive: true })
    writeFileSync(target, text)
    written.push(`${stubDir}/${folder}/SKILL.md`)
  }

  return { skipped, written }
}

function parseArguments(argv) {
  const options = {
    canonicalDir: ".agents/skills",
    force: false,
    root: process.cwd(),
    stubDir: ".claude/skills",
    write: false,
  }
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i]
    if (flag === "--write") {
      options.write = true
    } else if (flag === "--force") {
      options.force = true
    } else if (["--root", "--canonical", "--stubs"].includes(flag)) {
      const value = argv[(i += 1)]
      if (!value) {
        throw new Error(`${flag} needs a value`)
      }
      if (flag === "--root") {
        options.root = resolve(value)
      } else if (flag === "--canonical") {
        options.canonicalDir = value.replace(/\/+$/, "")
      } else {
        options.stubDir = value.replace(/\/+$/, "")
      }
    } else {
      throw new Error(`Unknown argument: ${flag}`)
    }
  }
  return options
}

function main() {
  let options
  try {
    options = parseArguments(process.argv.slice(2))
  } catch (error) {
    console.error(error.message)
    process.exit(2)
  }

  if (options.write) {
    const { skipped, written } = writeStubs(options)
    for (const path of written) {
      console.log(`wrote ${path}`)
    }
    for (const message of skipped) {
      console.error(message)
    }
  }

  const result = checkStubs(options)
  if (result.problems.length > 0) {
    for (const problem of result.problems) {
      console.error(problem)
    }
    process.exit(1)
  }
  console.log(
    `${result.stubs} of ${result.canonical} canonical skills have matching stubs in ${options.stubDir}.`,
  )
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  main()
}

# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`GLOSSARY.md`** at the repo root
- **`docs/adr/`**: read ADRs that touch the area you're about to work in (especially ADR 002 for system vs world trackers)

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront.

## File structure

Single-context repo:

```
/
├── GLOSSARY.md
├── docs/adr/
└── …
```

## Consumer rules

- Prefer glossary terms over synonyms the glossary marks as “Avoid”.
- Respect accepted ADRs; do not re-litigate them in a ticket without an explicit supersede.
- System tracker (`tasks/`) is worldbuilder engineering only; world QUEUE/ISSUES stay in the active world repo (ADR 002).

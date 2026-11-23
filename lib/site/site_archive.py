#!/usr/bin/env python3
"""Archive dated reviews from site/REVIEWS.txt, retaining the newest entries.

  python3 scripts/site_archive.py --world <world> run [--dry-run] [--keep-reviews N]

Review text and order are preserved. Archives are written atomically before the
live file, and existing blocks are deduplicated on an interrupted run's retry.
Structured issues are archived by scripts/issues.py.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from dataclasses import dataclass, field

from script_env import ROOT  # noqa: E402
SITE = ROOT / 'site'
ARCHIVE_DIRNAME = 'archive'

DEFAULT_KEEP_REVIEWS = 10

REVIEW_HEAD_RE = re.compile(r'20\d\d-\d\d-\d\d\b')


@dataclass
class Block:
    """A run of lines from a site file.

    ``key`` is the review head line or ``None``
    for free text (preamble, section headers) that always stays live.
    """

    lines: list[str]
    key: str | None = None
    archive: bool = False


@dataclass
class Spec:
    """How one live file maps to its archive."""

    live: str
    archive: str
    title: str
    pointer: str
    blocks: list[Block] = field(default_factory=list)


def _split_reviews(lines):
    """Split REVIEWS.txt: each dated entry runs until the next dated line.

    TOKEN NOTES and other column-0 text after an entry stay with that entry.

    @param lines: file lines without newlines
    @returns: list of Block
    """
    blocks = []
    cur = Block([])
    for line in lines:
        if REVIEW_HEAD_RE.match(line):
            if cur.lines:
                blocks.append(cur)
            cur = Block([line], key=line.rstrip())
            continue
        cur.lines.append(line)
    if cur.lines:
        blocks.append(cur)
    return blocks


def plan_reviews(text, keep=DEFAULT_KEEP_REVIEWS):
    """Mark every dated review entry except the last ``keep`` for archiving.

    @param text: REVIEWS.txt contents
    @param keep: number of most recent entries to leave live (>= 0)
    @returns: list of Block
    """
    if keep < 0:
        raise ValueError('keep must be >= 0')
    blocks = _split_reviews(text.splitlines())
    dated = [b for b in blocks if b.key]
    for b in dated[: max(len(dated) - keep, 0)]:
        b.archive = True
    return blocks


def _tidy(lines):
    """Strip trailing blanks and collapse runs of blank lines to one.

    @param lines: list of lines
    @returns: new list of lines
    """
    out = []
    for line in lines:
        if not line.strip() and out and not out[-1].strip():
            continue
        out.append(line.rstrip() if not line.strip() else line)
    while out and not out[-1].strip():
        out.pop()
    return out


def _body(block):
    """Block text without trailing blank lines (used for archive dedupe).

    @param block: Block
    @returns: string
    """
    return '\n'.join(_tidy(block.lines))


def _atomic_write(path, text):
    """Write ``text`` to ``path`` via a temp file + rename in the same dir.

    @param path: Path
    @param text: full new contents
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _with_pointer(blocks, pointer):
    """Return live lines, adding the archive pointer to the preamble once.

    @param blocks: blocks that stay live
    @param pointer: one-line note naming the archive file
    @returns: list of lines
    """
    lines = [l for b in blocks for l in b.lines]
    if any(pointer in l for l in lines):
        return lines
    pre = blocks[0].lines if blocks and blocks[0].key is None else []
    insert_at = 0
    for i, line in enumerate(pre):
        if not line.strip():
            break
        insert_at = i + 1
    return lines[:insert_at] + [pointer] + lines[insert_at:]


def archive_file(spec, site=SITE, dry=False):
    """Move ``spec``'s archivable blocks from its live file into its archive.

    @param spec: Spec with ``blocks`` already planned
    @param site: site directory holding the live file and ``archive/``
    @param dry: report only; write nothing
    @returns: (moved_keys, skipped_duplicate_keys)
    """
    moving = [b for b in spec.blocks if b.archive]
    if not moving:
        return [], []
    arc_path = site / ARCHIVE_DIRNAME / spec.archive
    existing = arc_path.read_text(encoding='utf-8') if arc_path.exists() else ''
    new_bodies, moved, dupes = [], [], []
    for b in moving:
        body = _body(b)
        if body and body in existing:
            dupes.append(b.key)
            continue
        new_bodies.append(body)
        moved.append(b.key)
    if dry:
        return moved, dupes
    if new_bodies:
        head = existing.rstrip('\n') if existing else spec.title
        _atomic_write(arc_path, head + '\n\n' + '\n\n'.join(new_bodies) + '\n')
    live_blocks = [b for b in spec.blocks if not b.archive]
    live_lines = _tidy(_with_pointer(live_blocks, spec.pointer))
    _atomic_write(site / spec.live, '\n'.join(live_lines) + '\n')
    return moved, dupes


def build_specs(site=SITE, keep_reviews=DEFAULT_KEEP_REVIEWS):
    """Plan the reviews archive; a missing live file is skipped.

    @param site: site directory
    @param keep_reviews: dated review entries to leave live
    @returns: list of Spec with blocks planned
    """
    path = site / 'REVIEWS.txt'
    if not path.exists():
        return []
    spec = Spec(
        'REVIEWS.txt', 'REVIEWS-archive.txt',
        'WORLDBUILDER — REVIEWS (archived entries, oldest first)',
        'Older reviews: site/archive/REVIEWS-archive.txt (scripts/site_archive.py).',
    )
    spec.blocks = plan_reviews(path.read_text(encoding='utf-8'), keep_reviews)
    return [spec]


def run(site=SITE, dry=False, keep_reviews=DEFAULT_KEEP_REVIEWS, out=sys.stdout):
    """Archive older dated reviews from the live reviews file.

    @param site: site directory
    @param dry: report only; write nothing
    @param keep_reviews: dated review entries to leave live
    @param out: stream for the report
    @returns: total number of blocks moved (or that would move when ``dry``)
    """
    total = 0
    for spec in build_specs(site, keep_reviews):
        moved, dupes = archive_file(spec, site, dry)
        total += len(moved)
        verb = 'would move' if dry else 'moved'
        label = ', '.join(k if not k.startswith('20') else k[:10] for k in moved)
        print(f'{spec.live}: {verb} {len(moved)} -> archive/{spec.archive}'
              + (f' ({label})' if moved and len(label) < 400 else ''), file=out)
        if dupes:
            print(f'  already archived, dropped from live: {len(dupes)}', file=out)
    return total


def main(argv=None):
    """CLI entry point.

    @param argv: argument list (defaults to sys.argv[1:])
    @returns: exit code
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv.pop(0) if argv else ''
    if cmd == 'run':
        keep = DEFAULT_KEEP_REVIEWS
        if '--keep-reviews' in argv:
            i = argv.index('--keep-reviews')
            try:
                keep = int(argv[i + 1])
            except (IndexError, ValueError):
                print('--keep-reviews needs an integer', file=sys.stderr)
                return 2
            if keep < 0:
                print('--keep-reviews must be >= 0', file=sys.stderr)
                return 2
        run(dry='--dry-run' in argv, keep_reviews=keep)
        return 0
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())

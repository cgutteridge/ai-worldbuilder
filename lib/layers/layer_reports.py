"""World-local diagnostic artifacts and comparable apply stage counts."""
from __future__ import annotations

from collections import Counter
from pathlib import Path


def artifact_path(name: str, artifact: str, *, dry: bool = False, directory: Path | None = None) -> Path:
    """Resolve every element artifact through one redirectable path boundary."""
    from script_env import ROOT
    directory = Path(directory) if directory is not None else ROOT / 'verification/site'
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f'{name}{"-dry" if dry else ""}-{artifact}'


def write_report(name: str, report: dict, directory: Path | None = None, *, dry: bool = False) -> Path:
    """Write all item/column details; dry runs preserve the last apply's report."""
    from atomic_json import write_json
    path = artifact_path(name, 'report.json', dry=dry, directory=directory)
    write_json(path, report)
    print(f'  full report: {path}')
    return path


def reason_summary(reasons) -> dict[str, int]:
    """Count exclusions without truncating the artifact's item-level reasons."""
    return dict(Counter(reasons).most_common())


def apply_counts(plan: dict, writes: dict, before: dict, actual: dict) -> dict[str, int]:
    """Account for final positions, not unlike candidate-item or request counts.

    Already-correct positions stay in the journal write list and in readback.
    The fill writer omits them from the transport when the begin snapshot matches.
    A transport's number of fill calls is deliberately not a block count.
    """
    from block_store import same_state
    if dict(plan) != dict(writes):
        raise ValueError('journal writes differ from the final guarded plan')
    if set(plan) - actual.keys():
        raise ValueError('readback omitted final plan positions')
    already = sum(same_state(st, before.get(p)) for p, st in plan.items())
    unknown = sum(before.get(p) is None for p in plan)
    return {'final_plan_positions': len(plan), 'journal_write_positions': len(writes),
            'already_correct': already, 'unknown_before_positions': unknown,
            'positions_requiring_change': len(plan) - already - unknown,
            'readback_positions': sum(actual.get(p) is not None for p in plan),
            'verified_positions': sum(same_state(st, actual.get(p)) for p, st in plan.items())}

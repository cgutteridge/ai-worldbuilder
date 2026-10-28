#!/usr/bin/env python3
"""Create a new blank Minecraft save from ``templates/empty-world``.

The save belongs to the standalone Minecraft instance outside this project. This
is the **only** code that writes under an instance's ``saves/`` folder, and it
only ever creates a new folder: an existing target is refused, never overwritten.
"""
from __future__ import annotations

import shutil
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE = TOOL_ROOT / 'templates' / 'empty-world'

# Never carried into a new save (the template has none, but copies may).
_SKIP = shutil.ignore_patterns('session.lock', 'players', '*.dat_old', '*.mca', 'README.txt')


def create_blank_world(save: Path, template: Path = TEMPLATE) -> Path:
    """Copy the empty-world template to a new save folder.

    @param save: new save folder (its parent, the instance's ``saves/``, must exist)
    @param template: template folder (tests)
    @returns: the created save path
    @raises FileExistsError: when ``save`` already exists
    @raises FileNotFoundError: when the parent folder or template is missing
    """
    save = Path(save)
    if save.exists():
        raise FileExistsError(f'refusing to overwrite existing save: {save}')
    if not save.parent.is_dir():
        raise FileNotFoundError(
            f'{save.parent} does not exist: create the Minecraft instance first '
            '(docs/minecraft-instance-setup.md)')
    if not template.is_dir():
        raise FileNotFoundError(f'template missing: {template}')
    shutil.copytree(template, save, ignore=_SKIP)
    return save

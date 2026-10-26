"""Disposable configuration for unit tests; never read a selected world checkout."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from unittest import mock

_TOOL_ROOT = Path(__file__).resolve().parent.parent
if str(_TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOL_ROOT))


@contextmanager
def unit_test_world() -> Iterator[Path]:
    """Select a temporary world before importing world-aware tool modules.

    Coordinates are fixed test inputs, shared by the geometry regression cases.
    The save, registry and evidence directories all live under the temporary root.
    Uses :func:`active_world.set_world_root` (not an environment variable).

    @returns: temporary test-world directory, removed on exit
    """
    with tempfile.TemporaryDirectory(prefix='worldbuilder-unit-') as tmp:
        root = Path(tmp)
        config = root / 'config'
        config.mkdir()
        region = root / 'save' / 'region'
        region.mkdir(parents=True)
        (config / 'world.json').write_text(json.dumps({
            'gameworld_id': 'unit-test',
            'save_path': str(root / 'save'),
            'region_path': str(region),
            'structure_namespace': 'southampton_refine',
        }))
        (config / 'world-frame.json').write_text(json.dumps({
            'place': 'Unit test fixture',
            'crs': 'EPSG:27700',
            'offsets': {'easting': 436248, 'northing': 109608},
            'bounds_mc': {'west': 0, 'east': 11604, 'north': 0, 'south': -8666},
            'cell': {'size': 128, 'origin': [48, -44]},
            'y_offset': 11,
        }))
        (config / 'local-providers.json').write_text(json.dumps({'providers': []}))
        # These providers belong to world repositories. Tool tests must supply
        # their own responses rather than import a place's provider code/data.
        providers = {}
        for name, operations in {
            'portals': ('load', 'describe', 'for_footprint'),
            'buildings': ('describe', 'for_footprint'),
        }.items():
            module = ModuleType(name)
            for operation in operations:
                setattr(module, operation, mock.Mock(side_effect=AssertionError(
                    f'{name}.{operation}: provide an explicit provider response in the test'
                )))
            providers[name] = module
        providers['portals'].DOOR_REACH = 5
        with mock.patch.dict(sys.modules, providers), mock.patch.dict(os.environ, {
            'AI_WORLDBUILDER_REGISTRY_ROOT': str(root),
        }):
            # Hook variables override cwd for git init/rev-parse in temporary
            # repositories. Never let a test mutate the invoking repository.
            for key in (
                'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
                'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
            ):
                os.environ.pop(key, None)
            import active_world
            import script_env

            previous_root = active_world.WORLD_ROOT
            active_world.set_world_root(root)
            script_env.bind_world()
            script_env.clear_world_caches()
            try:
                yield root
            finally:
                active_world.WORLD_ROOT = previous_root
                script_env.clear_world_caches()

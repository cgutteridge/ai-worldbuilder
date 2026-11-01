"""Shared lidar floor reference for footprint-based feature and house builders."""
from __future__ import annotations

import math
import statistics

import numpy as np


def floor_reference(name, label, cells, floor_min=None, grid=None):
    """Return median DTM block Y under a footprint, raised by an optional minimum.

    @param name: build name for missing/invalid terrain diagnostics
    @param label: footprint label
    @param cells: footprint's (x, z) positions
    @param floor_min: optional minimum floor Y
    @param grid: optional prepared ground_y grid with x0/z0 origin
    @returns: integer floor Y; refuses missing or nonfinite terrain
    """
    if grid is not None:
        ground = np.asarray(grid['ground_y'], dtype=float)
        x0, z0 = int(grid['x0']), int(grid['z0'])
    else:
        import lidar_provider as lp
        from world_frame import load_frame, mc_grid_to_en
        xs, zs = zip(*cells)
        x0, z0, x1, z1 = min(xs), min(zs), max(xs), max(zs)
        lidar = lp.tile_client(download=False)
        if lidar.missing('DTM', *lp.mc_box_en(x0, z0, x1, z1)):
            raise SystemExit(f'{name} {label}: lidar DTM cache missing for floor')
        xx, zz = np.meshgrid(np.arange(x0, x1 + 1), np.arange(z0, z1 + 1))
        frame = load_frame()
        east, north = mc_grid_to_en(xx, zz, frame)
        ground = np.trunc(lidar.sample('DTM', east, north, True)) + frame.y_offset
    values = [float(ground[z - z0, x - x0]) for x, z in cells]
    if not all(math.isfinite(y) for y in values):
        raise SystemExit(f'{name} {label}: lidar DTM has no valid ground under footprint')
    y = int(statistics.median(values))
    return max(y, floor_min) if floor_min is not None else y

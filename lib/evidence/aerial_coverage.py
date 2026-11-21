"""Local XYZ aerial mosaics: coverage, selection and sampling through the world frame.

Discover both centre-named mosaics and legacy northwest-named mosaics. Images
are never fetched here. Black/no-data pixels do not establish evidence coverage.
"""
from __future__ import annotations

import contextlib
import contextvars
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

import cellmath
from script_env import ROOT
from world_frame import mc_batch_to_wgs84, wgs84_to_wm_global_px


class CoverageError(ValueError):
    """A cell lacks usable aerial evidence; an empty detector result would be misleading."""


# Active only inside rgb_cache. None means each load_rgb call decodes again.
_rgb_cache: contextvars.ContextVar[dict[tuple[str, int, int], np.ndarray] | None] = (
    contextvars.ContextVar('aerial_rgb_cache', default=None)
)


@contextlib.contextmanager
def rgb_cache() -> Iterator[None]:
    """Reuse aerial RGB decodes until this planning run ends.

    Nested calls share the outer cache. ``require_coverage`` decodes through
    ``samples``, and a trees grid then samples again and decodes the same
    mosaic in the canopy or cover detector. One cache covers that whole run.

    @returns: context manager; no value
    """
    if _rgb_cache.get() is not None:
        yield
        return
    token = _rgb_cache.set({})
    try:
        yield
    finally:
        _rgb_cache.reset(token)


def load_rgb(path: Path) -> np.ndarray:
    """Decoded RGB pixels for ``path``.

    Inside ``rgb_cache``, the same resolved path, mtime and size returns the
    same read-only array. Outside a run, every call decodes.

    @param path: local image file
    @returns: uint8 array shaped ``(height, width, 3)``
    """
    cache = _rgb_cache.get()
    key = None
    if cache is not None:
        resolved = Path(path).resolve()
        stat = resolved.stat()
        key = (str(resolved), stat.st_mtime_ns, stat.st_size)
        hit = cache.get(key)
        if hit is not None:
            return hit
    with Image.open(path) as image:
        rgb = np.array(image.convert('RGB'), dtype=np.uint8, copy=True)
    if cache is not None:
        rgb.setflags(write=False)
        cache[key] = rgb
    return rgb


@dataclass(frozen=True)
class Mosaic:
    """One local mosaic, located by XYZ northwest tile and logical tile pixel size."""

    path: Path
    zoom: int
    tile_x: int
    tile_y: int
    scale: float = 1.0

    def pixels(self, points):
        """MC column centres to actual image pixels using the canonical CRS/XYZ functions."""
        ll = mc_batch_to_wgs84([(x + .5, z + .5) for x, z in points])
        return {p: ((px - self.tile_x * 256) * self.scale,
                    (py - self.tile_y * 256) * self.scale)
                for p, (lat, lon) in zip(points, ll)
                for px, py in [wgs84_to_wm_global_px(lat, lon, self.zoom)]}


def mosaics(root=None):
    """Discover geolocated mosaic PNGs; malformed mosaic names fail visibly."""
    base = Path(root) if root is not None else Path(ROOT)
    out = []
    pattern = re.compile(r'mosaic-z(\d+)-(\d+)x(\d+)-(centre-)?(\d+)-(\d+)(?:-.*)?\.png$')
    for path in sorted((base / 'reference' / 'aerial').rglob('mosaic-z*.png')):
        if path.stem.endswith(('-mask', '-preview', '-annotated')):
            continue
        m = pattern.fullmatch(path.name)
        if not m:
            raise ValueError(f'aerial mosaic has unknown geolocation: {path}')
        zoom, nx, ny = map(int, m.group(1, 2, 3))
        x, y = map(int, m.group(5, 6))
        if m.group(4):
            x, y = x - nx // 2, y - ny // 2
        with Image.open(path) as im:
            scale = im.width / (nx * 256)
            if not np.isclose(im.height / (ny * 256), scale):
                raise ValueError(f'aerial mosaic dimensions disagree with XYZ grid: {path}')
        out.append(Mosaic(path, zoom, x, y, scale))
    return sorted(out, key=lambda m: (-m.zoom, str(m.path)))


def cell_columns(cell, margin=0):
    """Columns in one named cell, with optional sampling margin."""
    x0, z0, x1, z1 = cellmath.bounds(cell)
    return [(x, z) for x in range(x0 - margin, x1 + margin + 1)
            for z in range(z0 - margin, z1 + margin + 1)]


def samples(points, sources, *, radius=0):
    """Best-resolution usable sample per column as ``(mosaic, px, py, RGB)``.

    Multiple mosaics may jointly cover a cell. Later sources only fill gaps.
    """
    out = {}
    for mosaic in sorted(sources, key=lambda m: (-m.zoom, str(m.path))):
        todo = [p for p in points if p not in out]
        if not todo:
            break
        rgb = load_rgb(mosaic.path)
        for p, (px, py) in mosaic.pixels(todo).items():
            if not (0 <= px < rgb.shape[1] and 0 <= py < rgb.shape[0]):
                continue
            ix, iy = int(px), int(py)
            win = rgb[max(0, iy - radius):iy + radius + 1,
                      max(0, ix - radius):ix + radius + 1]
            colour = tuple(win.reshape(-1, 3).mean(0) / 255)
            if sum(colour) / 3 < .01:
                continue
            out[p] = mosaic, px, py, colour
    return out


def coverage(cell, sources=None):
    """Fraction of the cell's columns covered by the union of usable local mosaics."""
    points = cell_columns(cell)
    return len(samples(points, mosaics() if sources is None else sources)) / len(points)


def require_coverage(cell, sources=None):
    """Return selected sources or raise with the actual fraction; never download."""
    sources = mosaics() if sources is None else sources
    fraction = coverage(cell, sources)
    if fraction < 1:
        raise CoverageError(f'needs-prep: aerial coverage {fraction:.1%} for cell '
                            f'{cell[0]},{cell[1]}; a locally cached mosaic covering the rest is required '
                            '(no implicit download)')
    return sources


def sample_rgb(cell, sources=None, *, margin=0):
    """RGB in [0,1] by MC column; refuse incomplete core-cell evidence."""
    sources = require_coverage(cell, sources)
    return {p: sample[3] for p, sample in samples(cell_columns(cell, margin), sources, radius=2).items()}


def cell_image(cell, sources=None):
    """North-up cell image assembled from every contributing local mosaic."""
    rgb = sample_rgb(cell, sources)
    x0, z0, x1, z1 = cellmath.bounds(cell)
    pixels = np.zeros((z1 - z0 + 1, x1 - x0 + 1, 3), dtype=np.uint8)
    for (x, z), colour in rgb.items():
        pixels[z - z0, x - x0] = np.asarray(colour) * 255
    return Image.fromarray(pixels)

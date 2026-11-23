"""Car-park bay lines copied from the aerial (#449).

The ground pass asks :func:`mark_car_park_bays` once per cell, after
:func:`ground_layer.classify`. A column becomes ``mark`` (``white_concrete``)
only when the aerial shows a thin pale streak there, the column sits in a
mapped surface car park (``amenity=parking``, not underground or multi-storey),
and the ground pass has already called that column tarmac.

The streak is whatever the image shows. Single-pixel specks, car-sized blobs
and streaks shorter than about three blocks are dropped. A streak that sits
on the polygon edge is the boundary, not a bay line. Gaps are left as gaps:
nothing is straightened or filled in from the mapped aisle. A park with no
streak about six blocks long draws nothing; that is not called soft, there
is simply no line to copy. When the aerial is too blurred to resolve a
one-pixel painted line, that park is left unmarked and named ``aerial too
soft`` in the ground report. There is no generated grid and no fallback guess.

Lines are painted only where the ground pass has called the column tarmac
and the mapped priority is still the car park. An aisle, a road or a path
painted over the polygon keeps its own priority and gets no line.

The ground pass writes these marks on its own plan, which is exempt from the
#327 ground-pass gate, so they do not go through ``Plan.ground_marking``.
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np

from ground_sources import PRI

# Tuned on the z19 mosaic the ground pass samples (~5.3 px per block on cell
# 50,-35), then frozen. The white bay lines there are about one pixel wide and
# clearly paler than the tarmac beside them (acuity ~0.40). A 3×3 majority
# would erase that line. The pale threshold is not lowered: the treed park on
# the same cell has no painted lines, and a lower threshold paints cars.
_PALE = 0.10                 # luminance above the local median
_RADIUS_AT_Z19 = 12          # median window ~2 blocks; a thin line does not move it
_MIN_RUN_AT_Z19 = 6          # shorter than ~1 block is a speck
_MIN_SPAN_AT_Z19 = 18        # ~3.4 blocks; a car edge or a branch is shorter
_MIN_ASPECT = 3.0            # long axis over short axis; a straight painted line
_JOINED_FAT = 0.08           # a hatch that touches itself is thin, not one straight line
_JOINED_PIXELS_AT_Z19 = 40   # and larger than a car edge
_EDGE_RADIUS_AT_Z19 = 4      # pixels; a streak in this band is the polygon boundary
_EDGE_FRAC = 0.65            # mostly-on-the-boundary components are not bay lines
_CLEAR_SPAN_AT_Z19 = 32      # ~6 blocks. No streak this long: draw nothing, do not say soft
_PX_PER_BLOCK_Z19 = 5.3
_FAT_RATIO = 0.25            # 4-neighbour core / area; a line erodes away, a car does not
_CLEAR_ACUITY = 0.36         # |1px step| / |3px step|. A radius-2 box blur scores ~1/3;
                                 # the z19 mosaic that shows the 50,-35 bay lines scores ~0.40.
_ACUITY_CONTRAST = 0.08      # a 3px luminance change at least this large is an edge we can judge
_MIN_EDGES = 40              # fewer strong edges than this is an empty picture, not a blurry one


class AerialFrame(NamedTuple):
    """One aerial crop of one car park, with each pixel's Minecraft column."""

    rgb: np.ndarray          # float HxWx3, 0..1
    mask: np.ndarray         # bool HxW, pixels inside this car park
    mx: np.ndarray           # int HxW column x
    mz: np.ndarray           # int HxW column z
    label: str               # car-park bbox, shared by every crop of that park
    px_per_block: float = _PX_PER_BLOCK_Z19
    missing: bool = False


class BayLineResult(NamedTuple):
    """Columns marked, and car parks whose aerial was too soft to read."""

    n: int
    soft: tuple[str, ...]


def _lengths(px_per_block: float) -> tuple[int, int, int, int, int, int]:
    """Pixel lengths for this image scale.

    @param px_per_block: aerial pixels across one Minecraft block
    @returns: ``(radius, min_run, min_span, joined_pixels, clear_span, edge_radius)``
    """
    k = float(px_per_block) / _PX_PER_BLOCK_Z19
    radius = max(3, int(round(_RADIUS_AT_Z19 * k)))
    min_run = max(4, int(round(_MIN_RUN_AT_Z19 * k)))
    min_span = max(8, int(round(_MIN_SPAN_AT_Z19 * k)))
    joined = max(16, int(round(_JOINED_PIXELS_AT_Z19 * k)))
    clear = max(16, int(round(_CLEAR_SPAN_AT_Z19 * k)))
    edge_radius = max(2, int(round(_EDGE_RADIUS_AT_Z19 * k)))
    return radius, min_run, min_span, joined, clear, edge_radius


def _local_median(values: np.ndarray, radius: int) -> np.ndarray:
    """Median of a square window around each pixel.

    @param values: 2-d luminance
    @param radius: half-width of the window in pixels
    @returns: median image, same shape as ``values``
    """
    pad = np.pad(values, radius, mode='edge')
    height, width = values.shape
    out = np.empty_like(values)
    win = 2 * radius + 1
    for y0 in range(0, height, 40):
        y1 = min(height, y0 + 40)
        block = np.lib.stride_tricks.sliding_window_view(pad[y0:y1 + 2 * radius], (win, win))
        out[y0:y1] = np.median(block, axis=(-1, -2))
    return out


def _components(hit: np.ndarray) -> list[list[tuple[int, int]]]:
    """8-connected components of a boolean image. Diagonal paint stays one run; a gap does not join.

    @param hit: boolean mask
    @returns: each component as ``(y, x)`` pixels
    """
    height, width = hit.shape
    seen = np.zeros_like(hit)
    out = []
    ys, xs = np.nonzero(hit)
    for y, x in zip(ys.tolist(), xs.tolist()):
        if seen[y, x]:
            continue
        stack = [(y, x)]
        seen[y, x] = True
        comp = []
        while stack:
            cy, cx = stack.pop()
            comp.append((cy, cx))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dy == 0 and dx == 0:
                        continue
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < height and 0 <= nx < width and hit[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        out.append(comp)
    return out


def _core_count(comp: list[tuple[int, int]], height: int, width: int) -> int:
    """Pixels of ``comp`` whose four orthogonal neighbours are also in it.

    A one-pixel line has none. A filled blob keeps its interior.

    @param comp: component pixels
    @param height: image height
    @param width: image width
    @returns: surviving pixel count
    """
    mask = np.zeros((height, width), dtype=bool)
    for y, x in comp:
        mask[y, x] = True
    pad = np.pad(mask, 1)
    keep = pad[1:-1, 1:-1].copy()
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        keep &= pad[1 + dy:1 + dy + height, 1 + dx:1 + dx + width]
    return int(keep.sum())


def _directed_steps(luminance: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Largest one-pixel and three-pixel steps that share a direction.

    A pixel three away from a sharp line is not itself an edge: the three-pixel
    step is large there, but the one-pixel step is not, so that direction is
    ignored. The returned one-pixel step is the one beside the winning
    three-pixel step.

    @param luminance: 2-d luminance, 0..1
    @returns: ``(step1, step3)`` aligned to ``luminance``
    """
    step1 = np.zeros_like(luminance)
    step3 = np.zeros_like(luminance)
    dirs = []
    height, width = luminance.shape
    if width > 3:
        right1 = np.zeros_like(luminance)
        right3 = np.zeros_like(luminance)
        right1[:, 1:] = np.abs(luminance[:, 1:] - luminance[:, :-1])
        right3[:, 3:] = np.abs(luminance[:, 3:] - luminance[:, :-3])
        dirs.append((right1, right3))
        left1 = np.zeros_like(luminance)
        left3 = np.zeros_like(luminance)
        left1[:, :-1] = np.abs(luminance[:, 1:] - luminance[:, :-1])
        left3[:, :-3] = np.abs(luminance[:, 3:] - luminance[:, :-3])
        dirs.append((left1, left3))
    if height > 3:
        down1 = np.zeros_like(luminance)
        down3 = np.zeros_like(luminance)
        down1[1:, :] = np.abs(luminance[1:, :] - luminance[:-1, :])
        down3[3:, :] = np.abs(luminance[3:, :] - luminance[:-3, :])
        dirs.append((down1, down3))
        up1 = np.zeros_like(luminance)
        up3 = np.zeros_like(luminance)
        up1[:-1, :] = np.abs(luminance[1:, :] - luminance[:-1, :])
        up3[:-3, :] = np.abs(luminance[3:, :] - luminance[:-3, :])
        dirs.append((up1, up3))
    if not dirs:
        return step1, step3
    stacked3 = np.stack([d3 for _, d3 in dirs], axis=0)
    winner = np.argmax(stacked3, axis=0)
    stacked1 = np.stack([d1 for d1, _ in dirs], axis=0)
    step3 = np.max(stacked3, axis=0)
    step1 = np.take_along_axis(stacked1, winner[None], axis=0)[0]
    return step1, step3


def _acuity(luminance: np.ndarray, mask: np.ndarray) -> tuple[float, int]:
    """How much of a 3-pixel luminance step happens in the first pixel.

    A sharp edge scores near 1. A blurred edge scores low. A flat picture, or
    one whose only contrast is a distant step this pixel does not itself make,
    has no edges to judge.

    @param luminance: 2-d luminance, 0..1
    @param mask: pixels that belong to the car park
    @returns: ``(median ratio, number of strong edges)``. The ratio is ``nan``
        when there are no strong edges.
    """
    step1, step3 = _directed_steps(luminance)
    # The one-pixel step has to be real. A pixel that only sees a line three
    # pixels away is not an edge.
    strong = mask & (step3 >= _ACUITY_CONTRAST) & (step1 >= 0.02)
    count = int(strong.sum())
    if count == 0:
        return float('nan'), 0
    return float(np.median(step1[strong] / step3[strong])), count


def park_too_soft(luminance: np.ndarray, mask: np.ndarray) -> bool:
    """True when this car park's aerial is too blurred to copy a painted line.

    An empty or uniformly flat picture is not soft: there is simply nothing to
    copy. Soft means the picture has edges, and those edges are ramps rather
    than one-pixel steps.

    @param luminance: 2-d luminance, 0..1
    @param mask: pixels inside the car park
    @returns: whether to draw nothing and report ``aerial too soft``
    """
    ratio, count = _acuity(luminance, mask)
    return count >= _MIN_EDGES and ratio < _CLEAR_ACUITY


def _span_aspect(comp: list[tuple[int, int]]) -> tuple[float, float]:
    """Length and straightness of one pale component.

    @param comp: ``(y, x)`` pixels
    @returns: ``(span, aspect)``. Span is the diagonal of the pixel box.
        Aspect is the long principal axis over the short one (1 when the
        component is too small to measure).
    """
    ys = np.array([p[0] for p in comp], dtype=np.float64)
    xs = np.array([p[1] for p in comp], dtype=np.float64)
    span = float(np.hypot(ys.max() - ys.min(), xs.max() - xs.min()))
    if len(comp) < 3:
        return span, 1.0
    centred = np.stack([ys, xs], axis=1)
    centred -= centred.mean(axis=0)
    eig = np.clip(np.linalg.eigvalsh((centred.T @ centred) / len(comp)), 1e-9, None)
    return span, float(np.sqrt(eig[-1] / eig[0]))


def _edge_band(mask: np.ndarray, radius: int) -> np.ndarray:
    """Car-park pixels within ``radius`` of a pixel outside the park.

    @param mask: pixels inside the car park
    @param radius: band width in pixels
    @returns: boolean band, same shape as ``mask``
    """
    outside = ~mask
    near = np.zeros_like(mask)
    height, width = mask.shape
    for dy in range(-radius, radius + 1):
        y_src0, y_dst0 = max(0, -dy), max(0, dy)
        y_len = height - abs(dy)
        for dx in range(-radius, radius + 1):
            x_src0, x_dst0 = max(0, -dx), max(0, dx)
            x_len = width - abs(dx)
            near[y_dst0:y_dst0 + y_len, x_dst0:x_dst0 + x_len] |= (
                outside[y_src0:y_src0 + y_len, x_src0:x_src0 + x_len])
    return mask & near


def thin_pale_mask(rgb: np.ndarray, mask: np.ndarray, *, px_per_block: float = _PX_PER_BLOCK_Z19
                   ) -> tuple[np.ndarray, bool]:
    """Pixels of thin pale streaks inside ``mask``.

    A park whose longest kept streak is under about six blocks returns an
    empty mask and ``too_soft`` false: the picture may be sharp, and there
    is no bay line to copy.

    @param rgb: float image, HxWx3, 0..1
    @param mask: pixels inside the car park
    @param px_per_block: aerial pixels across one block, for the speck length
    @returns: ``(keep, too_soft)``. ``keep`` is empty when the park is too soft
        or shows no long streak.
    """
    height, width = mask.shape
    empty = np.zeros((height, width), dtype=bool)
    if not mask.any():
        return empty, False
    luminance = rgb.reshape(height, width, -1).mean(axis=2) if rgb.ndim == 3 else rgb
    if park_too_soft(luminance, mask):
        return empty, True
    radius, min_run, min_span, joined_n, clear_span, edge_radius = _lengths(px_per_block)
    excess = luminance - _local_median(luminance, radius)
    pale = mask & (excess >= _PALE)
    edge = _edge_band(mask, edge_radius)
    kept: list[tuple[float, list[tuple[int, int]]]] = []
    for comp in _components(pale):
        if len(comp) < min_run:
            continue
        fat = _core_count(comp, height, width) / len(comp)
        if fat > _FAT_RATIO:
            continue
        span, aspect = _span_aspect(comp)
        if span < min_span:
            continue
        joined = fat <= _JOINED_FAT and len(comp) >= joined_n
        if aspect < _MIN_ASPECT and not joined:
            continue
        ys = [p[0] for p in comp]
        xs = [p[1] for p in comp]
        if float(edge[ys, xs].mean()) >= _EDGE_FRAC:
            continue
        kept.append((span, comp))
    if not kept or max(span for span, _comp in kept) < clear_span:
        return empty, False
    keep = np.zeros_like(pale)
    for _span, comp in kept:
        for y, x in comp:
            keep[y, x] = True
    return keep, False


def columns_of(keep: np.ndarray, mx: np.ndarray, mz: np.ndarray) -> set[tuple[int, int]]:
    """Minecraft columns that contain a kept line pixel.

    @param keep: boolean line pixels
    @param mx: column x per pixel
    @param mz: column z per pixel
    @returns: column set
    """
    if not keep.any():
        return set()
    return set(zip(mx[keep].tolist(), mz[keep].tolist()))


def _park_label(area: set) -> str:
    """Bbox of a car park, used as the report name.

    @param area: columns of one parking polygon
    @returns: ``x0,z0..x1,z1``
    """
    xs = [p[0] for p in area]
    zs = [p[1] for p in area]
    return f'{min(xs)},{min(zs)}..{max(xs)},{max(zs)}'


def _mask_columns(mx: np.ndarray, mz: np.ndarray, area: set) -> np.ndarray:
    """True where the pixel's column is in ``area``.

    @param mx: column x per pixel
    @param mz: column z per pixel
    @param area: car-park columns
    @returns: boolean mask
    """
    xs = [p[0] for p in area]
    zs = [p[1] for p in area]
    x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
    raster = np.zeros((z1 - z0 + 1, x1 - x0 + 1), dtype=bool)
    for x, z in area:
        raster[z - z0, x - x0] = True
    inside = (mx >= x0) & (mx <= x1) & (mz >= z0) & (mz <= z1)
    mask = np.zeros(mx.shape, dtype=bool)
    mask[inside] = raster[mz[inside] - z0, mx[inside] - x0]
    return mask


def _crop_box(pixels: dict) -> tuple[int, int, int, int]:
    """Pixel bbox of column-centre samples, padded by about a block.

    @param pixels: ``{(x, z): (u, v)}``
    @returns: ``u0, v0, u1, v1`` inclusive
    """
    us = [p[0] for p in pixels.values()]
    vs = [p[1] for p in pixels.values()]
    pad = 8
    return (int(np.floor(min(us))) - pad, int(np.floor(min(vs))) - pad,
            int(np.ceil(max(us))) + pad, int(np.ceil(max(vs))) + pad)


def _frame(rgb_u8: np.ndarray, u0: int, v0: int, u1: int, v1: int, area: set,
           mc_of, ppb: float, label: str) -> AerialFrame | None:
    """Crop ``rgb_u8`` and attach a column to each pixel.

    @param rgb_u8: full image, uint8 HxWx3
    @param u0: crop left
    @param v0: crop top
    @param u1: crop right, inclusive
    @param v1: crop bottom, inclusive
    @param area: car-park columns this crop covers
    @param mc_of: ``(u_array, v_array) -> (x_float, z_float)``
    @param ppb: pixels per block
    @param label: car-park bbox
    @returns: a frame, or None when the crop misses the image
    """
    height, width = rgb_u8.shape[:2]
    u0, v0 = max(0, u0), max(0, v0)
    u1, v1 = min(width - 1, u1), min(height - 1, v1)
    if u1 <= u0 or v1 <= v0:
        return None
    crop = rgb_u8[v0:v1 + 1, u0:u1 + 1].astype(np.float32) / 255.0
    ch, cw = crop.shape[:2]
    yy, xx = np.mgrid[0:ch, 0:cw]
    x, z = mc_of(u0 + xx + 0.5, v0 + yy + 0.5)
    mx = np.floor(x).astype(np.int32)
    mz = np.floor(z).astype(np.int32)
    mask = _mask_columns(mx, mz, area)
    if not mask.any():
        return None
    return AerialFrame(crop, mask, mx, mz, label, float(ppb), False)


def _missing(label: str) -> AerialFrame:
    """A car park no aerial covers: the report says the picture is too soft to read.

    @param label: car-park bbox
    @returns: a frame that marks nothing
    """
    return AerialFrame(
        np.zeros((1, 1, 3), np.float32), np.zeros((1, 1), dtype=bool),
        np.zeros((1, 1), np.int32), np.zeros((1, 1), np.int32), label, missing=True)


def _registered_frame(src, cols: set, label: str) -> AerialFrame | None:
    """Crop one registered aerial over ``cols``.

    @param src: a ``Registered`` aerial
    @param cols: columns it covers inside one car park
    @param label: car-park bbox
    @returns: the crop, or None when it misses the image
    """
    from aerial_coverage import load_rgb

    image = load_rgb(src.image_path)
    pixels = src.pixels(list(cols))
    u0, v0, u1, v1 = _crop_box(pixels)
    matrix = np.linalg.inv(np.vstack([src.A, [0, 0, 1]]))

    def mc_of(u, v, _m=matrix):
        return (_m[0, 0] * u + _m[0, 1] * v + _m[0, 2],
                _m[1, 0] * u + _m[1, 1] * v + _m[1, 2])

    return _frame(image, u0, v0, u1, v1, cols, mc_of, src.px_per_block, label)


def _mosaic_frame(mosaic, cols: set, label: str) -> AerialFrame | None:
    """Crop one mosaic over ``cols``.

    @param mosaic: the mosaic ``samples`` chose for these columns
    @param cols: columns inside one car park
    @param label: car-park bbox
    @returns: the crop, or None when it misses the image
    """
    from aerial_coverage import load_rgb
    from world_frame import wgs84_batch_to_mc

    image = load_rgb(mosaic.path)
    pixels = mosaic.pixels(list(cols))
    u0, v0, u1, v1 = _crop_box(pixels)
    origin = next(iter(cols))
    here = pixels[origin]
    nxt = mosaic.pixels([(origin[0] + 1, origin[1])])[(origin[0] + 1, origin[1])]
    ppb = float(np.hypot(nxt[0] - here[0], nxt[1] - here[1])) or _PX_PER_BLOCK_Z19

    def mc_of(u, v, _m=mosaic):
        n = 2.0 ** _m.zoom
        gx = _m.tile_x * 256 + u / _m.scale
        gy = _m.tile_y * 256 + v / _m.scale
        lon = (gx / 256.0) / n * 360.0 - 180.0
        lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (gy / 256.0) / n))))
        flat = list(zip(np.asarray(lat).ravel().tolist(), np.asarray(lon).ravel().tolist()))
        mcs = wgs84_batch_to_mc(flat)
        shape = np.asarray(u).shape
        return (np.array([p[0] for p in mcs], np.float64).reshape(shape),
                np.array([p[1] for p in mcs], np.float64).reshape(shape))

    return _frame(image, u0, v0, u1, v1, cols, mc_of, ppb, label)


def frames_from_aerial(areas: list[set]) -> list[AerialFrame]:
    """Crops of each car park from the aerial the ground pass samples.

    A registered user aerial wins where it covers a column (same priority as
    :func:`ground_sources.aerial_rgb`); the rest comes from the finest local
    mosaic. The crop is the raw image, not the per-column average the surface
    classifier uses: a one-pixel line does not survive that average.

    @param areas: one column set per surface car park
    @returns: one frame per park per image, plus a missing frame for a park
        no aerial covers
    """
    from aerial_coverage import load_rgb, mosaics, samples
    from aerial_registered import REG_EDGE_BLOCKS, registered

    claimed: dict[tuple[int, int], object] = {}
    for src in registered():
        try:
            image = load_rgb(src.image_path)
        except OSError:
            continue
        half = max(2, int(round(0.5 * src.px_per_block)))
        edge = REG_EDGE_BLOCKS * src.px_per_block
        img_h, img_w = image.shape[:2]
        todo = [q for area in areas for q in area if q not in claimed]
        if not todo:
            break
        for col, (u, v) in src.pixels(todo).items():
            if edge + half <= u < img_w - edge - half and edge + half <= v < img_h - edge - half:
                claimed[col] = src
    reg_by_id = {id(src): src for src in registered()}
    frames: list[AerialFrame] = []
    for area in areas:
        if not area:
            continue
        label = _park_label(area)
        reg_groups: dict[int, set] = {}
        for col in area:
            src = claimed.get(col)
            if src is not None:
                reg_groups.setdefault(id(src), set()).add(col)
        for src_id, cols in reg_groups.items():
            frame = _registered_frame(reg_by_id[src_id], cols, label)
            if frame is not None:
                frames.append(frame)
        mosaic_cols = [c for c in area if c not in claimed]
        picked = samples(mosaic_cols, mosaics()) if mosaic_cols else {}
        by_mosaic: dict[object, set] = {}
        mosaic_of: dict[object, object] = {}
        for col, sample in picked.items():
            by_mosaic.setdefault(sample[0].path, set()).add(col)
            mosaic_of[sample[0].path] = sample[0]
        for path, cols in by_mosaic.items():
            frame = _mosaic_frame(mosaic_of[path], cols, label)
            if frame is not None:
                frames.append(frame)
        if not reg_groups and not by_mosaic:
            frames.append(_missing(label))
    return frames


def mark_car_park_bays(kind: dict, vmap) -> BayLineResult:
    """Turn aerial bay-line columns into ``mark`` in the ground pass's ``kind`` map.

    Uses ``vmap.bay_frames`` when a test supplies crops. Otherwise reads the
    aerial the ground pass uses. Evidence with no ``parking_areas`` marks nothing.
    A column is marked only when ``kind`` is ``tarmac`` and ``vmap.pri`` is
    still ``PRI['parking']``.

    @param kind: column -> surface kind, edited in place
    @param vmap: vector-map evidence (``parking_areas``, and optional ``bay_frames``)
    @returns: how many columns were marked, and which parks were too soft to read
    """
    areas = [a for a in getattr(vmap, 'parking_areas', ()) if a]
    if not areas:
        return BayLineResult(0, ())
    frames = getattr(vmap, 'bay_frames', None)
    if frames is None:
        frames = frames_from_aerial(areas)
    area_cols = set().union(*areas) if areas else set()
    pri = getattr(vmap, 'pri', {})
    soft_only: dict[str, bool] = {}
    marked: set[tuple[int, int]] = set()
    for frame in frames:
        if frame.missing or not frame.mask.any():
            soft_only.setdefault(frame.label, True)
            continue
        keep, too_soft = thin_pale_mask(frame.rgb, frame.mask, px_per_block=frame.px_per_block)
        if too_soft:
            soft_only.setdefault(frame.label, True)
            continue
        soft_only[frame.label] = False
        for col in columns_of(keep, frame.mx, frame.mz):
            # The aisle, the road and the path keep a higher priority than the
            # car park, so a pale streak there is not a bay line.
            if kind.get(col) == 'tarmac' and col in area_cols and pri.get(col) == PRI['parking']:
                marked.add(col)
    for col in marked:
        kind[col] = 'mark'
    soft = tuple(label for label, only in soft_only.items() if only)
    return BayLineResult(len(marked), soft)

"""Scaffold world-local Geofabrik providers against published clipping geometry.

Metadata: https://download.geofabrik.de/technical.html (index-v1.json).
Scaffolding never downloads the regional PBF. Extractors use the shared GDAL
OSM schema and keep clips in the active world's reference/vector_map tree.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from script_env import TOOL_ROOT
from osm_cell_extract import cell_extract_layers_csv
from osm_extraction import LAYER_PRESETS, osm_schema, out_filename, run_ogr2ogr
from vector_map_clips import default_pbf_path, place_clip_dir

INDEX_URL = 'https://download.geofabrik.de/index-v1.json'
OSM_CONFIG = TOOL_ROOT / 'config' / 'osmconf.ini'


def parse_bbox(text: str) -> tuple[float, float, float, float]:
    """Parse a finite west,south,east,north WGS84 rectangle (no dateline wrap)."""
    values = tuple(float(x) for x in text.split(','))
    if len(values) != 4 or not all(math.isfinite(x) for x in values):
        raise ValueError('bbox needs four finite numbers: west,south,east,north')
    west, south, east, north = values
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError('bbox must be an ordered WGS84 rectangle')
    return values


def _inside_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        ax, ay = a[:2]
        bx, by = b[:2]
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
    return inside


def _segment_in_box(a: list, b: list, bbox: tuple) -> bool:
    """Whether a segment intersects the closed bbox (Liang–Barsky clipping)."""
    west, south, east, north = bbox
    dx, dy = b[0] - a[0], b[1] - a[1]
    low, high = 0.0, 1.0
    for p, q in ((-dx, a[0] - west), (dx, east - a[0]),
                 (-dy, a[1] - south), (dy, north - a[1])):
        if p == 0:
            if q < 0:
                return False
        elif p < 0:
            low = max(low, q / p)
        else:
            high = min(high, q / p)
        if low > high:
            return False
    return True


def covers_bbox(geometry: dict, bbox: tuple) -> bool:
    """Require the whole rectangle strictly inside one clipping polygon.

    Checking corners alone misses coastal notches and holes. Reject any ring
    boundary touching/crossing the box too. This is conservative at boundaries
    and where separate components share an edge: choose a larger extract.
    """
    kind = geometry.get('type')
    if kind not in ('Polygon', 'MultiPolygon'):
        raise ValueError('extract index needs Polygon or MultiPolygon geometry')
    polygons = [geometry['coordinates']] if kind == 'Polygon' else geometry['coordinates']
    west, south, east, north = bbox
    for rings in polygons:
        if not rings or not _inside_ring(west, south, rings[0]):
            continue
        if any(_inside_ring(west, south, hole) for hole in rings[1:]):
            continue
        if any(_segment_in_box(a, b, bbox) for ring in rings
               for a, b in zip(ring, ring[1:] + ring[:1])):
            continue
        return True
    return False


def scaffold(world_root: Path, extract: str, bbox: tuple, index: dict) -> Path:
    """Create a regional provider and catalogue row after checking coverage.

    Refuse existing providers and any existing vector_map row; never overwrite
    a world's provider choice. The caller supplies the Geofabrik index so tests
    and offline setups need no network.
    """
    matches = [f for f in index.get('features', [])
               if f.get('properties', {}).get('id') == extract]
    if len(matches) != 1:
        raise ValueError(f'Geofabrik extract id must match one index entry: {extract!r}')
    feature = matches[0]
    if not covers_bbox(feature.get('geometry') or {}, bbox):
        raise ValueError('extract does not fully cover bbox; choose a larger regional extract')
    url = feature['properties'].get('urls', {}).get('pbf', '')
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.netloc != 'download.geofabrik.de':
        raise ValueError('index PBF URL must use https://download.geofabrik.de/')
    filename = Path(parsed.path).name
    if not filename.endswith('.pbf'):
        raise ValueError('index has no PBF filename')
    if not re.fullmatch(r'[a-z0-9][a-z0-9/-]*', extract) or '..' in extract:
        raise ValueError('invalid extract id')
    provider_id = 'geofabrik-' + extract.replace('/', '-')
    package = world_root / 'providers' / provider_id
    catalogue = world_root / 'config' / 'local-providers.json'
    data = json.loads(catalogue.read_text()) if catalogue.exists() else {'providers': [], 'aliases': {}}
    if any('vector_map' in p.get('types', []) or p.get('id') == provider_id
           for p in data.get('providers', [])):
        raise ValueError('world already has a vector_map provider; edit it explicitly')
    if package.exists():
        raise FileExistsError(f'provider package already exists: {package}')
    metadata = {'extract': extract, 'pbf_url': url, 'pbf_filename': filename,
                'coverage_bbox_wgs84': list(bbox), 'index_url': INDEX_URL}
    wrapper = '''"""World-local Geofabrik extractor; shared implementation in the tool repo."""
from pathlib import Path
from geofabrik_provider import LAYER_PRESETS, extractor_main


def main(argv=None):
    """Extract clips using this provider's regional download configuration."""
    return extractor_main(Path(__file__).with_name('provider.json'), argv)
'''
    package.mkdir(parents=True)
    (package / 'provider.json').write_text(json.dumps(metadata, indent=2) + '\n')
    (package / 'osm_extract.py').write_text(wrapper)
    # A local copy makes the schema discoverable alongside provider metadata.
    shutil.copyfile(OSM_CONFIG, package / 'osmconf.ini')
    data.setdefault('providers', []).append({
        'id': provider_id, 'package': f'providers/{provider_id}', 'types': ['vector_map'],
        'scope': {'level': 'regional'}, 'keep': True, 'licence': 'OpenStreetMap ODbL 1.0',
        'tags': ['osm'], 'kind': 'map', 'checklist': True,
        'how': 'python3 shims/osm_extract.py --world <name> --at X,Z --cell --place PLACE',
    })
    catalogue.parent.mkdir(parents=True, exist_ok=True)
    catalogue.write_text(json.dumps(data, indent=2) + '\n')
    return package


def _query_bbox_mc(args: argparse.Namespace) -> tuple[tuple[int, int, int, int], tuple[float, float, float, float]]:
    """Resolve Minecraft query bounds for filenames and the WGS84 spat for GDAL.

    ``clip_coverage`` / ``_query_bbox`` only trust extents encoded in the clip
    name (``…-cell-…``, ``…-rNm-at-…``, ``…-bbox-…``). The place folder stays a
    human place slug; the query box must land in the filename.

    @param args: parsed extractor CLI
    @returns: (inclusive MC bbox for naming, WGS84 spat west,south,east,north)
    @raises ValueError: when spatial args are malformed
    """
    import cellmath
    import world_frame as wf

    if args.bbox_wgs84:
        spat = parse_bbox(args.bbox_wgs84)
        west, south, east, north = spat
        corners = [
            wf.wgs84_to_mc(south, west),
            wf.wgs84_to_mc(south, east),
            wf.wgs84_to_mc(north, west),
            wf.wgs84_to_mc(north, east),
        ]
        xs = [c[0] for c in corners]
        zs = [c[1] for c in corners]
        return (min(xs), min(zs), max(xs), max(zs)), spat
    if args.at:
        x, z = map(float, args.at.split(','))
        if args.cell:
            mc_bbox = cellmath.bounds(cellmath.cell_of(math.floor(x), math.floor(z)))
        else:
            if not math.isfinite(args.radius_m) or args.radius_m <= 0:
                raise ValueError('radius must be finite and positive')
            radius = int(round(args.radius_m))
            cx, cz = int(round(x)), int(round(z))
            mc_bbox = (cx - radius, cz - radius, cx + radius, cz + radius)
    else:
        x0, z0, x1, z1 = map(float, args.bbox_mc.split(','))
        if not all(math.isfinite(v) for v in (x0, z0, x1, z1)) or x0 >= x1 or z0 >= z1:
            raise ValueError('bbox-mc needs ordered finite x0,z0,x1,z1')
        mc_bbox = (int(round(x0)), int(round(z0)), int(round(x1)), int(round(z1)))
        # Spat follows the caller's floats so rounding for the filename does not
        # shrink or grow the GDAL query relative to the previous behaviour.
        corners = [wf.mc_to_wgs84(x, z) for x in (x0, x1) for z in (z0, z1)]
        spat = (min(lon for lat, lon in corners), min(lat for lat, lon in corners),
                max(lon for lat, lon in corners), max(lat for lat, lon in corners))
        return mc_bbox, spat
    x0, z0, x1, z1 = mc_bbox
    corners = [wf.mc_to_wgs84(x, z) for x in (x0, x1) for z in (z0, z1)]
    spat = (min(lon for lat, lon in corners), min(lat for lat, lon in corners),
            max(lon for lat, lon in corners), max(lat for lat, lon in corners))
    return mc_bbox, spat


def extractor_main(config_path: Path, argv: list[str] | None = None) -> int:
    """Run GDAL extraction from a world-local PBF; never download implicitly."""
    ap = argparse.ArgumentParser(description='Extract active-world OSM clips (requires ogr2ogr).')
    ap.add_argument('--place', required=True)
    area = ap.add_mutually_exclusive_group(required=True)
    area.add_argument('--at', help='Minecraft x,z (use with --cell or --radius-m)')
    area.add_argument('--bbox-mc', help='Minecraft x0,z0,x1,z1')
    area.add_argument('--bbox-wgs84', help='west,south,east,north')
    ap.add_argument('--cell', action='store_true')
    ap.add_argument('--radius-m', type=float, default=128)
    ap.add_argument('--layers', default=cell_extract_layers_csv())
    ap.add_argument('--gdal-layer', choices=['points', 'lines', 'multipolygons', 'multilinestrings', 'other_relations'])
    ap.add_argument('--where', default='1=1')
    ap.add_argument('--pbf', type=Path)
    args = ap.parse_args(argv)
    try:
        config = json.loads(config_path.read_text())
        pbf = args.pbf or default_pbf_path(config['pbf_filename'])
        if not pbf.is_file():
            raise FileNotFoundError(f'download {config["pbf_url"]} to {pbf} first (owner approval required)')
        if shutil.which('ogr2ogr') is None:
            raise RuntimeError('ogr2ogr missing: install GDAL with its OSM driver (see docs/minecraft-instance-setup.md)')
        mc_bbox, bbox = _query_bbox_mc(args)
        cell_extract = bool(args.at and args.cell)
        radius_for_name = None if (cell_extract or args.bbox_mc or args.bbox_wgs84) else float(args.radius_m)
        jobs = [(args.gdal_layer, args.gdal_layer, args.where, [])] if args.gdal_layer else [
            (name if len(LAYER_PRESETS[name]) == 1 else f'{name}-{i}', *job)
            for name in args.layers.split(',') for i, job in enumerate(LAYER_PRESETS[name])]
        output = place_clip_dir(args.place)
        output.mkdir(parents=True, exist_ok=True)
        schema = osm_schema(config_path.with_name('osmconf.ini'))
        for name, layer, where, _tags in jobs:
            filename = out_filename(
                [name], mc_bbox, cell=cell_extract, radius_m=radius_for_name,
            )
            # Temporary output prevents failed GDAL jobs replacing good evidence.
            with tempfile.TemporaryDirectory(dir=output) as tmp:
                target = Path(tmp) / filename
                run_ogr2ogr(pbf, target, layer, where, bbox, schema=schema, clip=True)
                target.replace(output / filename)
        print(output)
        return 0
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        ap.error(str(exc))

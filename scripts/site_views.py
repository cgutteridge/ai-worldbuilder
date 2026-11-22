#!/usr/bin/env python3
"""Capture named views from the active world's config, then restore game mode.

Views live in the world's config/views.json. This tool does not keep a camera
table of its own. Each name has an eye position and a look-at point:

    {"square": {"eye": [0, 80, 0], "look": [0, 64, 0]}}

With no names on the command line, every configured view is captured. Pictures
go under the world at verification/site/views/. A missing views file means
there is nothing to capture.

  python3 scripts/site_views.py --world <name> [--hires | --midres] [--prefix TEXT] [name ...]

Default capture is low-res (downscale 4). --hires is full size. --midres is
downscale 2. Flags that this command does not define are refused.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from pathlib import Path

_TOOL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL / 'scripts'))
import script_env  # noqa: E402

script_env.ensure_import_paths()

from contact_sheet import make_sheet  # noqa: E402

RES = {'--hires': 1, '--midres': 2}
LOWRES = 4
Point = list[int | float]
View = tuple[Point, Point]


def pop_downscale(args):
    """Remove resolution flags from args and return the view_capture downscale.

    @param args: argv list, edited in place
    @returns: downscale factor (1 = full size)
    """
    found = [a for a in args if a in RES]
    for a in found:
        args.remove(a)
    return min((RES[a] for a in found), default=LOWRES)


def downscale_for(*, hires: bool = False, midres: bool = False) -> int:
    """Return the view_capture downscale for the chosen resolution.

    @param hires: full size when True
    @param midres: downscale 2 when True
    @returns: downscale factor (1 = full size). Low-res is 4 when neither flag is set.
    """
    if hires:
        return RES['--hires']
    if midres:
        return RES['--midres']
    return LOWRES


def views_config_path(root: Path | None = None) -> Path:
    """Path of the active world's named-view catalogue.

    @param root: world checkout (default: the active world)
    @returns: ``config/views.json`` under that checkout
    """
    base = Path(root) if root is not None else Path(script_env.ROOT)
    return base / 'config' / 'views.json'


def views_output_dir(root: Path | None = None) -> Path:
    """Directory for captured view pictures.

    @param root: world checkout (default: the active world)
    @returns: ``verification/site/views`` under that checkout
    """
    base = Path(root) if root is not None else Path(script_env.ROOT)
    return base / 'verification' / 'site' / 'views'


def _check_view_name(name: object) -> str:
    """Refuse a view name that is not a plain file stem.

    @param name: JSON object key
    @returns: the name when it is safe to use in a file name
    @raises ValueError: when the name is empty, a flag, or a path
    """
    if (
        not isinstance(name, str) or not name or name.startswith('-')
        or name in ('.', '..') or '/' in name or '\\' in name
        or name != Path(name).name
    ):
        raise ValueError(f'view name must be a plain file stem, got {name!r}')
    return name


def check_prefix(prefix: str) -> str:
    """Refuse a prefix that could be a flag or a path.

    @param prefix: text prepended to each output file name
    @returns: the same prefix when it is safe
    @raises SystemExit: when the prefix is a flag or contains a path separator
    """
    if prefix and (
        prefix.startswith('-') or '/' in prefix or '\\' in prefix or prefix in ('.', '..')
    ):
        raise SystemExit(f'prefix must be a plain file-name fragment, got {prefix!r}')
    return prefix


def _point(value: object, where: str) -> Point:
    """Read one eye or look-at point.

    @param value: JSON value
    @param where: label used in the error
    @returns: three numbers
    @raises ValueError: when the value is not three numbers
    """
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f'{where} must be a list of three numbers')
    point: Point = []
    for number in value:
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ValueError(f'{where} must be a list of three numbers')
        point.append(number)
    return point


def load_views(path: Path | None = None) -> dict[str, View]:
    """Load name to (eye, look) from the world's config/views.json.

    A missing file means the world has no views. A file that exists must be a
    JSON object of named views.

    @param path: catalogue file (default: the active world's config/views.json)
    @returns: views in file order
    @raises ValueError: when the file is not a catalogue of named views
    """
    catalogue = views_config_path() if path is None else Path(path)
    if not catalogue.exists():
        return {}
    if not catalogue.is_file():
        raise ValueError(f'{catalogue} is not a file')
    try:
        data = json.loads(catalogue.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise ValueError(f'{catalogue.name} is not valid JSON: {exc}') from exc
    if not isinstance(data, dict):
        raise ValueError(f'{catalogue.name} must be a JSON object of named views')
    views: dict[str, View] = {}
    for name, spec in data.items():
        stem = _check_view_name(name)
        if not isinstance(spec, dict):
            raise ValueError(f'{stem} must be an object with eye and look')
        views[stem] = (
            _point(spec.get('eye'), f'{stem}.eye'),
            _point(spec.get('look'), f'{stem}.look'),
        )
    return views


def selected_names(views: dict[str, View], names: list[str]) -> list[str]:
    """Choose which configured views to capture.

    @param views: catalogue from :func:`load_views`
    @param names: requested names; empty means every view, in file order
    @returns: names to capture
    @raises SystemExit: when nothing is configured, or a name is not in the catalogue
    """
    chosen = list(names) if names else list(views)
    if not chosen:
        raise SystemExit(
            'No views in config/views.json — add a name with eye and look, then run again'
        )
    unknown = [name for name in chosen if name not in views]
    if unknown:
        raise SystemExit('Unknown view(s): ' + ', '.join(unknown))
    return chosen


def shot_path(out: Path, prefix: str, name: str) -> Path:
    """Picture path for one view, kept inside ``out``.

    @param out: views directory under the world
    @param prefix: plain file-name fragment (already checked)
    @param name: view name
    @returns: ``<out>/<prefix><name>.png``
    @raises SystemExit: when the path would leave ``out``
    """
    path = out / f'{prefix}{name}.png'
    if path.resolve().parent != out.resolve():
        raise SystemExit(f'refusing to write outside the views directory: {path}')
    return path


def plan_shots(views: dict[str, View], names: list[str], prefix: str, out: Path):
    """Pair each chosen view with the picture path it will write.

    @param views: catalogue from :func:`load_views`
    @param names: requested names; empty means every view
    @param prefix: text prepended to each file name
    @param out: views directory under the world
    @returns: ``(name, eye, look, path)`` rows
    @raises SystemExit: when the prefix or a name is not safe to capture
    """
    prefix = check_prefix(prefix)
    planned = []
    for name in selected_names(views, names):
        eye, look = views[name]
        planned.append((name, eye, look, shot_path(out, prefix, name)))
    return planned


def capture(planned, downscale: int, out: Path, prefix: str) -> Path | None:
    """Move through the planned viewpoints, write PNGs, and restore game mode.

    @param planned: rows from :func:`plan_shots`
    @param downscale: view_capture downscale
    @param out: views directory under the world
    @param prefix: file-name fragment used for the contact sheet
    @returns: contact-sheet path, or None when no sheet was written
    """
    from mc_paced import UUID, Paced
    from mc_player import move_player

    shots = []
    w, v = Paced(), Paced(8766)
    info = w.text('player_get_info', {'uuid': UUID})
    mode = re.search(r'gameMode: (\w+)', info).group(1)
    move_player(
        why=f'Views: moving you through {len(planned)} viewpoints in spectator for screenshots; your game mode returns after.',
        gamemode='spectator',
        m=w,
    )
    try:
        for name, pos, look, path in planned:
            move_player(
                why=f'Views: capturing {name}',
                position=pos,
                facing=look,
                m=w,
            )
            time.sleep(4)
            result = v.call('view_capture', {'downscale': downscale, 'close_screen': True})
            for block in result['content']:
                if block['type'] == 'image':
                    path.write_bytes(base64.b64decode(block['data']))
                    shots.append(path)
                    print(path, flush=True)
    finally:
        move_player(
            why='Views finished; game mode restored (you stay at the last viewpoint).',
            gamemode=mode,
            m=w,
        )
    sheet = make_sheet(shots, out / f'{prefix or "views-"}contact.png')
    if sheet:
        print(f'CONTACT SHEET: {sheet}  (read this one image; open single views only for detail)')
    return sheet


def build_parser() -> argparse.ArgumentParser:
    """Argument parser for the views command.

    @returns: parser that accepts ``--world``, resolution, prefix and view names
    """
    parser = script_env.make_parser(__doc__)
    script_env.add_world_arg(parser)
    resolution = parser.add_mutually_exclusive_group()
    resolution.add_argument('--hires', action='store_true', help='full-size capture (downscale 1)')
    resolution.add_argument('--midres', action='store_true', help='mid capture (downscale 2)')
    parser.add_argument('--prefix', default='', help='plain text prepended to each output file name')
    parser.add_argument('names', nargs='*', help='view names from config/views.json (default: all)')
    return parser


_OPTIONS = {'-h', '--help', '--world', '--hires', '--midres', '--prefix'}
_OPTIONS_WITH_VALUE = {'--world', '--prefix'}


def reject_unknown_flags(parser: argparse.ArgumentParser, argv: list[str]) -> None:
    """Refuse flags this command does not define.

    Argparse reports a missing ``--world`` before an unknown flag, so a typo
    such as ``--helpb35`` would otherwise look like a missing world. Unknown
    flags are reported first. ``--help`` still prints help.

    @param parser: the views argument parser
    @param argv: argument vector without the program name
    @returns: None
    @raises SystemExit: when a flag is not defined
    """
    unexpected = []
    skip_value = False
    for arg in argv:
        if skip_value:
            skip_value = False
            continue
        if arg == '--':
            break
        if not arg.startswith('-'):
            continue
        name = arg.split('=', 1)[0]
        if name not in _OPTIONS:
            unexpected.append(arg)
            continue
        if name in _OPTIONS_WITH_VALUE and '=' not in arg:
            skip_value = True
    if unexpected:
        parser.error('unrecognized arguments: ' + ' '.join(unexpected))


def main(argv: list[str] | None = None) -> int:
    """Capture the named views, or every view in config/views.json.

    @param argv: argument vector without the program name (default: ``sys.argv[1:]``)
    @returns: 0 when capture finishes
    @raises SystemExit: unknown flags, unknown views, or a views file that does not parse
    """
    parser = build_parser()
    args_list = list(sys.argv[1:] if argv is None else argv)
    reject_unknown_flags(parser, args_list)
    args = parser.parse_args(args_list)
    script_env.activate_world_from_args(args)
    try:
        views = load_views()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    out = views_output_dir()
    planned = plan_shots(views, list(args.names), args.prefix, out)
    out.mkdir(parents=True, exist_ok=True)
    capture(planned, downscale_for(hires=args.hires, midres=args.midres), out, args.prefix)
    return 0


if __name__ == '__main__':
    sys.exit(main())

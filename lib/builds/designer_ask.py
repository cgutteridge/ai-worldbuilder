"""One small designer-model decision on an image, with its measured token use (#459).

The read step asks the designer model (``config/build-roles.json``, ``designer``) one
question per building: a rotated aerial crop plus a short table, answered in JSON. This
module is the seam: it runs the call through the Claude Code CLI in print mode with no
tools, no MCP servers and a short system prompt, so the cost is the image, the table and
the answer. The CLI reports exact usage, which is returned with the answer.

Tests pass a ``runner`` double; nothing here reads a world.
"""
from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
SYSTEM = ('You read overhead aerial images of UK buildings for a Minecraft build tool. '
          'Answer with the JSON object asked for and nothing else.')
TIMEOUT_S = 300


def role_model(role_id: str) -> tuple[str, str | None]:
    """Split a build-roles id such as ``claude-opus-5-5-low`` into model and effort.

    @param role_id: designer or builder id from ``build_roles``
    @returns: ``(model, effort)``; effort None when the id names none
    """
    head, _sep, tail = role_id.strip().rpartition('-')
    if head and tail in EFFORTS:
        return head, tail
    return role_id.strip(), None


def command(model: str, effort: str | None) -> list[str]:
    """The CLI command for one image question (print mode, stream JSON in and out, no tools)."""
    cmd = ['claude', '-p', '--model', model]
    if effort:
        cmd += ['--effort', effort]
    return cmd + ['--tools', '', '--strict-mcp-config', '--no-session-persistence',
                  '--system-prompt', SYSTEM, '--input-format', 'stream-json',
                  '--output-format', 'stream-json', '--verbose']


def message(image_png: bytes, prompt: str) -> str:
    """One stream-JSON user message carrying the PNG and the prompt."""
    content = [
        {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
                                     'data': base64.b64encode(image_png).decode('ascii')}},
        {'type': 'text', 'text': prompt},
    ]
    return json.dumps({'type': 'user', 'message': {'role': 'user', 'content': content}}) + '\n'


def _run(cmd: list[str], stdin: str) -> str:
    done = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    if done.returncode != 0:
        raise RuntimeError(f'designer call failed ({done.returncode}): {done.stderr.strip()[-400:]}')
    return done.stdout


def ask(image_path: Path, prompt: str, *, role_id: str, runner=None) -> dict:
    """Ask the designer model one question about an image.

    @param image_path: PNG to send
    @param prompt: the question (the answer format is part of it)
    @param role_id: build-roles designer id, e.g. ``claude-opus-5-5-low``
    @param runner: ``(cmd, stdin) -> stdout`` (tests); default runs the CLI
    @returns: ``{model, effort, text, usage, cost_usd, measured}``; ``usage`` holds the CLI's
        ``input_tokens``, ``cache_creation_input_tokens``, ``cache_read_input_tokens`` and ``output_tokens``
    @raises RuntimeError: when the call fails or returns no result
    """
    model, effort = role_model(role_id)
    stdout = (runner or _run)(command(model, effort), message(Path(image_path).read_bytes(), prompt))
    result = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get('type') == 'result':
            result = event
    if result is None or result.get('is_error'):
        raise RuntimeError('designer call returned no result')
    usage = result.get('usage') or {}
    keys = ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens')
    usage = {key: int(usage.get(key) or 0) for key in keys}
    return {
        'model': model,
        'effort': effort,
        'text': str(result.get('result') or ''),
        'usage': usage,
        'total_tokens': sum(usage.values()),
        'cost_usd': result.get('total_cost_usd'),
        'measured': 'CLI result usage (claude -p, stream-json, no tools)',
    }

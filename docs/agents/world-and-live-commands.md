# World frame, providers and live-world commands

Read this before adding a provider, converting coordinates, running a command that touches the live world, or starting a slow command.

- Providers: on add, ask **local vs system** (below national → local). Skills: `evaluate-data-source`, `add-data-source`.
- World frame: active world `config/world-frame.json` via `scripts/world_frame.py` (canonical CRS / MC / cell / XYZ). Preferred XYZ CLI: `to-wm` / `from-wm` / `batch-wm` — logical 256-px `east_px` (right) and `south_px` (down) from the tile NW corner; lat/lon needs no world; MC needs `--frame` or the active world. Do not reimplement slippy-map maths elsewhere. Tile-image fetch / evidence sheets (`google_aerial sheet`, etc.) stay separate.
- Aerial downloads: `google_aerial sheet` and both `google_window` modes require `--download-authorized`, including their Python download APIs (`download_authorized=True`). First run with `--dry-run` to state the source, destination filename, tile count and uncompressed RGB size estimate (compressed transfer and PNG sizes vary). Obtain the owner's authorization in chat before rerunning with `--download-authorized`. For example: `python3 shims/google_aerial.py --world <world> sheet --cell <tx,tz> --dry-run`. Dry runs and `google_aerial url` make no network requests; `prepare` never authorizes or fetches imagery implicitly.
- Live-world and slow commands: follow [locked execution and background runs](../../rules/slow-commands.md#execution). It defines wrapper ownership, failure handling and completion checks.

## Sound cues

Optional local cues are for the user-facing parent agent only. Skip them when
`~/Projects/agent-soundboard/scripts/play.py` is absent; missing audio never
blocks work or changes a build verdict. After roughly 3+ minutes, use one cue
when useful, without waiting for playback:

```sh
python3 ~/Projects/agent-soundboard/scripts/play.py <id>
```

| ID | When |
| --- | --- |
| `done` | Substantive work finished |
| `party-cheer` | A player-standing redo finished successfully |
| `need-you` | Approval, a held lock or another human action is needed |
| `error` | A failure prevents safe continuation |

Check the command result before cueing success. Report the actual change or
remaining problem in chat; an audio cue is not the completion record.

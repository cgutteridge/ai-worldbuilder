# Minecraft apply rate-limit investigation (#389)

2026-11-22. Partial findings; the historical apply delays are not yet explained
by a measured request trace. Do not treat the old timing totals as proof that
parallel calls caused them.

## Installed limiter

Examined the locally installed `minecraft-fabric-mcp-1.1.0+26.2.jar` with
`javap -p -c`, then executed its actual `RateLimiter` class in Java 25. This
experiment made no requests to the running game.

- The limiter is a token bucket, not a fixed per-minute window. Capacity is
  `rateLimitRpm`; one token refills every `60_000_000_000 / rateLimitRpm`
  nanoseconds, up to capacity.
- Each HTTP transport owns a limiter. Without authentication, its key is the
  remote IP address, without the connection's port. Separate clients using the
  same loopback IP therefore share that transport's budget. Separate world and
  view transports do not share the same limiter instance.
- `HttpTransport` rejects before dispatch with HTTP 429 and a literal
  `Retry-After: 60`. The Python `mcp.Client` honours that header, so a rejected
  request costs a 60-second sleep even if a token would refill much sooner.
- Both installed configuration files (`config.json` and `client.json` under
  `config/minecraft_fabric_mcp/`) currently say `rate_limit_rpm: 600`. File
  contents do not establish the running process's loaded value, or the value
  during the original four applies.

## Measurements

The harness instantiated the installed class, exhausted a bucket, attempted
another acquisition with the same and a different IP, then advanced only its
recorded refill timestamp by one interval using reflection. The timestamp
adjustment is an isolated test technique, not a measurement of elapsed real time.

| Configured RPM | Immediate accepted requests | Next, same IP | Different IP | Refill interval | After one simulated interval |
| --- | ---: | --- | --- | ---: | --- |
| 60 | 60 | rejected | accepted | 1,000 ms | accepted |
| 600 | 600 | rejected | accepted | 100 ms | accepted |

The initial unmodified 600-request burst completed in 5.66 ms; request 601 was
rejected. The two refill experiments completed in 225.853 ms and 1.689 ms,
respectively; those figures include reflection/JVM warm-up and are not apply
timings.

## Writer pacing and remaining work

`mc_paced.Paced` waits 1.1 seconds between calls on one instance, measured after
the preceding call completes. That caps a single caller below 54.55 requests
per minute, which fits either 60 or 600 RPM in isolation. Instances do not
coordinate their schedules. At 60 RPM, concurrent callers can drain the shared
bucket even when each caller respects its own pace; at 600 RPM, a substantially
larger aggregate load or burst is required. The module's assertion that the
mod always allows 60 requests/minute is therefore too specific.

Still required before closing #389:

1. Record request timestamps, the loaded quota and 429 response headers during
   the same real apply, once alone and once with controlled background calls.
2. Distinguish fill calls from planning, backup and readback traffic; record
   which callers use `Paced` and which instantiate the raw client.
3. Use that trace to identify the original failure pattern and either tune
   pacing with evidence or file the concrete fix. Do not lower retry delays
   against the server's header merely because the isolated bucket refills sooner.

No pacing constant or running-game configuration was changed by these experiments.

## Live ping observations

Two bounded ping-only probes ran against the world transport under the site lock.
They changed no blocks. These were **not isolated**: other Codex MCP connections
remained open, and the client trace does not account for their requests.

| Probe | Observation |
| --- | --- |
| First burst | 601 accepted pings; request 602 rejected at 0.305 s with HTTP 429 and `Retry-After: 60` |
| After that rejection | Immediate and 20 ms later: 429; after a further 120 ms: 200 |
| Second burst, after partial refill | 206 accepted pings; request 207 rejected at 0.106 s |
| Second refill measurement | After draining again and waiting 1.055 s, six pings succeeded before another 429 |

The first burst supports a capacity near the configured 600; it does not establish
the initial token count or exclude concurrent consumers. The last observation is
below the ten tokens expected from a quiet 600-RPM bucket over that interval, so
it cannot be used as a clean refill measurement. Open connections alone do not
prove which client was consuming quota. The low-latency acceptance after a 429
does confirm that a rejected request need not remain blocked for a full minute;
the fixed server header, rather than a fixed-minute reset, produces the Python
client's 60-second pause.

## Prepared real-apply comparison

The original building is the existing feature `w146985470` (B35). Its active
landed journal entry is 1718, spanning cells `49,-44`, `49,-43`, `50,-44` and
`50,-43`. Read-only preparation reports full evidence coverage, no missing lidar,
no candidates, no blockers and no preparation actions for all four cells. Their
validation stamps are stale. Under this snapshot, `prepare --apply` would refresh
local validation notes and create no ghosts or frontier. Recheck immediately
before execution; do not bypass a new blocker.

The recorded plan contains 30,698 positions, merged into 2,270 fill boxes in one
`block_fill_batch`, plus **112 individually placed bottom-half slabs**. At the
existing 1.1-second pace, those 113 writer calls account for about 124 seconds,
consistent with the reported 125-second fill without retries. An ordinary
overwrite would skip already-correct positions and would not reproduce that work.
Undo restores a prior with 7,658 differing positions, including all 112 slab
positions, so undo/replay retains the relevant request workload.

The unchanged spec's SHA256 is
`833cd0aa840cadddc2943f7541142352bc8c214b7b6fde3d8af282468351b76b`.
Current-code, read-only disk-world planning produced the same 30,698-position
plan twice (47.099 and 47.258 seconds), but five states differ from journal 1718.
For a controlled comparison, replay the exact recorded plan rather than silently
changing the building. Its normalized SHA256 is
`20a5f2de87ed1a4551cc95577f91d8c63ad4672a8eb2410439716c2a55e0cc35`.

The original structure backup
`southampton_refine:before_w146985470_20261122000928` exists (302,388 bytes).
The journal's prior blob `40903aab175ea7a1deadf4bf9b69b5fa` and plan blob
`c523712b15c9877a1fd4facc4a2318d1` also exist. Preserve them throughout the trial.

A prepared guarded runner has passed its default read-only preflight. Execution
must remain pending until the owner authorizes two B35 undo/replays. The proposed
second run injects one bounded ping burst immediately before the first fill,
stopping at the first 429, then lets the normal client's retry complete. Record
all request timestamps, tool names, statuses and retry headers without arguments
or authorization headers. A quiet baseline additionally requires other MCP
apps/runs to stop, or comprehensive server-side request tracing; the site lock
alone cannot establish it. Allow at least four minutes of writer pacing across
the pair, plus undo, journal, backup, readback and any 60-second retries.

The exact cause of the original four timings remains unproven. The live apply
comparison and a concrete pacing fix remain open; these ping observations do not
close #389.

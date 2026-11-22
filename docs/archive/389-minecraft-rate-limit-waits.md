# Finding: Minecraft apply rate-limit waits (#389)

2026-11-22. Spike complete. Longer scratch notes:
`docs/plans/minecraft-rate-limit.md`.

## Limit

The Fabric MCP HTTP transport uses a **per-transport, per-remote-IP token
bucket**. Loaded configs for world `8765` and view `8766` both set
`rate_limit_rpm: 600` (`/Users/cjg/Projects/minecraft-mcp/config/minecraft_fabric_mcp/`).
Capacity equals that RPM; one token refills every `60s / 600 = 100 ms`.

A rejected request returns **HTTP 429** with a literal **`Retry-After: 60`**.
That header is not the bucket refill time.

## Cause

Waits printed as `Minecraft rate limit: retrying in 60s` come from
`lib/blocks/mcp.py`: the client slept the full `Retry-After` value. The paced
writer is not what empties the bucket.

| Evidence | Result |
| --- | --- |
| Live burst (ping-only) | 620 accepted, then 429 + `Retry-After: 60` in 6.86 s |
| Recovery after drain | 429 at 0 ms and 50 ms; **200 by 100 ms** |
| `Paced` alone, 30 pings @ 1.1 s | **0 × 429** (32.4 s) |
| Same pace + parallel ping burster | **14 × 429** in 20 calls; first at t=2.2 s |
| Open clients during measurement | **7 Codex** TCP sessions on `8765` (plus matching `8766`) sharing the loopback IP budget |
| B35 offline plan | 2,270 boxes → **1** `block_fill_batch` (`FILL_BATCH=8000`) + **112** slab `block_set_state` = **113** paced calls ≈ **123 s** at 1.1 s — matches the historical **125 s / 0 retries** fill |

One `Paced` instance is about **54.5 requests/minute**, well under 600. Concurrent
MCP callers (agent sessions, scans, teleports, captures, a second `Paced`) share
the same IP bucket. When they drain it, the apply’s next call gets 429 and — before
this ticket’s client cap — paid a full minute even though a token was back in
about 100 ms. Historical fills with 1 / 7 / 10 retries are that collision pattern;
retry count tracks other load, not slab count.

## Fix landed with this finding

`mcp.Client` now caps the 429 sleep at **2 s** (still honours a shorter
`Retry-After`). Two seconds is about twenty tokens at 600 RPM — enough to clear
a collision without turning it into a sixty-second stall. The 1.1 s `Paced`
interval is **unchanged**; slowing it further would not address shared-bucket
drain.

## Still optional live apply

A quiet B35 undo/replay with no other MCP apps was not required to identify the
limit or cause. Ping-only probes and the recorded plan arithmetic are enough.
A paired undo/apply with request tracing remains useful only as confirmation
under production fill traffic.

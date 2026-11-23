# Minecraft apply rate-limit investigation (#389)

**Finding (complete):** [`docs/archive/389-minecraft-rate-limit-waits.md`](../archive/389-minecraft-rate-limit-waits.md).

Summary: loaded limiter is a **600 RPM per-IP token bucket**; 429s carry a
literal **`Retry-After: 60`** even though a token returns in about **100 ms**.
A single `Paced` writer at 1.1 s cannot exhaust that budget; **concurrent MCP
clients** on loopback can. `mcp.Client` now uses bounded exponential 429
backoff (0.25 s → grow → ~60 s total across retries; `Retry-After` still caps
each sleep).

## Earlier jar and live notes (kept for detail)

Examined `minecraft-fabric-mcp-1.1.0+26.2.jar` with `javap` and a Java
`RateLimiter` harness (no game traffic): capacity `rateLimitRpm`, refill every
`60_000_000_000 / rateLimitRpm` ns, key = remote IP without port, world and view
transports do not share an instance.

| Configured RPM | Immediate accepted | Next, same IP | Different IP | Refill interval |
| --- | ---: | --- | --- | ---: |
| 60 | 60 | rejected | accepted | 1,000 ms |
| 600 | 600 | rejected | accepted | 100 ms |

Live ping-only (2026-11-22, other Codex MCP sessions still open):

- Burst: 620 accepted then 429 + `Retry-After: 60` (earlier probe: 601 then 429).
- Recovery: 429 at 0–50 ms; 200 by ~100 ms.
- Paced alone 30 @ 1.1 s: 0 × 429.
- Paced + burster 20: 14 × 429.

B35 recorded plan: 30,698 positions → 2,270 boxes + 112 bottom-half slabs → 113
writer calls ≈ 123 s at 1.1 s (matches the historical 125 s / 0-retry fill).
Spec SHA256 `833cd0aa…`; undo/replay fixture notes remain in git history of this
file if a confirmation apply is ever wanted — not required to close the spike.

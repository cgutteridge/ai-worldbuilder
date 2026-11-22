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

No pacing constant or running-game configuration was changed by this experiment.

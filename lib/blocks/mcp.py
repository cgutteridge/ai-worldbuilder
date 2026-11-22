#!/usr/bin/env python3
"""Small local MCP client for diagnostics while the game is open."""
import argparse
import json
import urllib.request
import urllib.error
import time

# Fabric MCP returns Retry-After: 60 for a token bucket that refills far sooner
# (100 ms per token at the loaded 600 RPM; see docs/archive/389-minecraft-rate-limit-waits.md).
# Bounded exponential backoff: start short, grow, and spend at most ~60 s across retries —
# still treating a numeric Retry-After as a per-sleep upper bound.
RATE_LIMIT_RETRY_BASE_S = 0.25
RATE_LIMIT_RETRY_GROWTH = 16
RATE_LIMIT_RETRY_BUDGET_S = 60.0


def rate_limit_retry_delay_s(
    retry_after_header: str | None,
    *,
    attempt: int,
    waited_s: float = 0.0,
) -> float:
    """Seconds to wait after HTTP 429 before the next MCP retry.

    Bounded exponential backoff from :data:`RATE_LIMIT_RETRY_BASE_S`, growing by
    :data:`RATE_LIMIT_RETRY_GROWTH` each attempt. Each sleep is capped by the
    lesser of a numeric ``Retry-After`` and the remaining total budget
    (:data:`RATE_LIMIT_RETRY_BUDGET_S` minus ``waited_s``). Missing or non-numeric
    headers use the budget as that upper bound only — they do not force a long
    first sleep.

    @param retry_after_header: raw ``Retry-After`` response header, or None
    @param attempt: zero-based retry index (0 = first sleep after a 429)
    @param waited_s: seconds already slept on earlier retries for this request
    @returns: sleep seconds (>= 0.05)
    """
    try:
        header_s = (
            float(retry_after_header)
            if retry_after_header is not None
            else RATE_LIMIT_RETRY_BUDGET_S
        )
    except ValueError:
        header_s = RATE_LIMIT_RETRY_BUDGET_S
    remaining = max(0.05, RATE_LIMIT_RETRY_BUDGET_S - waited_s)
    backoff = RATE_LIMIT_RETRY_BASE_S * (RATE_LIMIT_RETRY_GROWTH ** attempt)
    return max(0.05, min(backoff, header_s, remaining))


class Client:
    def __init__(self, port=8765, resume_stateless=False):
        if port not in (8765, 8766):
            raise ValueError("Use the configured Minecraft world or view port")
        self.url = f"http://127.0.0.1:{port}/mcp"
        self.sequence = time.time_ns()
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if resume_stateless:
            self.headers["Mcp-Session-Id"] = "stateless"
            self.headers["MCP-Protocol-Version"] = "2025-06-18"
            return
        result = self.request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "worldbuilder-check", "version": "1.0"},
        })
        self.headers["MCP-Protocol-Version"] = result["protocolVersion"]
        self.request("notifications/initialized", {}, notification=True)

    def request(self, method, params=None, notification=False):
        self.sequence += 1
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            payload["id"] = self.sequence
        req = urllib.request.Request(self.url, json.dumps(payload).encode(), self.headers)
        # A 429 is rejected before dispatch, so retrying it cannot duplicate an edit.
        waited_s = 0.0
        for attempt in range(4):
            try:
                with self.opener.open(req, timeout=60) as response:
                    session = response.headers.get("Mcp-Session-Id")
                    if session:
                        self.headers["Mcp-Session-Id"] = session
                    raw = response.read().decode()
                break
            except urllib.error.HTTPError as error:
                if error.code != 429 or attempt == 3:
                    raise
                delay = rate_limit_retry_delay_s(
                    error.headers.get("Retry-After"),
                    attempt=attempt,
                    waited_s=waited_s,
                )
                print(f"Minecraft rate limit: retrying in {delay}s", file=__import__("sys").stderr, flush=True)
                time.sleep(delay)
                waited_s += delay
                if not notification:
                    self.sequence += 1
                    payload["id"] = self.sequence
                    req = urllib.request.Request(self.url, json.dumps(payload).encode(), self.headers)
        if not raw.strip():
            return None
        data = json.loads(raw)
        if "error" in data:
            raise RuntimeError(data["error"])
        return data.get("result")

    def call(self, name, arguments):
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError(result)
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("method", choices=["tools/list", "tools/call", "ping"])
    parser.add_argument("--port", type=int, default=8765, choices=[8765, 8766])
    parser.add_argument("--tool")
    parser.add_argument("--arguments", default="{}")
    args = parser.parse_args()
    client = Client(args.port)
    if args.method == "tools/call":
        if not args.tool:
            parser.error("tools/call requires --tool")
        result = client.call(args.tool, json.loads(args.arguments))
    else:
        result = client.request(args.method)
    print(json.dumps(result, indent=2))

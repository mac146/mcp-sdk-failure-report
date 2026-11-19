# MCP Python SDK 2.3.0: what happens when a call fails halfway

## 1. What I tried

The [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/) 2.3.0 (released 2 Oct 2026). I picked it because it needs no API key and I could put a fault-injecting proxy between its client and a real server. I followed the README quickstart in an empty directory with a fresh venv, then tried one release-note feature: `max_sse_event_size`, new in [v2.3.0](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0).

Date: 19 Nov 2026. Environment: Windows 11, Python 3.13.1, Node 22.14.0, `uv` not installed at the start. **Time to first working run: 12 minutes** (17:43 to 17:55 IST, `{'result': 3}` from the README client). About 9 of those went on `mcp dev`.

## 2. What worked

`pip install "mcp[cli]"` and the 15-line server just worked. The README HTTP client printed `{'result': 3}`. The [troubleshooting page](https://py.sdk.modelcontextprotocol.io/troubleshooting/) quotes real errors, and the one I checked (the `headers=` `TypeError`) was accurate.

## 3. What broke or confused me

**1. A lost response raises `CancelledError`, not `MCPError` (wasted time; a production bug waiting).** My proxy let the server run `create_order`, then dropped the response. Inside the block, `call_tool` raised `asyncio.CancelledError`, which is not an `Exception`, so `except Exception` and `except MCPError` both miss it. The real cause, `httpx2.RemoteProtocolError: Server disconnected without sending a response.`, only appeared at `async with` exit inside an `ExceptionGroup`. A second call on the same client failed with `MCPError: 'Connection closed'`. I expected `MCPError`. The [troubleshooting page](https://py.sdk.modelcontextprotocol.io/troubleshooting/) says "Caught inside it, the same failure is the plain MCPError", true for server errors, not for this. Upstream already tracks this: [#3560](https://github.com/modelcontextprotocol/python-sdk/issues/3560) and [#3522](https://github.com/modelcontextprotocol/python-sdk/issues/3522), both open when I checked (19 Nov 2026), so I did not file a duplicate.

**2. The quickstart's `uv run mcp dev server.py` needs `uv` and Node; the README and install page list neither (blocker, then ~9 min).** Literal result: `uv: command not found`. The [install page](https://py.sdk.modelcontextprotocol.io/get-started/installation/) offers `pip install "mcp[cli]"` as an equal tab. Running `mcp dev` directly, I found it launches `npx @modelcontextprotocol/inspector uv run --with mcp==2.3.0 mcp run server.py`, so pip users still need `uv`. Only the [Running page](https://py.sdk.modelcontextprotocol.io/run/) mentions Node ("It needs npx on your PATH"), and never `uv`. Even after `pip install uv`, the Inspector never came up in about 3 minutes (npx kept installing a deprecated `inspector@1.0.6`); I don't know if my network was to blame. I skipped the Inspector.

**3. A stalled stream waits 300 s, and the error depends on the reply type (wasted time).** The [transports page](https://py.sdk.modelcontextprotocol.io/client/transports/) says "a 300-second read timeout", and it is accurate: a stalled SSE stream failed after 300.0 s with `MCPError: SSE stream ended without a response`. A stalled plain-JSON body failed after 300.4 s with an empty `httpx2.ReadTimeout: ''` that only surfaced at `async with` exit. Three things it doesn't say: the error differs by reply type; it's per-chunk inactivity (a server sending a `: keepalive` comment every second kept my call waiting past a 3 s read timeout), and the only real deadline is `call_tool(..., read_timeout_seconds=5)`, which gave a clean `MCPError: "Request 'tools/call' timed out"`.

**4. `max_sse_event_size` only matters when the reply is SSE, and the docs don't say when (wasted time).** I followed the [feature section](https://py.sdk.modelcontextprotocol.io/client/transports/) exactly. A 2 MiB result from a plain tool succeeded with the default 1 MiB limit. The same 2 MiB from a tool that calls `ctx.report_progress` failed: `SSE stream failed: Server-sent event exceeded the 1048576 byte limit.` The docs say "JSON responses are unaffected" but not what makes a response JSON.

**5. Python version (minor).** [Installation](https://py.sdk.modelcontextprotocol.io/get-started/installation/) says "Python 3.11+"; PyPI metadata says `>=3.10`.

## 4. How I'd fix the docs

1. Add to troubleshooting, under the ExceptionGroup entry: *"A connection that drops mid-call is the exception to 'catch MCPError inside the block': `call_tool` raises `CancelledError` and the session is closed. Catch `BaseException` there, or give the call a `read_timeout_seconds`, then reconnect. The cause is in the group at the end of `async with`."*
2. README and install page: replace the `mcp dev` line with *"`mcp dev` needs `uv` and Node.js (`npx`). Without them, run `mcp run server.py` and connect a `Client`."*
3. Transports page, after the 300 s sentence: *"This is time without any bytes, not a deadline. Pass `read_timeout_seconds=` to `call_tool` for a total limit."*
4. Same page: *"Replies are SSE only when the tool streams (e.g. `report_progress`); otherwise JSON. The limit applies to SSE only."*
5. Make the Python floor match (3.11 vs 3.10).

## 5. The failure-handling gap

The docs cover server-side errors well ([handling errors](https://py.sdk.modelcontextprotocol.io/servers/handling-errors/)) but say nothing on the transport dying mid-call. I used a 75-line TCP proxy between the real client and a real server (my own tool, CrossTrace, wasn't set up for this run). Evidence (reproduce with `run_cases.py`, in [`repro/`](repro/)):

| Fault | Client sees | Tool ran? | Auto-retry? |
|---|---|---|---|
| Response dropped after tool ran | `CancelledError` inside; `RemoteProtocolError` at exit | **yes, once** | no. My fresh-client retry wrote it **twice** |
| Response cut mid-body | `CancelledError` inside; `RemoteProtocolError` at exit; no partial result | yes | no |
| Request cut inside `arguments` | `CancelledError` inside; `RemoteProtocolError` at exit | **no** (server logs `ClientDisconnect`) | no |
| Stall, defaults | SSE: `MCPError` "SSE stream ended" at 300 s; JSON body: empty `ReadTimeout` at exit | yes | no |
| Stall + `read_timeout_seconds=5` | `MCPError` timed out at 5 s | yes | no |
| Keep-alives only | no failure by 20 s (my test limit) | not checked | not checked |
| >1 MiB SSE event | `MCPError` "SSE stream failed" | n/a (no write) | no |

The good news is undocumented: a request cut inside its arguments never ran the tool. The bad news: a lost response is indistinguishable from a failed call, `call_tool` has no idempotency key, and the only mention of repeat calls is `idempotent_hint`, which the [tools page](https://py.sdk.modelcontextprotocol.io/servers/tools/) calls "hints, not security". Suggested page, **What happens when things fail**: this table, plus a recipe (client-made key as a tool argument, server de-duplicates) and the `read_timeout_seconds` advice.

## 6. One bigger suggestion

The docs sell `Client(mcp)` in-memory testing, which can't fail halfway. Ship a small `FaultyTransport` wrapper (drop response, cut body, stall) and a "Testing failure" page, so users test retry logic before production does.

**Not tested:** stdio faults, 429/HTTP status faults, resumability (`Last-Event-ID`; my server negotiated protocol 2026-07-28), `json_response=True`, the model-API layer (the SDK has none), Python other than 3.13.1, and the Inspector (never loaded).

## Reproduce

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scriptsctivate
pip install "mcp[cli]"==2.3.0
python repro/run_cases.py          # add --full for the 5-minute default-timeout stall
```

`run_cases.py` starts a real `MCPServer`, puts `proxy2.py` (a small TCP fault proxy) in front of it, and prints one line per case. I ran it on Windows 11 with Python 3.13.1 only.

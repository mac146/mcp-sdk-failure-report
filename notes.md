# notes.md — running log (all times IST, 2026-11-19)

Env: Windows 11 Home 10.0.26200, system Python 3.13.1, Node v22.14.0 (npx 11.21.1), venv Python 3.13.1. `uv` NOT preinstalled. No LLM API key set (tool chosen partly because of that).
Tool: MCP Python SDK, PyPI `mcp` 2.3.0 (released 2026-10-02, GitHub tag v2.3.0). Docs: https://py.sdk.modelcontextprotocol.io/
Note: CrossTrace is NOT in this repo (repo = YoraitoWork). Used a local mock instead, as the brief allows.

## Timeline
- 17:43:21 T0 (env check, then read README / releases / get-started page)
- 17:44:16 `python -m venv .venv` in empty dir `qs/` -> ok
- `uv add "mcp[cli]"` (README primary) -> `uv: command not found` (rc 127). Quickstart never says to install uv.
- `pip install "mcp[cli]"` (README "or: pip install") -> ok, 1m24s, installed mcp-2.3.0 + httpx2 2.16.2 etc.
- `uv run mcp dev server.py` (README, literal) -> `uv: command not found`.
- `mcp dev server.py` (without uv) -> prints npm warn "inspector@1.0.6 ... v1 is deprecated. Upgrade to v2", then `ERROR Dev server failed cli.py:284` only when my `timeout` killed it. Process list showed it actually spawns `npx.cmd @modelcontextprotocol/inspector uv run --with mcp==2.3.0 mcp run server.py` => pip-only users still need uv. Nothing listened on 6274/6277 after ~4 min (npx still "installing"; slow network). NOT diagnosed further.
- 17:51:43 `pip install uv` -> uv 0.13.9. `uv run mcp dev server.py` -> still nothing on 6274 after ~3 min, log only "npm warn exec ... inspector@1.0.6 will be installed". Killed. Inspector path = UNRESOLVED (could be network).
- 17:55:09 README client example: `uv run mcp run server.py --transport streamable-http` -> server up on 127.0.0.1:8000; `python client.py` -> `{'result': 3}`  **FIRST SUCCESS 17:55:25 (~12 min from T0; ~11 min from venv)**
- Server log also showed `GET /health 404` from something on 8000 (not diagnosed).
- README says "Python 3.11+"; PyPI metadata says requires_python ">=3.10" (read from pypi json). Docs inconsistency (minor).

## Failure-handling tests (17:56 - 18:08). Harness: repro/ (earlier harness) (real MCPServer on :8001 + TCP fault proxy). Negotiated protocol: 2026-07-28 (Client.protocol_version). Server answers tools/call with a plain JSON body (Content-Length), not SSE, unless the tool streams (progress).
- Transient "disk full" (0 MB free on C:) hit once at ~18:05 and cleared to 2.8 GB on its own; not caused by my files (workdir 111 MB; no npx cache dirs from today). Nothing deleted.
- A. LOST RESPONSE (proxy lets server run the tool, drops the response, closes socket):
  - server executed create_order exactly once (orders.log 1 line) -> SDK did NOT auto-retry.
  - Inside `except` around call_tool (no wait_for): `asyncio.exceptions.CancelledError` (isinstance Exception == False). `except Exception` and `except MCPError` do not catch it.
  - Real cause only at `async with` exit: ExceptionGroup -> httpx2.RemoteProtocolError 'Server disconnected without sending a response.'
  - Same Client reused afterwards: `MCPError: 'Connection closed'`.
  - Fresh Client, same args: OK -> orders.log 2 lines = DUPLICATE WRITE. No idempotency key anywhere in the call_tool signature. Docs only mention idempotent_hint (a hint, servers/tools page).
- B. Response cut mid-body (headers + 40 of 261 body bytes): `httpx2.RemoteProtocolError: peer closed connection without sending complete message body (received 40 bytes, expected 261)` escapes at async with exit; no partial result returned. (inside-block type = same CancelledError pattern as A; not separately printed in first run, printed in repro run.)
- C. Request cut inside tool arguments (client->server; forwarded up to `"arguments":{"item":"stal`): server did NOT run the tool (no orders.log); server log shows starlette.requests.ClientDisconnect traceback; client got `RemoteProtocolError: Server disconnected without sending a response.`
  - NOTE: true "model stream cut inside tool-call args" is a model-API concept; MCP SDK has no model streaming. This is the nearest MCP-layer equivalent. Model-layer NOT tested.
- D. STALL, default client settings (proxy sends SSE headers then silence): ReadTimeout after 300.4 s (18:02:44 -> 18:07:45). Message was EMPTY: `httpx2.ReadTimeout: ''`, surfaced only at `async with` exit. Docs claim "300-second read timeout" (client/transports) = accurate. The server had already executed the write.
  - With `call_tool(..., read_timeout_seconds=5)`: after 5.2 s `MCPError: "Request 'tools/call' timed out"`, caught by `except Exception` inside block. Clean.
- E. Keep-alive trickle (SSE comment every 1 s) with httpx read=3 s: still waiting at 20 s (my wait_for stopped it) -> httpx read timeout is per-chunk inactivity, not a deadline. With read_timeout_seconds=8: MCPError timed out at 8.2 s.
- F. v2.3.0 feature `max_sse_event_size` (client/transports page "Larger SSE events"): 2 MiB result from a plain tool -> OK with default client (JSON body, limit not applied). 2 MiB from a tool that calls ctx.report_progress (SSE) -> default client: `MCPError: 'SSE stream failed: Server-sent event exceeded the 1048576 byte limit.'`; with streamable_http_client(url, max_sse_event_size=32*1024*1024) -> OK. Docs say "JSON responses are unaffected" but never say when you get JSON vs SSE.
- Docs warning check: `streamable_http_client(url, headers=...)` -> `TypeError: streamable_http_client() got an unexpected keyword argument 'headers'` — exactly as the docs quote.
- Docs check: troubleshooting page says "Caught inside it, the same failure is the plain MCPError" (about errors that leave `async with`). True for server-reported errors; NOT true for transport failures in A/B (inside = CancelledError).
- Not tested: stdio transport faults, 429 / HTTP status faults, event_store/resumability (docs say resumability exists on the legacy stateful leg; my server negotiated 2026-07-28 so Last-Event-ID resume untested), json_response=True server, model-API layer, Inspector (never came up, see above), Python 3.10/3.11 (only 3.13.1 tried).
- README says "Python 3.11+"; PyPI `requires_python` = ">=3.10".
- gh search of modelcontextprotocol/python-sdk issues for "CancelledError connection" / "ReadTimeout call_tool": 0 results (did not look further).

## Clean-directory verification (18:09-18:18): new dir verify/, new venv, pip install "mcp[cli]" -> mcp 2.3.0, `python -I run_cases.py --full`
A,B,C,D,E,F reproduced exactly (see verify/verify_out.txt). B and C also show CancelledError inside the block.
G (default stall, proxy2 = real SSE headers then silence): 300.0s, `MCPError: SSE stream ended without a response` inside block, nothing at exit, server executed 1x.
=> DIFFERS from my first stall test (old proxy stalled a Content-Length JSON body: 300.4s, empty `httpx2.ReadTimeout: ''` at exit). Both valid; the error depends on reply type. Doc reports both.

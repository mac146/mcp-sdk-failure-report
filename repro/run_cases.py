"""Fault cases for the MCP Python SDK 2.3.0. Usage: python run_cases.py [--full]   (--full adds the 300 s default-timeout stall)
Starts its own server (:8021) and one proxy per case. Prints one line per observation."""
import asyncio, os, subprocess, sys, time, pathlib
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

HERE = pathlib.Path(__file__).parent
LOG = HERE / "orders.log"
SRV, PORT = 8021, 8022
URL = f"http://127.0.0.1:{PORT}/mcp"
procs = []

def spawn(*args, env=None):
    p = subprocess.Popen([sys.executable, *args], cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    procs.append(p); return p

def orders(item): return LOG.read_text().splitlines().count(item) if LOG.exists() else 0

def leaves(e):
    if isinstance(e, BaseExceptionGroup):
        for s in e.exceptions: yield from leaves(s)
    else: yield e

async def call(target, tool, args, **kw):
    """returns (what call_tool raised INSIDE the block, what escaped at `async with` exit, seconds)"""
    t = time.time(); inner = outer = None
    try:
        async with Client(target) as c:
            try:
                await c.call_tool(tool, args, **kw); inner = "OK"
            except BaseException as e:
                inner = f"{type(e).__name__}{'' if isinstance(e, Exception) else ' (not an Exception subclass)'}: {str(e)[:70]}"
                if not isinstance(e, Exception): raise
    except BaseException as e:
        outer = "; ".join(f"{type(x).__name__}: {str(x)[:70]}" for x in leaves(e))
    return inner, outer, time.time() - t

def start_proxy(mode):
    spawn("proxy2.py", mode, str(PORT), str(SRV)); time.sleep(1.5)

def stop_proxy():
    procs.pop().kill(); time.sleep(0.5)

async def main(full):
    LOG.unlink(missing_ok=True)
    spawn("orders_server.py", str(SRV), env={**os.environ, "ORDERS_LOG": str(LOG)}); time.sleep(8)
    print(f"SDK: mcp {__import__('importlib.metadata').metadata.version('mcp')}, python {sys.version.split()[0]}")

    start_proxy("drop_response")
    i, o, s = await call(URL, "create_order", {"item": "order-42"})
    print(f"[A lost response] call 1 inside block: {i} | at exit: {o} | {s:.1f}s | server executed: {orders('order-42')}x")
    i, o, s = await call(URL, "create_order", {"item": "order-42"})
    print(f"[A lost response] retry on fresh Client: {i} | server executed in total: {orders('order-42')}x"); stop_proxy()

    start_proxy("truncate_response")
    i, o, s = await call(URL, "create_order", {"item": "cut-body"})
    print(f"[B response cut mid-body] inside: {i} | at exit: {o} | {s:.1f}s | server executed: {orders('cut-body')}x"); stop_proxy()

    start_proxy("truncate_request")
    i, o, s = await call(URL, "create_order", {"item": "stal-args"})
    print(f"[C request cut inside arguments] inside: {i} | at exit: {o} | server executed: {orders('stal-args')}x"); stop_proxy()

    start_proxy("stall")
    i, o, s = await call(URL, "create_order", {"item": "stall-5s"}, read_timeout_seconds=5)
    print(f"[D stall + read_timeout_seconds=5] inside: {i} | {s:.1f}s | server executed: {orders('stall-5s')}x"); stop_proxy()

    start_proxy("trickle_sse")
    http = httpx2.AsyncClient(timeout=httpx2.Timeout(30.0, read=3.0))
    t = time.time()
    try:
        async with Client(streamable_http_client(URL, http_client=http)) as c:
            try:
                await asyncio.wait_for(c.call_tool("create_order", {"item": "trickle"}), 20); r = "returned"
            except asyncio.TimeoutError: r = f"still waiting after {time.time()-t:.0f}s although httpx read timeout = 3s (my own wait_for stopped it)"
            except Exception as e: r = f"{type(e).__name__}: {e}"
    except BaseException as e: r = "escaped " + type(e).__name__
    print(f"[E SSE keep-alive comments every 1s, no read_timeout_seconds] {r}"); stop_proxy()

    for tool, label in (("big", "plain tool"), ("big_with_progress", "tool that calls ctx.report_progress")):
        async def cb(p, t_, m): pass
        kw = {"progress_callback": cb} if tool != "big" else {}
        start_proxy("pass")
        res = []
        for lim in ("default", 32 * 1024 * 1024):
            tgt = URL if lim == "default" else streamable_http_client(URL, max_sse_event_size=lim)
            i, o, s = await call(tgt, tool, {"n": 2 * 1024 * 1024}, **kw); res.append(f"{'default' if lim=='default' else 'max_sse_event_size=32MiB'}: {i}")
        print(f"[F 2 MiB result, {label}] " + " | ".join(res)); stop_proxy()

    if full:
        start_proxy("stall")
        print("[G stall, default client settings] waiting (about 5 min)...", flush=True)
        i, o, s = await call(URL, "create_order", {"item": "stall-default"})
        print(f"[G stall, default client settings] inside: {i} | at exit: {o} | {s:.1f}s | server executed: {orders('stall-default')}x"); stop_proxy()

try:
    asyncio.run(main("--full" in sys.argv))
finally:
    for p in procs: p.kill()

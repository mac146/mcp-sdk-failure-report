"""Tiny TCP fault proxy (arms on the FIRST tools/call only).
MODE: pass | stall | drop_response | truncate_response | trickle_sse
usage: python proxy2.py MODE LISTEN_PORT UPSTREAM_PORT"""
import asyncio, sys

MODE, LPORT, UPORT = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
armed = {"done": False}
SSE_HDR = b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ncache-control: no-cache\r\n\r\n"


async def handle(cr, cw):
    ur, uw = await asyncio.open_connection("127.0.0.1", UPORT)
    st = {"fault": False, "buf": b""}

    async def c2u():
        try:
            while (d := await cr.read(65536)):
                if MODE == "truncate_request" and not armed["done"] and (b"tools/call" in d or st["fault"]):
                    st["fault"] = True
                    st["creq"] = st.get("creq", b"") + d
                    mark = b'"arguments":{"item":"stal'
                    k = st["creq"].find(mark)
                    if k < 0:
                        continue
                    armed["done"] = True
                    out = st["creq"][:k + len(mark)]
                    uw.write(out); await uw.drain()
                    print(f"[proxy] request total {len(st['creq'])}B; forwarded only {len(out)}B, ending inside arguments: ...{out[-60:]!r}", flush=True)
                    await asyncio.sleep(0.5); uw.close(); cw.close(); return
                if b"tools/call" in d and MODE not in ("pass", "truncate_request") and not armed["done"]:
                    st["fault"] = armed["done"] = True
                    print(f"[proxy] tools/call seen, arming {MODE}", flush=True)
                uw.write(d); await uw.drain()
        except Exception:
            pass

    async def u2c():
        try:
            while (d := await ur.read(65536)):
                if not st["fault"]:
                    cw.write(d); await cw.drain(); continue
                if MODE == "stall":
                    cw.write(SSE_HDR); await cw.drain()
                    print("[proxy] sent SSE headers, now silent", flush=True)
                    await asyncio.sleep(10**6)
                if MODE == "drop_response":
                    print(f"[proxy] dropped {len(d)}B of response, closing", flush=True); cw.close(); return
                if MODE == "truncate_response":
                    st["buf"] += d
                    he = st["buf"].find(b"\r\n\r\n") + 4
                    if he >= 4 and len(st["buf"]) >= he + 40:
                        cw.write(st["buf"][:he + 40]); await cw.drain()
                        print(f"[proxy] sent headers + 40 of {len(st['buf']) - he} body bytes, closing", flush=True)
                        cw.close(); return
                    continue
                if MODE == "trickle_sse":
                    cw.write(SSE_HDR); await cw.drain(); n = 0
                    while True:
                        cw.write(b": keepalive\n\n"); await cw.drain(); n += 1
                        if n % 5 == 0: print(f"[proxy] keepalive {n}", flush=True)
                        await asyncio.sleep(1)
        except Exception:
            pass
        finally:
            try: cw.close()
            except Exception: pass

    await asyncio.gather(c2u(), u2c())


async def main():
    s = await asyncio.start_server(handle, "127.0.0.1", LPORT)
    print(f"[proxy] {MODE} on {LPORT} -> {UPORT}", flush=True)
    async with s: await s.serve_forever()
asyncio.run(main())

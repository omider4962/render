"""Minimal VLESS-only panel: three fixed UUIDs, unlimited quota and no expiry."""
import asyncio
import contextlib
import logging
import os
import secrets
import time
import uuid as uuid_lib
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import database as db
import vless
from utils import uptime_str

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("vless-panel")
templates = Jinja2Templates(directory="templates")
connections = {}
connections_lock = asyncio.Lock()
stats_rt = {"total_bytes": 0, "total_errors": 0}
RELAY_BUF = 32 * 1024


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    logger.info("VLESS panel started with three fixed configs")
    try:
        yield
    finally:
        await db.close_db()


app = FastAPI(title="VLESS Panel", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/panel")


@app.get("/panel", response_class=HTMLResponse)
async def panel_page(request: Request):
    return templates.TemplateResponse("panel.html", {"request": request})


@app.get("/health")
async def health():
    async with connections_lock:
        count = len(connections)
    return {"status": "ok", "connections": count, "uptime": uptime_str()}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(content=b"", media_type="image/x-icon", status_code=204)


@app.get("/api/configs")
async def get_configs():
    domain = vless.get_domain()
    return [
        {
            "slot": item["slot"],
            "name": f"کانفیگ {item['slot']}",
            "uid": item["uid"],
            "link": vless.build_vless_link(item["uid"], f"VLESS-{item['slot']}", domain),
            "subscription": f"https://{domain}/sub/{item['uid']}",
        }
        for item in await db.list_configs()
    ]


@app.get("/api/stats")
async def get_stats():
    async with connections_lock:
        count = len(connections)
    return {"connections": count, "domain": vless.get_domain(), "uptime": uptime_str()}


@app.get("/sub/{uid}", response_class=PlainTextResponse)
async def subscription(uid: str):
    import base64
    if not await db.is_config(uid):
        raise HTTPException(status_code=404, detail="not found")
    link = vless.build_vless_link(uid, f"VLESS-{uid[:8]}")
    return base64.b64encode(link.encode()).decode()


class _UDPQueueProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.queue = asyncio.Queue(maxsize=256)

    def datagram_received(self, data, addr):
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait((data, addr))

    def connection_lost(self, exc):
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait((b"", None))


async def _open_udp_transport(remote):
    loop = asyncio.get_running_loop()
    proto = _UDPQueueProtocol()
    transport, _ = await asyncio.wait_for(
        loop.create_datagram_endpoint(lambda: proto, local_addr=("0.0.0.0", 0), remote_addr=remote), timeout=10.0
    )
    return transport, proto


async def _record_bytes(count: int):
    stats_rt["total_bytes"] += count


async def _pump_ws_to_tcp(websocket, writer):
    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                return
            data = msg.get("bytes") or (msg.get("text") or "").encode()
            if data:
                writer.write(data)
                await writer.drain()
                await _record_bytes(len(data))
    except (WebSocketDisconnect, ConnectionError, BrokenPipeError):
        pass
    except asyncio.CancelledError:
        raise
    except Exception:
        stats_rt["total_errors"] += 1
        logger.exception("websocket-to-tcp relay failed")


async def _pump_tcp_to_ws(websocket, reader, version):
    try:
        await websocket.send_bytes(bytes((version & 0xFF, 0)))
        while True:
            data = await reader.read(RELAY_BUF)
            if not data:
                return
            await websocket.send_bytes(data)
            await _record_bytes(len(data))
    except (WebSocketDisconnect, RuntimeError):
        pass
    except asyncio.CancelledError:
        raise
    except Exception:
        stats_rt["total_errors"] += 1
        logger.exception("tcp-to-websocket relay failed")


async def _pump_vless_udp(websocket, transport, initial=b""):
    buffer = bytearray(initial)
    try:
        while True:
            if len(buffer) < 2 or len(buffer) < 2 + int.from_bytes(buffer[:2], "big"):
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                buffer.extend(msg.get("bytes") or (msg.get("text") or "").encode())
                continue
            size = int.from_bytes(buffer[:2], "big")
            packet = bytes(buffer[2:2 + size])
            del buffer[:2 + size]
            if packet:
                transport.sendto(packet)
                await _record_bytes(len(packet))
    except (WebSocketDisconnect, RuntimeError):
        pass
    except asyncio.CancelledError:
        raise


async def _pump_udp_to_vless(websocket, proto):
    try:
        while True:
            packet, _ = await proto.queue.get()
            if not packet:
                return
            await websocket.send_bytes(len(packet).to_bytes(2, "big") + packet)
            await _record_bytes(len(packet))
    except (WebSocketDisconnect, RuntimeError):
        pass
    except asyncio.CancelledError:
        raise


def _tune_socket(writer):
    try:
        import socket
        sock = writer.get_extra_info("socket")
        if sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        pass


@app.websocket("/ws/{uid}")
async def websocket_tunnel(websocket: WebSocket, uid: str):
    await websocket.accept()
    writer = None
    conn_id = None
    address = port = "?"
    try:
        if not await db.is_config(uid):
            await websocket.close(code=1008, reason="unknown config")
            return
        first_chunk = b""
        for _ in range(8):
            msg = await asyncio.wait_for(websocket.receive(), timeout=15.0)
            if msg["type"] == "websocket.disconnect":
                return
            first_chunk += msg.get("bytes") or (msg.get("text") or "").encode()
            if len(first_chunk) > 64 * 1024:
                await websocket.close(code=1008, reason="header too large")
                return
            try:
                version, command, address, port, initial_payload = vless.parse_vless_header(first_chunk)
                break
            except ValueError as exc:
                if any(word in str(exc).lower() for word in ("incomplete", "too small")):
                    continue
                await websocket.close(code=1008, reason="invalid header")
                return
        else:
            await websocket.close(code=1008, reason="incomplete header")
            return
        try:
            header_uid = str(uuid_lib.UUID(bytes=first_chunk[1:17]))
        except (ValueError, TypeError):
            await websocket.close(code=1008, reason="invalid uuid")
            return
        if header_uid.lower() != uid.lower():
            await websocket.close(code=1008, reason="uuid mismatch")
            return

        conn_id = secrets.token_urlsafe(8)
        async with connections_lock:
            connections[conn_id] = {"uid": uid, "connected_at": datetime.now(timezone.utc).isoformat()}

        if command == 2:
            transport, proto = await _open_udp_transport((address, port))
            try:
                await websocket.send_bytes(bytes((version & 0xFF, 0)))
                up = asyncio.create_task(_pump_vless_udp(websocket, transport, initial_payload))
                down = asyncio.create_task(_pump_udp_to_vless(websocket, proto))
                _, pending = await asyncio.wait((up, down), return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
            finally:
                transport.close()
            return

        reader, writer = await asyncio.wait_for(asyncio.open_connection(address, port), timeout=10.0)
        _tune_socket(writer)
        if initial_payload:
            writer.write(initial_payload)
            await writer.drain()
            await _record_bytes(len(initial_payload))
        up = asyncio.create_task(_pump_ws_to_tcp(websocket, writer))
        down = asyncio.create_task(_pump_tcp_to_ws(websocket, reader, version))
        _, pending = await asyncio.wait((up, down), return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
    except WebSocketDisconnect:
        pass
    except (asyncio.TimeoutError, OSError) as exc:
        stats_rt["total_errors"] += 1
        logger.warning("VLESS outbound failed uid=%s target=%s:%s error=%s", uid, address, port, exc)
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="outbound connection failed")
    except Exception:
        stats_rt["total_errors"] += 1
        logger.exception("VLESS relay failed uid=%s", uid)
    finally:
        if writer:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
        if conn_id:
            async with connections_lock:
                connections.pop(conn_id, None)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)),
        loop="auto", ws_ping_interval=10.0, ws_ping_timeout=10.0,
    )

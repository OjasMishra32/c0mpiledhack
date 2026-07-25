from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import orchestrator
from app.config import lan_ip, settings
from app.state import InboundMessage, state
from app.websocket_manager import ws, SlotsFull

log = logging.getLogger("hive")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="HIVE")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
async def startup() -> None:
    await state.reset("incident_stabilization")
    app.state.tick_task = asyncio.create_task(orchestrator.run_forever())
    if settings.demo_mode:
        log.info("DEMO MODE — join at http://%s:5173/join", lan_ip())


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "mode": state.mode,
        "workers_connected": sum(1 for w in state.workers.values() if w.connected),
        "uptime": True,
    }


@app.get("/api/join-info")
async def join_info() -> dict:
    ip = lan_ip()
    return {"url": f"http://{ip}:5173/join", "lan_ip": ip, "port": settings.port}


@app.get("/api/state")
async def api_state() -> dict:
    return {
        "mode": state.mode,
        "goal": state.goal.model_dump() if state.goal else None,
        "actions": {k: v.model_dump() for k, v in state.actions.items()},
        "workers": [w.public_dict() for w in state.workers.values()],
        "world": state.world.model_dump(),
        "execution_status": state.execution_status,
    }


@app.websocket("/ws")
async def ws_endpoint(sock: WebSocket, role: str = "host", token: str | None = None) -> None:
    await sock.accept()
    worker = None
    try:
        if role == "worker":
            try:
                worker = await ws.connect_worker(sock, token)
            except SlotsFull:
                return
        else:
            await ws.connect_host(sock)
        await ws.send_snapshot(sock)
        while True:
            raw = await sock.receive_json()
            state.inbox.append(InboundMessage(
                type=raw["type"],
                payload=raw.get("payload", {}),
                worker_id=worker.id if worker else None,
                role=role,
            ))
    except WebSocketDisconnect:
        pass
    finally:
        await ws.disconnect(sock)


_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="ui")

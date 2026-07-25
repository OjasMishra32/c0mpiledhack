"""HIVE backend entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import orchestrator
from .config import join_url, lan_ip, settings
from .demo.scenarios import SCENARIOS
from .models import InboundMessage
from .perception import nim_client
from .perception.analyzer import analyzer
from .state import state
from .websocket_manager import SlotsFull, ws

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("hive")

ROOT = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def lifespan(app: FastAPI):
    await state.reset()
    app.state.tick = asyncio.create_task(orchestrator.run_forever())
    app.state.probe = asyncio.create_task(_startup_probe())
    log.info("HIVE online — host http://localhost:%d/host", settings.frontend_port)
    log.info("             join %s", join_url())
    yield
    for t in ("tick", "probe"):
        task = getattr(app.state, t, None)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    from .vision.camera import camera

    camera.release()


async def _startup_probe() -> None:
    """Discover which models this account can actually reach, and warm the camera.

    Free hosted endpoints vary by account, so the stack self-configures rather than
    trusting a hardcoded model id.
    """
    from .planner.base import planner_probe

    await asyncio.gather(analyzer.probe(), planner_probe.run())
    if analyzer.reasoner:
        await state.emit(
            "perception_ready",
            f"Scene reasoning online ({analyzer.reasoner.split('/')[-1]}).",
            severity="success",
        )
    elif settings.has_model_access:
        await state.emit(
            "perception_degraded",
            "Scene reasoning unavailable for this account — running on computer vision alone.",
            severity="warn",
        )

    from .planner.base import planner_probe as _pp

    await state.emit(
        "planner_ready",
        f"Task compiler ready — {_pp.reason}.",
        severity="info" if _pp.usable else "warn",
    )

    if settings.world_mode in ("live", "assisted"):
        from .vision.camera import camera

        # Open during startup, never during the demo: on macOS the first read triggers
        # the permission prompt, and a permission dialog on stage is a disaster.
        ok = await asyncio.to_thread(camera.open)
        state.world.camera_online = ok
        state.world.mode = settings.world_mode if ok else "simulation"  # type: ignore[assignment]
        await state.emit(
            "camera_status",
            f"Camera {settings.camera_index} online." if ok else "No camera — running in simulation.",
            severity="success" if ok else "warn",
        )


app = FastAPI(title="HIVE", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# ── HTTP ────────────────────────────────────────────────────────────────────


def _planner_probe():
    from .planner.base import planner_probe

    return planner_probe


@app.get("/api/health")
async def health():
    from .vision.camera import camera

    return {
        "ok": True,
        "mode": state.world.mode,
        "execution_status": state.execution_status,
        "workers_connected": sum(1 for w in state.workers.values() if w.connected),
        "scenario": state.scenario.id,
        "camera": {"online": camera.online, "index": camera.index, "fps": camera.fps, "error": camera.error},
        "models": nim_client.health(),
        "perception": analyzer.health(),
        "planner": {"usable": _planner_probe().usable, "reason": _planner_probe().reason},
        "demo_mode": settings.demo_mode,
    }


@app.get("/api/join-info")
async def join_info():
    return {"url": join_url(), "lan_ip": lan_ip(), "port": settings.frontend_port}


@app.get("/api/state")
async def get_state():
    return state.snapshot()


@app.get("/api/scenarios")
async def scenarios():
    return [
        {"id": s.id, "title": s.title, "subtitle": s.subtitle, "suggested_goal": s.suggested_goal}
        for s in SCENARIOS.values()
    ]


@app.get("/api/vision/cameras")
async def cameras():
    from .vision.camera import probe_cameras

    return await asyncio.to_thread(probe_cameras)


@app.post("/api/vision/select/{index}")
async def select_camera(index: int):
    from .vision.camera import camera

    ok = await asyncio.to_thread(camera.open, index)
    state.world.camera_online = ok
    if ok:
        settings.camera_index = index
        state.world.mode = "live"  # type: ignore[assignment]
    return {"ok": ok, "index": index, "error": camera.error}


@app.get("/api/vision/frame.mjpg")
async def mjpeg():
    """MJPEG chosen over WebRTC deliberately: ~20 lines, no negotiation, works everywhere."""
    from .vision.camera import camera

    async def gen():
        boundary = b"--frame\r\n"
        while True:
            jpeg = camera.snapshot_jpeg()
            if jpeg:
                yield boundary + b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            await asyncio.sleep(1 / 15)

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/api/voygr/usage")
async def voygr_usage():
    from .integrations.voygr import usage

    return await usage()


# ── WebSocket ───────────────────────────────────────────────────────────────


@app.websocket("/ws")
async def ws_endpoint(sock: WebSocket, role: str = "host", token: str | None = None):
    await sock.accept()
    worker = None
    try:
        if role == "worker":
            worker = await ws.connect_worker(sock, token)
        else:
            await ws.connect_host(sock)
        await ws.send_snapshot(sock, role, worker.id if worker else None)

        while True:
            raw = await sock.receive_json()
            mtype = raw.get("type")
            if not mtype:
                continue
            # Workers may only send worker_* messages. A phone can never drive the host.
            if role == "worker" and not mtype.startswith("worker_"):
                continue
            state.inbox.append(
                InboundMessage(
                    type=mtype,
                    payload=raw.get("payload") or {},
                    worker_id=worker.id if worker else None,
                    role=role,
                )
            )
    except (WebSocketDisconnect, SlotsFull):
        pass
    except Exception:
        log.debug("ws error", exc_info=True)
    finally:
        await ws.disconnect(sock)


# Mount the built frontend LAST, or it swallows /api and /ws.
_dist = ROOT / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="ui")

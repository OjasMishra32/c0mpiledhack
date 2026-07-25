"""Minimal FastAPI app exposing Steven's vision workstream standalone, so the
camera pipeline can be exercised end-to-end before backend-core (Ojas.md)
exists. Endpoints match the contract in docs/CONTRACTS.md where it overlaps."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time

import cv2
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.demo.simulator import Simulator
from app.models import utc_now
from app.state import state
from app.vision.calibration import Calibration
from app.vision.camera import Camera
from app.vision.world_model import WorldModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("hive.main")

app = FastAPI(title="HIVE — vision workstream")

# CV discovery runs on a downscaled copy of the display frame so a higher
# capture resolution (for stream quality) doesn't slow down the world-model
# tick — Detection positions are normalized, so resolution is irrelevant to
# the resulting object/zone coordinates.
PROCESS_WIDTH = 640
PROCESS_HEIGHT = 360
JPEG_QUALITY = 92

camera = Camera(index=settings.camera_index)
world_model = WorldModel(state)
calibration = Calibration(world_model.discovery)
simulator = Simulator(state)

_vision_task: asyncio.Task | None = None
_boot_time = time.time()


@app.on_event("startup")
async def startup() -> None:
    world_model.load_profile()
    state.world.mode = settings.world_mode
    if settings.world_mode == "simulation":
        simulator.spawn_scene(n=5)
    global _vision_task
    _vision_task = asyncio.create_task(_vision_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    if _vision_task:
        _vision_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _vision_task
    camera.release()


async def _vision_loop() -> None:
    """Frame-driven, not polled: blocks on the next captured frame rather than
    sleeping a fixed interval, so the world model tracks the camera's actual
    cadence. Never blocks the event loop, never raises out of the loop."""
    frame_count = 0
    fps_window_start = time.time()
    last_version = 0
    while True:
        try:
            if state.world.mode == "live":
                if not camera.online:
                    camera.maybe_reopen()
                if camera.online:
                    frame, last_version = await asyncio.to_thread(
                        camera.wait_for_new_frame, last_version, 1.0
                    )
                    if frame is not None:
                        small = cv2.resize(frame, (PROCESS_WIDTH, PROCESS_HEIGHT))
                        detections = world_model.discovery.discover(small, calibration)
                        world_model.ingest(detections)
                        state.world.camera_online = True
                        state.world.last_frame_at = utc_now()
                        frame_count += 1
                else:
                    state.world.camera_online = False
                    await asyncio.sleep(0.2)
            else:
                await asyncio.sleep(0.2)
            now = time.time()
            if now - fps_window_start >= 1.0:
                state.world.vision_fps = frame_count / (now - fps_window_start)
                frame_count = 0
                fps_window_start = now
        except Exception:
            log.exception("vision tick")
            await asyncio.sleep(0.1)


# ---- HTTP endpoints -----------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    return """
    <html><body style="font-family: sans-serif; padding: 2rem;">
      <h2>HIVE vision workstream</h2>
      <ul>
        <li><a href="/api/vision/frame.mjpg">/api/vision/frame.mjpg</a> — live MJPEG stream</li>
        <li><a href="/api/health">/api/health</a></li>
        <li><a href="/api/vision/scene">/api/vision/scene</a></li>
        <li><a href="/api/events">/api/events</a></li>
        <li><a href="/docs">/docs</a> — full API (FastAPI auto docs)</li>
      </ul>
    </body></html>
    """


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "mode": state.world.mode,
        "camera_online": state.world.camera_online,
        "camera_resolution": f"{camera.actual_width}x{camera.actual_height}",
        "camera_fps": round(camera.actual_fps, 1),
        "vision_fps": round(state.world.vision_fps, 1),
        "uptime": round(time.time() - _boot_time, 1),
    }


@app.post("/api/vision/warmup")
async def warmup():
    """Open the camera during setup, not during the demo — triggers the macOS
    permission prompt ahead of time."""
    ok = camera.open()
    return {"ok": ok, "online": camera.online}


class ScanRequest(BaseModel):
    relabel: bool = True


@app.post("/api/vision/scan")
async def scan(req: ScanRequest = ScanRequest()):
    if state.world.mode == "simulation":
        objects = simulator.spawn_scene(n=len(state.scene.objects) or 5)
        return _scene_json()

    if not camera.online:
        camera.open()
    frame = await asyncio.to_thread(camera.read)
    if frame is None:
        return JSONResponse({"error": "no frame available"}, status_code=503)

    world_model.rebuild_scene(frame)
    await asyncio.sleep(1.5)  # let discovery settle over a few frames before declaring stable
    world_model.mark_scene_stable()
    return _scene_json()


def _scene_json():
    scene = state.scene
    return {
        "objects": [o.model_dump() for o in scene.objects],
        "zones": [z.model_dump() for z in scene.zones],
        "scanned_at": scene.scanned_at,
        "object_count": scene.object_count,
        "labeling_source": scene.labeling_source,
        "stable": scene.stable,
    }


@app.get("/api/vision/scene")
async def get_scene():
    return _scene_json()


@app.get("/api/vision/frame.mjpg")
async def mjpeg_stream():
    async def gen():
        last_version = 0
        while True:
            frame = None
            if state.world.mode == "live" and camera.online:
                frame, last_version = await asyncio.to_thread(
                    camera.wait_for_new_frame, last_version, 1.0
                )
                if calibration.state.show_mask and frame is not None:
                    world_model.discovery.discover(frame, calibration)
                    mask = world_model.discovery.last_mask()
                    if mask is not None:
                        frame = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
            if frame is None:
                frame = _placeholder_frame()
                await asyncio.sleep(0.1)  # avoid a busy loop when nothing is live
            ok, buf = await asyncio.to_thread(
                cv2.imencode, ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
            )
            if ok:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n")

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


def _placeholder_frame():
    import numpy as np
    img = np.zeros((360, 640, 3), np.uint8)
    img[:] = (30, 30, 30)
    cv2.putText(img, "NO CAMERA - simulation mode", (40, 180),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (120, 220, 120), 2, cv2.LINE_AA)
    return img


class ZoneRequest(BaseModel):
    zone_id: str | None = None
    bounds: tuple[float, float, float, float]
    label: str


@app.post("/api/vision/zones")
async def define_zone(req: ZoneRequest):
    zone = world_model.define_zone(req.zone_id, req.bounds, req.label)
    world_model.save_profile()
    return zone.model_dump()


@app.post("/api/vision/detect-zones")
async def detect_zones_endpoint():
    if not camera.online:
        return JSONResponse({"error": "camera not online"}, status_code=503)
    frame = await asyncio.to_thread(camera.read)
    if frame is None:
        return JSONResponse({"error": "no frame available"}, status_code=503)
    return {"proposed": world_model.detect_zones_from_frame(frame)}


class CalibrateRequest(BaseModel):
    saliency_bias: int | None = None
    min_area_frac: float | None = None
    max_area_frac: float | None = None
    show_mask: bool | None = None


@app.post("/api/vision/calibrate")
async def calibrate(req: CalibrateRequest):
    if req.saliency_bias is not None:
        calibration.set_saliency(req.saliency_bias)
    if req.min_area_frac is not None or req.max_area_frac is not None:
        calibration.set_area_bounds(req.min_area_frac, req.max_area_frac)
    if req.show_mask is not None:
        calibration.toggle_mask(req.show_mask)
    return {"ok": True, "state": calibration.state.__dict__}


class SetModeRequest(BaseModel):
    mode: str


@app.post("/api/vision/mode")
async def set_mode(req: SetModeRequest):
    if req.mode not in ("live", "assisted", "simulation"):
        return JSONResponse({"error": "invalid mode"}, status_code=400)
    state.world.mode = req.mode
    if req.mode == "simulation" and not state.scene.objects:
        simulator.spawn_scene(n=5)
    return {"ok": True, "mode": state.world.mode}


@app.get("/api/events")
async def events():
    return [e.model_dump() for e in state.events[-50:]]

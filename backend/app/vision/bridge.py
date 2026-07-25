"""Bridge between the vision workstream and the orchestration core.

The vision modules are class-based and constructed against the live state; the
orchestrator wants a handful of module-level functions it can call every tick. Rather
than bend either side, this file owns the wiring: it holds the singletons, exposes the
flat API the tick uses, and adds the two capabilities the core needs that the vision
layer has no reason to provide (a JPEG ring buffer for reasoning bursts, and animated
motion for simulated execution).

OWNER: Ojas. If a vision API changes, this file absorbs it — nothing in orchestrator.py
or host_commands.py should ever import a vision class directly.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

import cv2

from ..models import Point, now_iso

log = logging.getLogger("hive.vision.bridge")

_camera: Any = None
_discovery: Any = None
_world: Any = None
_sim: Any = None

# JPEG ring buffer for the perception layer's reasoning bursts. Kept here so the camera
# module stays a pure capture device.
_ring: deque[tuple[float, bytes]] = deque(maxlen=90)
_ring_lock = threading.Lock()
_ring_thread: threading.Thread | None = None
_ring_stop = threading.Event()

# Simulated motion: objects glide rather than teleport, which is most of why simulation
# mode reads as premium instead of fake.
_motions: dict[str, tuple[Point, Point, float, float]] = {}
_overrides: dict[str, float] = {}
_OVERRIDE_TTL = 20.0


# ── singletons ──────────────────────────────────────────────────────────────


def camera():
    global _camera
    if _camera is None:
        from ..config import settings
        from .camera import Camera

        _camera = Camera(settings.camera_index)
    return _camera


def discovery():
    global _discovery
    if _discovery is None:
        from .scene_discovery import SceneDiscovery

        _discovery = SceneDiscovery()
    return _discovery


def world(state: Any):
    global _world
    if _world is None or getattr(_world, "state", None) is not state:
        from .world_model import WorldModel

        _world = WorldModel(state)
    return _world


def simulator(state: Any):
    global _sim
    if _sim is None or getattr(_sim, "state", None) is not state:
        from ..demo.simulator import Simulator

        _sim = Simulator(state)
    return _sim


# ── camera lifecycle ────────────────────────────────────────────────────────


def open_camera(index: int | None = None) -> bool:
    cam = camera()
    if index is not None and index != cam.index:
        release_camera()
        cam.index = index
    ok = cam.open()
    if ok:
        _start_ring()
    return ok


def release_camera() -> None:
    _ring_stop.set()
    with _ring_lock:
        _ring.clear()
    try:
        camera().release()
    except Exception:
        pass


def camera_online() -> bool:
    try:
        return camera().online
    except Exception:
        return False


def camera_fps() -> float:
    return round(getattr(camera(), "actual_fps", 0.0), 1)


def latest_frame():
    try:
        return camera().read()
    except Exception:
        return None


def _start_ring() -> None:
    """Sample the feed to JPEG at ~6 Hz so a burst can cover the last couple of seconds
    without holding a wall of frames in memory."""
    global _ring_thread
    if _ring_thread and _ring_thread.is_alive():
        return
    _ring_stop.clear()

    def loop() -> None:
        while not _ring_stop.is_set():
            frame = latest_frame()
            if frame is not None:
                try:
                    enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 72])[1]
                    with _ring_lock:
                        _ring.append((time.monotonic(), enc.tobytes()))
                except Exception:
                    pass
            time.sleep(1 / 6)

    _ring_thread = threading.Thread(target=loop, daemon=True, name="hive-frame-ring")
    _ring_thread.start()


def snapshot_jpeg(quality: int = 75) -> bytes | None:
    frame = latest_frame()
    if frame is None:
        return None
    try:
        return cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()
    except Exception:
        return None


def burst(count: int = 5, seconds: float = 2.5) -> list[bytes]:
    """The most recent `count` frames spanning ~`seconds`, oldest first.

    A reasoning burst beats a single still because almost every question worth asking is
    about change: did it move, or was it briefly occluded?
    """
    with _ring_lock:
        snapshot = list(_ring)
    if not snapshot:
        one = snapshot_jpeg()
        return [one] if one else []
    now = time.monotonic()
    recent = [(t, j) for t, j in snapshot if now - t <= seconds] or snapshot[-count:]
    if len(recent) <= count:
        return [j for _, j in recent]
    step = len(recent) / count
    return [recent[min(len(recent) - 1, int(i * step))][1] for i in range(count)]


# ── the tick API ────────────────────────────────────────────────────────────


def refresh(state: Any) -> bool:
    """Update the scene in place from the active source. Never blocks. Never raises."""
    try:
        if state.world.mode == "simulation" or not camera_online():
            return _step_motions(state)
        frame = latest_frame()
        if frame is None:
            return _step_motions(state)
        world(state).ingest(discovery().discover(frame))
        state.world.vision_fps = camera_fps()
        state.world.last_frame_at = now_iso()
        _sync_occupancy(state)
        return True
    except Exception:
        log.exception("vision refresh")
        return False


def scan(state: Any) -> int:
    """Full rescan: discard tracked ids and re-register from what is visible now."""
    try:
        if state.world.mode != "simulation" and camera_online():
            frame = latest_frame()
            if frame is not None:
                world(state).rebuild_scene(frame)
                _sync_occupancy(state)
                return len(state.scene.objects)
        simulator(state).spawn_scene(n=5)
        _motions.clear()
        _sync_occupancy(state)
        return len(state.scene.objects)
    except Exception:
        log.exception("scan")
        return len(state.scene.objects)


def classify_zone(state: Any, p: Point) -> str:
    try:
        return world(state).classify_zone(p)
    except Exception:
        for z in state.scene.zones:
            if z.bounds.contains(p):
                return z.id
        return "field"


def set_object_position(state: Any, object_id: str, position: Point) -> None:
    """Host-assisted observation. Wins over tracking for a short window."""
    _overrides[object_id] = time.monotonic()
    _motions.pop(object_id, None)
    obj = state.scene.by_id(object_id)
    if not obj:
        return
    obj.position = position
    obj.zone = classify_zone(state, position)
    obj.source = "host_override"
    obj.confidence = 1.0
    obj.visible = True
    obj.last_updated_at = now_iso()
    _sync_occupancy(state)


def set_object_zone(state: Any, object_id: str, zone_id: str) -> None:
    z = state.scene.zone_by_id(zone_id)
    if z:
        set_object_position(state, object_id, z.bounds.center)


def move_object_to_zone(state: Any, object_id: str, zone_id: str, duration: float = 1.4) -> None:
    """Animated move used when simulating physical execution."""
    obj = state.scene.by_id(object_id)
    z = state.scene.zone_by_id(zone_id)
    if not obj or not z:
        return
    _motions[object_id] = (obj.position, z.bounds.center, time.monotonic(), duration)


def override_active(object_id: str) -> bool:
    t = _overrides.get(object_id)
    return t is not None and (time.monotonic() - t) < _OVERRIDE_TTL


# ── internals ───────────────────────────────────────────────────────────────


def _step_motions(state: Any) -> bool:
    if not _motions:
        return False
    now = time.monotonic()
    changed = False
    for oid, (start, end, t0, dur) in list(_motions.items()):
        obj = state.scene.by_id(oid)
        if obj is None:
            _motions.pop(oid, None)
            continue
        t = min(1.0, (now - t0) / max(dur, 0.01))
        e = 1 - (1 - t) ** 3  # ease-out; objects glide, never teleport
        obj.position = Point(
            x=round(start.x + (end.x - start.x) * e, 4),
            y=round(start.y + (end.y - start.y) * e, 4),
        )
        obj.zone = classify_zone(state, obj.position)
        obj.last_updated_at = now_iso()
        changed = True
        if t >= 1.0:
            _motions.pop(oid, None)
    if changed:
        _sync_occupancy(state)
    return changed


def _sync_occupancy(state: Any) -> None:
    for z in state.scene.zones:
        z.occupancy = [o.id for o in state.scene.objects if o.zone == z.id]


def reset() -> None:
    _motions.clear()
    _overrides.clear()

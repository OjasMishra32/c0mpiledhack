"""World model — merges the active observation source into the scene.

OWNER: Steven. This is the seam the orchestrator calls every tick. It must be
synchronous, non-blocking, and must never raise.

Live-camera discovery/tracking lands in scene_discovery.py and plugs in here.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..demo.simulator import classify_zone, simulator
from ..models import Point, now_iso

log = logging.getLogger("hive.world")

# Host-assisted observations win over tracking for a short window.
_overrides: dict[str, float] = {}
_OVERRIDE_TTL = 20.0


def host_override(object_id: str) -> None:
    _overrides[object_id] = time.monotonic()


def _override_active(object_id: str) -> bool:
    t = _overrides.get(object_id)
    return t is not None and (time.monotonic() - t) < _OVERRIDE_TTL


def refresh(state: Any) -> bool:
    """Update scene objects in place from the active source. Returns True if changed."""
    try:
        mode = state.world.mode
        if mode == "simulation" or not state.world.camera_online:
            changed = simulator.step(state.scene)
        else:
            from .camera import camera
            from .scene_discovery import discovery

            frame = camera.latest()
            if frame is None:
                return simulator.step(state.scene)
            detections = discovery.discover(frame)
            changed = ingest(state, detections)
            state.world.vision_fps = camera.fps
            state.world.last_frame_at = now_iso()

        for z in state.scene.zones:
            z.occupancy = [o.id for o in state.scene.objects if o.zone == z.id]
        return changed
    except Exception:
        log.exception("world_model.refresh")
        return False


def ingest(state: Any, detections: list[Any]) -> bool:
    """Associate detections to tracked objects. Ids are stable; colour is an attribute."""
    from .scene_discovery import associate, register_new_object, stable_filter

    changed = False
    matches = associate(detections, state.scene.objects)

    for obj in state.scene.objects:
        if _override_active(obj.id):
            continue
        det = matches.get(obj.id)
        if det is None:
            obj.visible = False
            obj.confidence = max(0.0, obj.confidence - 0.08)  # decay, don't snap to zero
            changed = True
            continue
        smoothed = stable_filter.update(obj.id, det)
        if smoothed is None:
            continue
        obj.position = smoothed.position
        obj.confidence = smoothed.confidence
        obj.visible = True
        obj.source = "vision"
        obj.last_updated_at = now_iso()
        new_zone = classify_zone(state.scene, obj.position, margin=0.0)
        if new_zone != obj.zone and smoothed.settled:
            obj.zone = new_zone
        changed = True

    matched = set(matches.keys())
    for det in detections:
        if getattr(det, "_matched_to", None) in matched:
            continue
        if getattr(det, "_matched_to", None) is None:
            obj = register_new_object(state.scene, det)
            obj.zone = classify_zone(state.scene, obj.position)
            state.emit_soon(
                "object_appeared",
                f"New item detected in {state.zone_label(obj.zone)}: {obj.display_label()}.",
                severity="warn",
                object_id=obj.id,
            )
            changed = True
    return changed


def set_object_position(state: Any, object_id: str, position: Point) -> None:
    """Host-assisted observation / manual placement."""
    host_override(object_id)
    simulator.place(state.scene, object_id, position)
    obj = state.scene.by_id(object_id)
    if obj:
        obj.source = "host_override"
        obj.confidence = 1.0


def set_object_zone(state: Any, object_id: str, zone_id: str) -> None:
    z = state.scene.zone_by_id(zone_id)
    if not z:
        return
    set_object_position(state, object_id, z.bounds.center)

"""Owns the merge of every observation source into state.scene / state.world.

Object identity is never re-keyed by color — obj.id is the identity, the
descriptor is an attribute that can drift with lighting. See Steven.md §5.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

import numpy as np

from app.models import Descriptor, ObservedObject, Point, Rect, Zone
from app.state import HiveState
from app.vision.scene_discovery import (
    Detection,
    ProposedZone,
    SceneDiscovery,
    StableTracker,
    associate,
    detect_zones,
)

log = logging.getLogger("hive.vision.world_model")

MISSING_CONFIDENCE_DECAY = 0.08
MISSING_FLAG_THRESHOLD = 0.25
HYSTERESIS = 0.02
EMA_ALPHA = 0.15  # slow-EMA the hue; objects don't change color, but lighting drifts

PROFILE_PATH = Path(__file__).resolve().parents[3] / "scene_profile.json"


def _next_object_id(existing: list[ObservedObject]) -> str:
    nums = [int(o.id.split("_")[1]) for o in existing if o.id.startswith("obj_") and o.id.split("_")[1].isdigit()]
    return f"obj_{(max(nums) + 1) if nums else 1}"


def merge_descriptor(old: Descriptor, det: Detection) -> Descriptor:
    h_old, s_old, v_old = old.dominant_hsv
    h_new, s_new, v_new = det.dominant_hsv
    h = round(h_old * (1 - EMA_ALPHA) + h_new * EMA_ALPHA)
    s = round(s_old * (1 - EMA_ALPHA) + s_new * EMA_ALPHA)
    v = round(v_old * (1 - EMA_ALPHA) + v_new * EMA_ALPHA)
    return det.to_descriptor().model_copy(update={"dominant_hsv": (h, s, v)})


class WorldModel:
    def __init__(self, state: HiveState) -> None:
        self.state = state
        self.discovery = SceneDiscovery()
        self.stable = StableTracker()
        self._camera_fingerprint: str | None = None

    # ---- ingest: called every vision tick with fresh detections -------------
    def ingest(self, detections: list[Detection]) -> None:
        scene = self.state.scene
        existing_positions = {o.id: o.position for o in scene.objects}
        matches = associate(detections, existing_positions)

        used_indices: set[int] = set()
        for obj in scene.objects:
            if self.state.override_active(obj.id):
                continue  # assisted mode wins for the override's TTL

            idx = matches.get(obj.id)
            if idx is None:
                obj.visible = False
                obj.confidence = max(0.0, obj.confidence - MISSING_CONFIDENCE_DECAY)
                if obj.confidence < MISSING_FLAG_THRESHOLD and obj.held_by is None:
                    self._flag_missing(obj)
                continue

            used_indices.add(idx)
            det = detections[idx]
            smoothed = self.stable.update(obj.id, det)
            if smoothed is None:
                continue
            obj.position = smoothed.position
            obj.confidence = smoothed.confidence
            obj.visible = True
            obj.descriptor = merge_descriptor(obj.descriptor, det)

            new_zone = self.classify_zone(smoothed.position, current=obj.zone)
            if new_zone != obj.zone and smoothed.settled:
                prev = obj.zone
                obj.zone = new_zone
                self.state.emit_nowait(
                    "zone_change",
                    f"{obj.display_label()} moved from {prev} to {new_zone}.",
                    severity="info",
                    metadata={"object_id": obj.id, "from": prev, "to": new_zone},
                )
            obj.last_updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            obj.source = "vision"

        for i, det in enumerate(detections):
            if i in used_indices:
                continue
            obj = self._register_new_object(det)
            scene.objects.append(obj)
            self.state.emit_nowait(
                "object_appeared",
                f"New object detected in {obj.zone}: {obj.display_label()}.",
                severity="warn",
                metadata={"object_id": obj.id},
            )

        scene.object_count = len(scene.objects)
        self.state.world.objects = scene.objects
        self.state.mark_world_dirty()

    def _register_new_object(self, det: Detection) -> ObservedObject:
        oid = _next_object_id(self.state.scene.objects)
        return ObservedObject(
            id=oid,
            descriptor=det.to_descriptor(),
            position=det.position,
            zone=self.classify_zone(det.position),
            visible=True,
            confidence=det.confidence,
            source="vision",
        )

    def _flag_missing(self, obj: ObservedObject) -> None:
        self.state.emit_nowait(
            "object_missing",
            f"{obj.display_label()} is no longer visible.",
            severity="warn",
            metadata={"object_id": obj.id},
        )

    # ---- zones ----------------------------------------------------------
    def classify_zone(self, p: Point, current: str | None = None) -> str:
        """`current`, if given, gets a sticky +HYSTERESIS margin (stays put on a
        border). Any other zone needs the object -HYSTERESIS *inside* its bounds
        to be assigned — an object sitting on a taped line does not flap zones."""
        if current and current != "field":
            cur = next((z for z in self.state.scene.zones if z.id == current), None)
            if cur and cur.bounds.contains(p, margin=HYSTERESIS):
                return current
        for z in self.state.scene.zones:
            if z.bounds.contains(p, margin=-HYSTERESIS):
                return z.id
        return "field"

    def detect_zones_from_frame(self, frame: np.ndarray) -> list[dict]:
        proposed = detect_zones(frame)
        return [{"bounds": p.bounds, "confidence": p.confidence} for p in proposed]

    def define_zone(self, zone_id: str | None, bounds: tuple[float, float, float, float],
                     label: str, source: str = "drawn") -> Zone:
        x, y, w, h = bounds
        zid = zone_id or f"zone_{len(self.state.scene.zones) + 1}"
        zone = Zone(id=zid, label=label, bounds=Rect(x=x, y=y, w=w, h=h), source=source)
        self.state.scene.zones = [z for z in self.state.scene.zones if z.id != zid] + [zone]
        self.state.world.zones = self.state.scene.zones
        self.state.mark_world_dirty()
        return zone

    # ---- full rescan ------------------------------------------------------
    def rebuild_scene(self, frame: np.ndarray) -> None:
        """Full re-discovery: drop tracked object identity and start fresh
        (used by /api/vision/scan)."""
        detections = self.discovery.discover(frame)
        self.stable = StableTracker()
        objects = []
        for i, det in enumerate(detections, start=1):
            objects.append(ObservedObject(
                id=f"obj_{i}",
                descriptor=det.to_descriptor(),
                position=det.position,
                zone=self.classify_zone(det.position),
                visible=True,
                confidence=det.confidence,
                source="vision",
            ))
        self.state.scene.objects = objects
        self.state.scene.object_count = len(objects)
        self.state.scene.scanned_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.state.scene.stable = False
        self.state.world.objects = objects
        self.state.mark_world_dirty()

    def mark_scene_stable(self) -> None:
        self.state.scene.stable = True

    # ---- persistence --------------------------------------------------
    def fingerprint(self, frame: np.ndarray) -> str:
        small = frame[::8, ::8].mean(axis=2).astype(np.uint8)
        return hashlib.sha1(small.tobytes()).hexdigest()[:12]

    def save_profile(self, frame: np.ndarray | None = None) -> None:
        data = {
            "fingerprint": self._camera_fingerprint,
            "zones": [z.model_dump() for z in self.state.scene.zones],
        }
        PROFILE_PATH.write_text(json.dumps(data, indent=2))

    def load_profile(self, frame: np.ndarray | None = None) -> bool:
        if not PROFILE_PATH.exists():
            return False
        try:
            data = json.loads(PROFILE_PATH.read_text())
            zones = [Zone.model_validate(z) for z in data.get("zones", [])]
            self.state.scene.zones = zones
            self.state.world.zones = zones
            self._camera_fingerprint = data.get("fingerprint")
            return True
        except Exception:
            log.exception("failed to load scene_profile.json")
            return False

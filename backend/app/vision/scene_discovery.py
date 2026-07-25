"""Generic scene discovery — find whatever is there, expect nothing.

OWNER: Steven. Working implementation — extend in place.

There is NO preset list of objects. HIVE must work when someone puts objects it has never
seen on a table it has never seen. Segment salient regions generically, then MEASURE each
one. Meaning arrives later, from grounding.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from ..demo.simulator import hsv_to_hex, name_hue
from ..models import Descriptor, ObservedObject, Point, Scene, now_iso

log = logging.getLogger("hive.discovery")

K5 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
K11 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
MIN_AREA_FRAC = 0.0018
MAX_AREA_FRAC = 0.18


@dataclass
class Detection:
    position: Point
    dominant_hsv: tuple[int, int, int]
    color_name: str
    color_hex: str
    area_norm: float
    aspect: float
    circularity: float
    solidity: float
    confidence: float
    bbox: tuple[int, int, int, int]
    _matched_to: str | None = None

    def descriptor(self) -> Descriptor:
        return Descriptor(
            dominant_hsv=self.dominant_hsv,
            color_name=self.color_name,
            color_hex=self.color_hex,
            area_norm=round(self.area_norm, 4),
            aspect=round(self.aspect, 2),
            circularity=round(self.circularity, 2),
            shape_hint="round" if self.circularity > 0.75 else "rectangular",
        )


class Discovery:
    """Adaptive saliency segmentation. Thresholds derive from the frame, so it works
    under lighting it has never seen without any calibration step."""

    def __init__(self) -> None:
        self.saliency_percentile = 82
        self.min_area_frac = MIN_AREA_FRAC
        self.max_area_frac = MAX_AREA_FRAC

    def discover(self, frame_bgr: np.ndarray) -> list[Detection]:
        try:
            frame = cv2.GaussianBlur(frame_bgr, (5, 5), 0)
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            H, W = frame.shape[:2]
            area_total = float(H * W)

            s_thr = max(70, int(np.percentile(hsv[:, :, 1], self.saliency_percentile)))
            v_thr = max(50, int(np.percentile(hsv[:, :, 2], 25)))
            mask = cv2.inRange(hsv, (0, s_thr, v_thr), (179, 255, 255))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, K5)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, K11)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            out: list[Detection] = []
            for c in contours:
                area = cv2.contourArea(c)
                if not (self.min_area_frac * area_total <= area <= self.max_area_frac * area_total):
                    continue
                m = cv2.moments(c)
                if m["m00"] == 0:
                    continue
                cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]

                region = np.zeros((H, W), np.uint8)
                cv2.drawContours(region, [c], -1, 255, -1)
                px = hsv[region > 0]
                if px.size == 0:
                    continue
                h, s, v = (int(x) for x in np.median(px, axis=0))

                x, y, w, hh = cv2.boundingRect(c)
                perim = max(1.0, cv2.arcLength(c, True))
                hull_area = max(1.0, cv2.contourArea(cv2.convexHull(c)))
                circ = float(4 * np.pi * area / (perim**2))
                sol = float(area / hull_area)

                out.append(
                    Detection(
                        position=Point(x=round(cx / W, 4), y=round(cy / H, 4)),
                        dominant_hsv=(h, s, v),
                        color_name=name_hue(h, s, v),
                        color_hex=hsv_to_hex(h, s, v),
                        area_norm=area / area_total,
                        aspect=w / max(1, hh),
                        circularity=min(1.0, circ),
                        solidity=min(1.0, sol),
                        confidence=self._confidence(area / area_total, sol, s),
                        bbox=(x, y, w, hh),
                    )
                )
            out.sort(key=lambda d: -d.area_norm)
            return out[:12]
        except Exception:
            log.exception("discover")
            return []

    @staticmethod
    def _confidence(area_norm: float, solidity: float, sat: int) -> float:
        a = min(1.0, area_norm / 0.02)
        sd = min(1.0, solidity / 0.85)
        st = min(1.0, sat / 140.0)
        # Capped at 0.95 — never report certainty a camera cannot have.
        return round(min(0.95, 0.40 + 0.25 * a + 0.20 * sd + 0.15 * st), 2)


discovery = Discovery()


# ── association: ids are stable, colour is an attribute ─────────────────────


def associate(detections: list[Detection], objects: list[ObservedObject]) -> dict[str, Detection]:
    """Greedy nearest-match on position + hue + area. Keeps ids stable as things move."""
    out: dict[str, Detection] = {}
    used: set[int] = set()
    for obj in objects:
        best_i, best_cost = None, 0.32
        for i, d in enumerate(detections):
            if i in used:
                continue
            pos = obj.position.dist(d.position)
            hue = _hue_dist(obj.descriptor.dominant_hsv[0], d.dominant_hsv[0]) / 180.0
            area = abs(obj.descriptor.area_norm - d.area_norm) / max(0.01, obj.descriptor.area_norm)
            cost = 3.0 * pos + 1.0 * hue + 0.5 * min(area, 1.0)
            if cost < best_cost:
                best_i, best_cost = i, cost
        if best_i is not None:
            used.add(best_i)
            detections[best_i]._matched_to = obj.id
            out[obj.id] = detections[best_i]
    return out


def _hue_dist(a: float, b: float) -> float:
    d = abs(a - b) % 180
    return min(d, 180 - d)


def register_new_object(scene: Scene, det: Detection) -> ObservedObject:
    n = 1 + max((int(o.id.split("_")[-1]) for o in scene.objects if o.id.startswith("obj_")), default=0)
    obj = ObservedObject(
        id=f"obj_{n}",
        descriptor=det.descriptor(),
        position=det.position,
        confidence=det.confidence,
        source="vision",
        first_seen_at=now_iso(),
    )
    scene.objects.append(obj)
    return obj


def rebuild_scene(scene: Scene, detections: list[Detection]) -> Scene:
    """Full rescan: discard tracked ids and re-register from what is visible now."""
    scene.objects = []
    for i, det in enumerate(detections, start=1):
        scene.objects.append(
            ObservedObject(
                id=f"obj_{i}",
                descriptor=det.descriptor(),
                position=det.position,
                confidence=det.confidence,
                source="vision",
            )
        )
    scene.scanned_at = now_iso()
    scene.stable = True
    return scene


# ── temporal stability: the single most valuable filter here ────────────────


@dataclass
class Smoothed:
    position: Point
    confidence: float
    settled: bool


@dataclass
class StableFilter:
    history: dict[str, list[Detection]] = field(default_factory=dict)
    window: int = 6

    def update(self, key: str, det: Detection) -> Smoothed | None:
        buf = self.history.setdefault(key, [])
        buf.append(det)
        if len(buf) > self.window:
            buf.pop(0)
        if len(buf) < 3:
            return None
        pts = np.array([[d.position.x, d.position.y] for d in buf])
        med = np.median(pts, axis=0)
        spread = float(np.std(pts, axis=0).mean())
        return Smoothed(
            position=Point(x=round(float(med[0]), 4), y=round(float(med[1]), 4)),
            confidence=round(det.confidence * (1.0 if spread < 0.03 else 0.7), 2),
            settled=spread < 0.02,
        )


stable_filter = StableFilter()

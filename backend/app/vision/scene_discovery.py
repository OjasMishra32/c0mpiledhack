"""Generic scene discovery — discover, don't expect.

No preset object list, no color->meaning table. Segments salient regions
generically and measures each one. See Steven.md §4 for the full rationale.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.models import Descriptor, Point

KERNEL_3 = np.ones((3, 3), np.uint8)
KERNEL_5 = np.ones((5, 5), np.uint8)
KERNEL_11 = np.ones((11, 11), np.uint8)

MIN_AREA_FRAC = 0.0025   # reject dust
MAX_AREA_FRAC = 0.35     # reject "the whole table lit up"
TYPICAL_AREA = 0.02      # normalized area a "typical" tabletop object occupies

HUE_NAMES = [
    (0, "red"), (15, "orange"), (28, "yellow"), (38, "lime"), (52, "green"),
    (85, "teal"), (100, "cyan"), (115, "blue"), (135, "indigo"), (150, "purple"),
    (165, "magenta"), (179, "red"),
]


def circular_dist(a: float, b: float, period: float = 180.0) -> float:
    d = abs(a - b) % period
    return min(d, period - d)


def name_hue(h: float, s: float, v: float) -> str:
    if v < 55:
        return "black"
    if s < 40:
        return "white" if v > 190 else "grey"
    return min(HUE_NAMES, key=lambda hn: circular_dist(h, hn[0]))[1]


def hsv_to_hex(h: float, s: float, v: float) -> str:
    px = np.uint8([[[h, s, v]]])
    bgr = cv2.cvtColor(px, cv2.COLOR_HSV2BGR)[0][0]
    b, g, r = int(bgr[0]), int(bgr[1]), int(bgr[2])
    return f"#{r:02X}{g:02X}{b:02X}"


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
    contour: np.ndarray = field(repr=False, default=None)

    def shape_hint(self) -> str:
        if self.circularity > 0.75:
            return "round"
        if self.aspect < 0.65 or self.aspect > 1.55:
            return "rectangular"
        if 0.55 <= self.circularity <= 0.75:
            return "rectangular"
        return "irregular"

    def to_descriptor(self) -> Descriptor:
        return Descriptor(
            dominant_hsv=self.dominant_hsv,
            color_name=self.color_name,
            color_hex=self.color_hex,
            area_norm=self.area_norm,
            aspect=self.aspect,
            circularity=self.circularity,
            shape_hint=self.shape_hint(),
        )


@dataclass
class ProposedZone:
    bounds: tuple[float, float, float, float]  # x, y, w, h normalized
    confidence: float


class SceneDiscovery:
    """Generic saliency segmentation. Stateless with respect to object identity —
    tracking/association lives in world_model.py, which owns the id space."""

    def discover(self, frame_bgr: np.ndarray, calibration=None) -> list[Detection]:
        min_area_frac = calibration.state.min_area_frac if calibration else MIN_AREA_FRAC
        max_area_frac = calibration.state.max_area_frac if calibration else MAX_AREA_FRAC
        saliency_bias = calibration.state.saliency_bias if calibration else 0

        frame = cv2.GaussianBlur(frame_bgr, (5, 5), 0)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        H, W = frame.shape[:2]

        s_thr = max(10, min(250, max(70, int(np.percentile(hsv[:, :, 1], 82))) + saliency_bias))
        v_thr = max(50, int(np.percentile(hsv[:, :, 2], 25)))
        mask = cv2.inRange(hsv, (0, s_thr, v_thr), (179, 255, 255))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, KERNEL_5)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, KERNEL_11)

        self._last_mask = mask

        regions = self._split_by_hue(mask, hsv)

        out: list[Detection] = []
        for region in regions:
            area = cv2.contourArea(region)
            if not (min_area_frac * H * W <= area <= max_area_frac * H * W):
                continue
            m = cv2.moments(region)
            if m["m00"] == 0:
                continue
            cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
            px = self._region_pixels(hsv, region, (H, W))
            if px is None or len(px) == 0:
                continue
            h, s, v = np.median(px, axis=0)
            hull = cv2.convexHull(region)
            perim = cv2.arcLength(region, True)
            hull_area = cv2.contourArea(hull)
            x, y, w, h_box = cv2.boundingRect(region)
            aspect = w / h_box if h_box else 1.0
            out.append(Detection(
                position=Point(x=cx / W, y=cy / H),
                dominant_hsv=(int(h), int(s), int(v)),
                color_name=name_hue(h, s, v),
                color_hex=hsv_to_hex(h, s, v),
                area_norm=area / (H * W),
                aspect=aspect,
                circularity=4 * np.pi * area / max(1.0, perim ** 2),
                solidity=area / max(1.0, hull_area),
                confidence=self._confidence(area / (H * W), area / max(1.0, hull_area), s),
                bbox=(x, y, w, h_box),
                contour=region,
            ))
        return out

    def _region_pixels(self, hsv, region, shape) -> np.ndarray | None:
        H, W = shape
        mask = np.zeros((H, W), np.uint8)
        cv2.drawContours(mask, [region], -1, 255, -1)
        ys, xs = np.where(mask == 255)
        if len(xs) == 0:
            return None
        return hsv[ys, xs]

    def _split_by_hue(self, mask: np.ndarray, hsv: np.ndarray) -> list[np.ndarray]:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out: list[np.ndarray] = []
        H, W = mask.shape
        for c in contours:
            area = cv2.contourArea(c)
            if area < MIN_AREA_FRAC * H * W:
                continue
            px = self._region_pixels(hsv, c, (H, W))
            if px is None or len(px) < 20:
                out.append(c)
                continue
            hues = px[:, 0].astype(np.float32)
            hist, edges = np.histogram(hues, bins=18, range=(0, 180))
            peaks = self._find_hue_peaks(hist, edges)
            if len(peaks) < 2:
                out.append(c)
                continue
            # Bimodal enough to be two touching objects of different hue: split by
            # k-means on hue (k=2) within the region, then re-contour each cluster.
            split = self._watershed_split(c, hsv, (H, W))
            out.extend(split if split else [c])
        return out

    def _find_hue_peaks(self, hist: np.ndarray, edges: np.ndarray) -> list[float]:
        total = hist.sum()
        if total == 0:
            return []
        peaks = []
        for i in range(len(hist)):
            if hist[i] < 0.20 * total:
                continue
            if hist[i] >= hist[i - 1] and hist[i] >= hist[(i + 1) % len(hist)]:
                peaks.append((edges[i] + edges[i + 1]) / 2)
        strong = []
        for p in peaks:
            if not any(circular_dist(p, q) < 25 for q in strong):
                strong.append(p)
        return strong

    def _watershed_split(self, contour, hsv, shape) -> list[np.ndarray]:
        H, W = shape
        mask = np.zeros((H, W), np.uint8)
        cv2.drawContours(mask, [contour], -1, 255, -1)
        ys, xs = np.where(mask == 255)
        pts = np.stack([hsv[ys, xs, 0].astype(np.float32)], axis=1)
        if len(pts) < 20:
            return [contour]
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
        _, labels, _ = cv2.kmeans(pts, 2, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
        labels = labels.flatten()
        out = []
        for k in (0, 1):
            sub = np.zeros((H, W), np.uint8)
            sel = labels == k
            sub[ys[sel], xs[sel]] = 255
            sub = cv2.morphologyEx(sub, cv2.MORPH_OPEN, KERNEL_3)
            cs, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not cs:
                continue
            biggest = max(cs, key=cv2.contourArea)
            if cv2.contourArea(biggest) >= MIN_AREA_FRAC * H * W:
                out.append(biggest)
        return out if len(out) == 2 else [contour]

    def _confidence(self, area_norm: float, solidity: float, sat: float) -> float:
        a = min(1.0, area_norm / TYPICAL_AREA)
        sd = min(1.0, solidity / 0.85)
        st = min(1.0, sat / 140.0)
        return round(min(0.95, 0.40 + 0.25 * a + 0.20 * sd + 0.15 * st), 2)

    def last_mask(self) -> np.ndarray | None:
        return getattr(self, "_last_mask", None)


def detect_zones(frame_bgr: np.ndarray) -> list[ProposedZone]:
    """Find taped rectangles via edge detection. Works well with thick black tape
    on a plain surface. Proposes zones; the host accepts, edits, or discards."""
    H, W = frame_bgr.shape[:2]
    frame_area = H * W
    g = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.bilateralFilter(g, 9, 60, 60), 50, 150)
    edges = cv2.dilate(edges, KERNEL_3)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    out: list[ProposedZone] = []
    for c in contours:
        approx = cv2.approxPolyDP(c, 0.03 * cv2.arcLength(c, True), True)
        area = cv2.contourArea(approx)
        if len(approx) == 4 and 0.02 < area / frame_area < 0.30 and cv2.isContourConvex(approx):
            x, y, w, h = cv2.boundingRect(approx)
            quality = min(1.0, area / max(1.0, w * h))  # how rectangular vs its bbox
            out.append(ProposedZone(
                bounds=(x / W, y / H, w / W, h / H),
                confidence=round(quality, 2),
            ))
    return _dedupe_zones(out)


def _dedupe_zones(zones: list[ProposedZone]) -> list[ProposedZone]:
    kept: list[ProposedZone] = []
    for z in sorted(zones, key=lambda z: -z.confidence):
        x, y, w, h = z.bounds
        cx, cy = x + w / 2, y + h / 2
        if any(abs(cx - (k.bounds[0] + k.bounds[2] / 2)) < 0.05
               and abs(cy - (k.bounds[1] + k.bounds[3] / 2)) < 0.05 for k in kept):
            continue
        kept.append(z)
    return kept


class StableTracker:
    """Temporal stability filter — the single most valuable filter in this file.
    Kills per-frame jitter and one-frame glitches before they can fire a false
    deviation."""
    HISTORY = 6

    def __init__(self) -> None:
        self.hist: dict[str, list[Detection]] = {}

    def update(self, obj_id: str, det: Detection) -> "Smoothed | None":
        buf = self.hist.setdefault(obj_id, [])
        buf.append(det)
        del buf[: max(0, len(buf) - self.HISTORY)]
        if len(buf) < 3:
            return None
        pts = np.array([[d.position.x, d.position.y] for d in buf])
        med = np.median(pts, axis=0)
        spread = float(np.std(pts, axis=0).mean())
        return Smoothed(
            position=Point(x=float(med[0]), y=float(med[1])),
            confidence=det.confidence * (1.0 if spread < 0.03 else 0.7),
            settled=spread < 0.02,
        )

    def forget(self, obj_id: str) -> None:
        self.hist.pop(obj_id, None)


@dataclass
class Smoothed:
    position: Point
    confidence: float
    settled: bool


def associate(detections: list[Detection], existing_ids: dict[str, Point]) -> dict[str, int | None]:
    """Greedy nearest-neighbour association on cost = 3.0*position_dist + 1.0*hue_dist
    (area term omitted here; hue+position is sufficient for well-separated tabletop
    objects and avoids pulling in scipy for a 5-object hackathon demo).

    Returns {existing_id: index_into_detections | None}.
    """
    result: dict[str, int | None] = {oid: None for oid in existing_ids}
    used: set[int] = set()
    pairs = []
    for oid, pos in existing_ids.items():
        for i, det in enumerate(detections):
            dist = ((pos.x - det.position.x) ** 2 + (pos.y - det.position.y) ** 2) ** 0.5
            pairs.append((dist, oid, i))
    pairs.sort(key=lambda p: p[0])
    assigned_oid: set[str] = set()
    for dist, oid, i in pairs:
        if oid in assigned_oid or i in used:
            continue
        if dist > 0.35:  # too far to plausibly be the same object between frames
            continue
        result[oid] = i
        used.add(i)
        assigned_oid.add(oid)
    return result

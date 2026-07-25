"""Simulation mode — a first-class world source, not a placeholder.

OWNER: Steven. This is a complete working implementation so the whole stack runs
without a camera; replace/extend in place.

Objects here are GENERATED, not hardcoded — same descriptor path as real discovery.
Simulation must never be the one mode where a fixed object manifest sneaks back in.
"""

from __future__ import annotations

import colorsys
import random
import time

from ..models import Descriptor, ObservedObject, Point, Scene, now_iso

# A generic hue→name function. This is the ONLY colour knowledge in the system and it
# names an arbitrary measured hue; it does not know what a red thing *is*.
HUE_NAMES: list[tuple[int, str]] = [
    (0, "red"), (15, "orange"), (28, "yellow"), (38, "lime"), (52, "green"),
    (85, "teal"), (100, "cyan"), (115, "blue"), (135, "indigo"), (150, "purple"),
    (165, "magenta"), (179, "red"),
]


def circular_hue_dist(a: float, b: float) -> float:
    d = abs(a - b) % 180
    return min(d, 180 - d)


def name_hue(h: float, s: float, v: float) -> str:
    if v < 55:
        return "black"
    if s < 40:
        return "white" if v > 190 else "grey"
    return min(HUE_NAMES, key=lambda hn: circular_hue_dist(h, hn[0]))[1]


def hsv_to_hex(h: float, s: float, v: float) -> str:
    r, g, b = colorsys.hsv_to_rgb((h % 180) / 180.0, s / 255.0, v / 255.0)
    return "#%02X%02X%02X" % (int(r * 255), int(g * 255), int(b * 255))


class Simulator:
    """Drives a virtual workspace and can auto-execute actions."""

    def __init__(self) -> None:
        self.animations: dict[str, tuple[Point, Point, float, float]] = {}
        self.auto_execute: bool = False

    # ── scene generation ────────────────────────────────────────────────────

    def spawn_scene(self, scene: Scene, n: int = 5) -> None:
        """Generate n objects with distinct hues. Ids are synthetic and carry no meaning."""
        scene.objects = []
        # Hues chosen to land squarely on distinct names via name_hue(), so a simulated
        # scene reads like a real table of well-separated objects.
        hues = [2, 18, 28, 55, 115, 150, 95][: max(n, 1)]
        random.shuffle(hues)
        zones = [z.id for z in scene.zones] or ["field"]
        for i, h in enumerate(hues[:n], start=1):
            s, v = random.randint(190, 240), random.randint(170, 225)
            zone = zones[(i - 1) % len(zones)]
            z = scene.zone_by_id(zone)
            base = z.bounds.center if z else Point(x=0.5, y=0.5)
            pos = Point(
                x=round(min(0.97, max(0.03, base.x + random.uniform(-0.05, 0.05))), 3),
                y=round(min(0.97, max(0.03, base.y + random.uniform(-0.05, 0.05))), 3),
            )
            circ = random.uniform(0.55, 0.95)
            scene.objects.append(
                ObservedObject(
                    id=f"obj_{i}",
                    descriptor=Descriptor(
                        dominant_hsv=(h, s, v),
                        color_name=name_hue(h, s, v),
                        color_hex=hsv_to_hex(h, s, v),
                        area_norm=round(random.uniform(0.012, 0.028), 4),
                        aspect=round(random.uniform(0.85, 1.25), 2),
                        circularity=round(circ, 2),
                        shape_hint="round" if circ > 0.75 else "rectangular",
                    ),
                    position=pos,
                    zone=zone,
                    confidence=0.95,
                    source="simulation",
                )
            )
        scene.scanned_at = now_iso()
        scene.stable = True
        scene.labeling_source = "descriptor"
        self.animations.clear()
        self._reclassify(scene)

    # ── motion ──────────────────────────────────────────────────────────────

    def move_to(self, scene: Scene, object_id: str, target: Point, duration: float = 1.5) -> None:
        obj = scene.by_id(object_id)
        if not obj:
            return
        self.animations[object_id] = (obj.position, target, time.monotonic(), duration)

    def move_to_zone(self, scene: Scene, object_id: str, zone_id: str, duration: float = 1.5) -> None:
        z = scene.zone_by_id(zone_id)
        if not z:
            return
        c = z.bounds.center
        jitter = Point(
            x=round(min(0.97, max(0.03, c.x + random.uniform(-0.04, 0.04))), 3),
            y=round(min(0.97, max(0.03, c.y + random.uniform(-0.04, 0.04))), 3),
        )
        self.move_to(scene, object_id, jitter, duration)

    def place(self, scene: Scene, object_id: str, position: Point) -> None:
        """Instant placement (host drag / failure injection)."""
        obj = scene.by_id(object_id)
        if not obj:
            return
        self.animations.pop(object_id, None)
        obj.position = position
        obj.last_updated_at = now_iso()
        self._reclassify(scene)

    def step(self, scene: Scene) -> bool:
        """Advance animations. Returns True if anything changed."""
        if not self.animations:
            return False
        now = time.monotonic()
        changed = False
        for oid, (start, end, t0, dur) in list(self.animations.items()):
            obj = scene.by_id(oid)
            if obj is None:
                self.animations.pop(oid, None)
                continue
            t = min(1.0, (now - t0) / max(dur, 0.01))
            e = 1 - (1 - t) ** 3  # ease-out cubic; objects glide, never teleport
            obj.position = Point(
                x=round(start.x + (end.x - start.x) * e, 4),
                y=round(start.y + (end.y - start.y) * e, 4),
            )
            obj.last_updated_at = now_iso()
            changed = True
            if t >= 1.0:
                self.animations.pop(oid, None)
        if changed:
            self._reclassify(scene)
        return changed

    # ── zones ───────────────────────────────────────────────────────────────

    @staticmethod
    def _reclassify(scene: Scene) -> None:
        for obj in scene.objects:
            obj.zone = classify_zone(scene, obj.position)
        for z in scene.zones:
            z.occupancy = [o.id for o in scene.objects if o.zone == z.id]


def classify_zone(scene: Scene, p: Point, margin: float = 0.0) -> str:
    for z in scene.zones:
        if z.bounds.contains(p, margin):
            return z.id
    return "field"


simulator = Simulator()

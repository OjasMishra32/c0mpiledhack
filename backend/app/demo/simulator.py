"""Simulation mode — a first-class mode, not a placeholder. Lets any one
person demo the whole vision stack alone with no camera, no phones.
See Steven.md §8."""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field

from app.models import Descriptor, ObservedObject, Point
from app.state import HiveState
from app.vision.scene_discovery import hsv_to_hex, name_hue

SHAPES = ["round", "rectangular", "irregular"]
MOVE_SECONDS = 1.5


@dataclass
class SimWorker:
    id: str
    callsign: str
    busy_until: float = 0.0


@dataclass
class _Motion:
    object_id: str
    start: Point
    target: Point
    started_at: float
    duration: float = MOVE_SECONDS
    on_complete: object = None


class Simulator:
    def __init__(self, state: HiveState) -> None:
        self.state = state
        self.workers: list[SimWorker] = []
        self._motions: dict[str, _Motion] = {}

    def spawn_scene(self, n: int = 5) -> list[ObservedObject]:
        """Objects are generated, not hardcoded: randomized hues, positions, shapes,
        run through the identical descriptor path as real detections."""
        objects: list[ObservedObject] = []
        used_hues: list[int] = []
        for i in range(1, n + 1):
            h = self._distinct_hue(used_hues)
            used_hues.append(h)
            s, v = random.randint(150, 230), random.randint(150, 230)
            shape = random.choice(SHAPES)
            aspect = 1.0 if shape == "round" else random.choice([0.6, 1.6])
            circularity = 0.85 if shape == "round" else 0.55
            descriptor = Descriptor(
                dominant_hsv=(h, s, v),
                color_name=name_hue(h, s, v),
                color_hex=hsv_to_hex(h, s, v),
                area_norm=round(random.uniform(0.015, 0.03), 4),
                aspect=aspect,
                circularity=circularity,
                shape_hint=shape,
            )
            pos = Point(x=round(random.uniform(0.1, 0.9), 3), y=round(random.uniform(0.1, 0.9), 3))
            objects.append(ObservedObject(
                id=f"obj_{i}",
                descriptor=descriptor,
                position=pos,
                zone=self._classify(pos),
                visible=True,
                confidence=0.95,
                source="simulation",
            ))
        self.state.scene.objects = objects
        self.state.scene.object_count = len(objects)
        self.state.scene.stable = True
        self.state.world.objects = objects
        self.state.world.mode = "simulation"
        self.state.mark_world_dirty()
        return objects

    def _distinct_hue(self, used: list[int], min_gap: int = 25) -> int:
        for _ in range(20):
            h = random.randint(0, 179)
            if all(min(abs(h - u), 180 - abs(h - u)) >= min_gap for u in used):
                return h
        return random.randint(0, 179)

    def _classify(self, p: Point) -> str:
        for z in self.state.scene.zones:
            if z.bounds.contains(p):
                return z.id
        return "field"

    # ---- interaction --------------------------------------------------
    def drag(self, object_id: str, position: Point) -> None:
        for o in self.state.scene.objects:
            if o.id == object_id:
                o.position = position
                o.zone = self._classify(position)
                o.confidence = 0.95
                o.source = "simulation"
        self.state.world.objects = self.state.scene.objects
        self.state.mark_world_dirty()

    async def auto_execute(self, object_id: str, target: Point) -> None:
        """Animate the object toward the target over ~1.5s, then report completion.
        Never teleports — the interpolation is most of why this looks premium."""
        obj = next((o for o in self.state.scene.objects if o.id == object_id), None)
        if obj is None:
            return
        start = Point(x=obj.position.x, y=obj.position.y)
        t0 = time.time()
        while True:
            elapsed = time.time() - t0
            frac = min(1.0, elapsed / MOVE_SECONDS)
            obj.position = Point(
                x=start.x + (target.x - start.x) * frac,
                y=start.y + (target.y - start.y) * frac,
            )
            obj.zone = self._classify(obj.position)
            self.state.world.objects = self.state.scene.objects
            self.state.mark_world_dirty()
            if frac >= 1.0:
                break
            await asyncio.sleep(0.05)
        self.state.emit_nowait(
            "action_completed",
            f"{obj.display_label()} arrived at {obj.zone}.",
            severity="success",
            metadata={"object_id": object_id},
        )

    def spawn_workers(self, n: int = 5) -> list[SimWorker]:
        """Fake worker sockets for solo testing — highest-leverage thing in this
        file, build it in hour one."""
        callsigns = ["ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO"]
        self.workers = [SimWorker(id=f"worker_{chr(97+i)}", callsign=callsigns[i]) for i in range(n)]
        return self.workers

    async def simulate_worker_cycle(self, worker: SimWorker) -> None:
        """Realistic delay: 1-3s to acknowledge, 3-6s to complete. One worker
        should occasionally be slow to keep timeout logic honest."""
        await asyncio.sleep(random.uniform(1, 3))
        slow = random.random() < 0.2
        await asyncio.sleep(random.uniform(6, 10) if slow else random.uniform(3, 6))

    def inject(self, kind: str, target_id: str | None = None) -> None:
        """Failure injection — lands in the same code path as a real event."""
        objs = self.state.scene.objects
        if kind == "object_removed" and target_id:
            for o in objs:
                if o.id == target_id:
                    o.visible = False
                    o.confidence = 0.0
        elif kind == "wrong_object_move" and target_id:
            zones = self.state.scene.zones
            if zones:
                wrong = random.choice(zones)
                for o in objs:
                    if o.id == target_id:
                        cx = wrong.bounds.x + wrong.bounds.w / 2
                        cy = wrong.bounds.y + wrong.bounds.h / 2
                        o.position = Point(x=cx, y=cy)
                        o.zone = wrong.id
                        o.confidence = 0.9
        elif kind == "verification_regress" and target_id:
            for o in objs:
                if o.id == target_id:
                    o.zone = "field"
        elif kind == "zone_blocked" and target_id:
            for z in self.state.scene.zones:
                if z.id == target_id:
                    z.status = "critical"
        elif kind == "vision_degraded":
            for o in objs:
                o.confidence = round(o.confidence * 0.5, 2)
        self.state.world.objects = objs
        self.state.mark_world_dirty()

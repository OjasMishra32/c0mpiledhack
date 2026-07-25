"""Correction, not setup. Discovery runs with zero configuration; this module
holds the runtime-adjustable knobs the ScenePanel exposes when the environment
fights the defaults (busy surface, dim room, glare-split objects)."""
from __future__ import annotations

from dataclasses import dataclass

from app.vision.scene_discovery import MAX_AREA_FRAC, MIN_AREA_FRAC, SceneDiscovery


@dataclass
class CalibrationState:
    saliency_bias: int = 0       # added to the adaptive saturation percentile threshold
    min_area_frac: float = MIN_AREA_FRAC
    max_area_frac: float = MAX_AREA_FRAC
    show_mask: bool = False


class Calibration:
    """Wraps a SceneDiscovery instance and lets the host nudge its thresholds
    without restarting the vision loop."""

    def __init__(self, discovery: SceneDiscovery) -> None:
        self.discovery = discovery
        self.state = CalibrationState()

    def set_saliency(self, bias: int) -> None:
        self.state.saliency_bias = max(-40, min(40, bias))

    def set_area_bounds(self, min_frac: float | None, max_frac: float | None) -> None:
        if min_frac is not None:
            self.state.min_area_frac = max(0.0005, min_frac)
        if max_frac is not None:
            self.state.max_area_frac = min(0.9, max_frac)

    def toggle_mask(self, show: bool) -> None:
        self.state.show_mask = show

    def merge_regions(self, world_model, ids: list[str]) -> None:
        """Merge N detected objects into one (glare split a single object into
        two blobs). Keeps the first id, drops the rest, averages position."""
        scene = world_model.state.scene
        objs = [o for o in scene.objects if o.id in ids]
        if len(objs) < 2:
            return
        keep, rest = objs[0], objs[1:]
        xs = [o.position.x for o in objs]
        ys = [o.position.y for o in objs]
        keep.position = keep.position.model_copy(update={
            "x": sum(xs) / len(xs), "y": sum(ys) / len(ys),
        })
        drop_ids = {o.id for o in rest}
        scene.objects = [o for o in scene.objects if o.id not in drop_ids]
        world_model.state.world.objects = scene.objects
        world_model.state.mark_world_dirty()

    def delete_region(self, world_model, object_id: str) -> None:
        """Discard a mis-detected region (a hand, a phone, the tape)."""
        scene = world_model.state.scene
        scene.objects = [o for o in scene.objects if o.id != object_id]
        scene.object_count = len(scene.objects)
        world_model.state.world.objects = scene.objects
        world_model.stable.forget(object_id)
        world_model.state.mark_world_dirty()

    def rename(self, world_model, object_id: str, role: str) -> None:
        for o in world_model.state.scene.objects:
            if o.id == object_id:
                o.role = role
                o.role_confidence = 1.0
        world_model.state.mark_world_dirty()

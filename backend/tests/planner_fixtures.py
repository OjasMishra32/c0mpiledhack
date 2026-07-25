"""Scene builders for the planner/scheduler tests.

Kept out of `conftest.py` on purpose: that file is shared with four other workstreams.

Nothing here is a manifest — these are stand-ins for what a camera would have discovered, and
every test builds its own scene so no test can accidentally depend on a fixed object list.
"""

from __future__ import annotations

from app.models import (
    Action,
    Bounds,
    Descriptor,
    HiveState,
    ObservedObject,
    Scene,
    Vec2,
    Worker,
    WorkerStatus,
    Zone,
)

# hue values are what a tracker would measure; the names come from the same table vision uses
HUES = {
    "red": (5, "#C43A2E"),
    "orange": (16, "#E8862B"),
    "yellow": (28, "#E8C72B"),
    "green": (60, "#3FBF57"),
    "cyan": (90, "#2FBFC4"),
    "blue": (110, "#3B6FE0"),
    "purple": (140, "#8B5CF6"),
    "pink": (162, "#F0508A"),
}

ZONE_LAYOUT = [
    ("zone_1", "Inbound Dock", Bounds(x=0.02, y=0.30, w=0.22, h=0.40)),
    ("zone_2", "Pack Station", Bounds(x=0.74, y=0.28, w=0.24, h=0.44)),
    ("zone_3", "Pick Aisle A", Bounds(x=0.30, y=0.02, w=0.40, h=0.20)),
    ("zone_4", "Pick Aisle B", Bounds(x=0.30, y=0.78, w=0.40, h=0.20)),
]

WORKER_LAYOUT = [
    ("worker_a", "ALPHA", "#5AC8FA", "Picker A", (0.10, 0.50), ["zone_1", "zone_3", "zone_2", "field"]),
    ("worker_b", "BRAVO", "#5E5CE6", "Picker B", (0.45, 0.08), ["zone_3", "zone_1", "zone_2", "field"]),
    ("worker_c", "CHARLIE", "#30D158", "Runner C", (0.50, 0.50), ["zone_1", "zone_2", "zone_3", "zone_4", "field"]),
    ("worker_d", "DELTA", "#FF9F0A", "Packer D", (0.88, 0.50), ["zone_2", "zone_4", "field"]),
    ("worker_e", "ECHO", "#FF375F", "Restocker E", (0.45, 0.90), ["zone_4", "zone_2", "field"]),
]


def make_object(
    oid: str,
    color: str = "red",
    x: float = 0.5,
    y: float = 0.5,
    *,
    zone: str = "field",
    semantic_label: str | None = None,
    area: float = 0.020,
    shape: str = "round",
    aspect: float = 1.0,
) -> ObservedObject:
    hue, hex_code = HUES.get(color, (0, "#8E8E93"))
    return ObservedObject(
        id=oid,
        descriptor=Descriptor(
            dominant_hsv=[hue, 210, 190],
            color_name=color,
            color_hex=hex_code,
            area_norm=area,
            aspect=aspect,
            circularity=0.85 if shape == "round" else 0.4,
            shape_hint=shape,
        ),
        position=Vec2(x=x, y=y),
        zone=zone,
        semantic_label=semantic_label,
    )


def make_zones(labels: list[str] | None = None) -> list[Zone]:
    zones = []
    for i, (zid, label, bounds) in enumerate(ZONE_LAYOUT):
        zones.append(Zone(id=zid, label=(labels[i] if labels and i < len(labels) else label), bounds=bounds))
    return zones


def make_scene(objects: list[ObservedObject], zones: list[Zone] | None = None, *, stable: bool = True) -> Scene:
    zs = make_zones() if zones is None else zones
    for z in zs:
        z.occupancy = [o.id for o in objects if o.zone == z.id]
    return Scene(objects=objects, zones=zs, object_count=len(objects), stable=stable)


def make_workers(count: int = 5, *, reachable: dict[str, list[str]] | None = None) -> list[Worker]:
    workers = []
    for wid, callsign, color, role, (x, y), zones in WORKER_LAYOUT[:count]:
        workers.append(
            Worker(
                id=wid,
                display_name=f"Worker {wid[-1].upper()}",
                callsign=callsign,
                color=color,
                role=role,
                status=WorkerStatus.ready.value,
                connected=True,
                available=True,
                position=Vec2(x=x, y=y),
                reachable_zones=(reachable or {}).get(wid, zones),
            )
        )
    return workers


def make_state(scene: Scene, workers: list[Worker], actions: list[Action] | None = None) -> HiveState:
    return HiveState(scene=scene, workers=workers, actions=list(actions or []), locks={})


# ── the flagship rehearsal scene ────────────────────────────────────────────────

FLAGSHIP_GOAL = (
    "Fulfill expedited order 4471 at the Pack Station using the red item and the blue item, "
    "and restock the orange item to Pick Aisle B. "
    "Packing cannot start until the yellow scanner is docked and the green item is staged."
)


def flagship_scene() -> Scene:
    return make_scene(
        [
            make_object("obj_1", "red", 0.35, 0.45, semantic_label="red cup"),
            make_object("obj_2", "blue", 0.40, 0.62, semantic_label="blue cup"),
            make_object("obj_3", "green", 0.55, 0.35, semantic_label="green cup"),
            make_object("obj_4", "yellow", 0.60, 0.66, semantic_label="yellow scanner"),
            make_object("obj_5", "orange", 0.25, 0.70, semantic_label="orange bin"),
        ]
    )

"""HIVE data model. Mirrors docs/CONTRACTS.md and frontend/src/types/hive.ts.

Field names are snake_case on BOTH sides of the wire. No mapping layer, no camelCase.
If you change something here, change hive.ts and CONTRACTS.md in the same commit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ── enums (plain string literals; they serialize as-is) ──────────────────────

class WorkerStatus(str, Enum):
    disconnected = "disconnected"
    joining = "joining"
    ready = "ready"
    assigned = "assigned"
    executing = "executing"
    blocked = "blocked"
    paused = "paused"
    unavailable = "unavailable"
    emergency = "emergency"


class ActionStatus(str, Enum):
    queued = "queued"
    available = "available"
    assigned = "assigned"
    dispatched = "dispatched"
    acknowledged = "acknowledged"
    executing = "executing"
    awaiting_verification = "awaiting_verification"
    verified = "verified"
    failed = "failed"
    blocked = "blocked"
    cancelled = "cancelled"
    recovery = "recovery"


class ActionType(str, Enum):
    pick_up = "pick_up"
    move_to_zone = "move_to_zone"
    place_in_zone = "place_in_zone"
    place_on = "place_on"
    hold = "hold"
    release = "release"
    inspect = "inspect"
    standby = "standby"


class PredicateType(str, Enum):
    object_in_zone = "object_in_zone"
    object_near_object = "object_near_object"
    object_stacked_on = "object_stacked_on"
    object_held_by = "object_held_by"
    worker_ready = "worker_ready"
    worker_idle = "worker_idle"
    object_visible = "object_visible"
    sequence_completed = "sequence_completed"
    all_objects_in_zone = "all_objects_in_zone"
    worker_acknowledged = "worker_acknowledged"
    manually_verified = "manually_verified"


GoalStatus = Literal["draft", "compiling", "compiled", "executing", "paused", "completed", "failed", "aborted"]
PlanSource = Literal["llm", "template", "demo_script", "manual"]
WorldMode = Literal["live", "assisted", "simulation"]
Severity = Literal["debug", "info", "warn", "critical", "success"]
EvidenceKind = Literal[
    "vision", "vlm", "worker_report", "host_override", "simulation", "timing",
    "inference", "system",
]
ZoneStatus = Literal["unknown", "pending", "active", "satisfied", "blocked"]
ExecutionStatus = Literal["idle", "planning", "executing", "paused", "completed", "emergency"]

SUPPORTED_ACTIONS: list[str] = [
    "pick_up", "move_to_zone", "place_in_zone", "place_on", "hold", "release", "inspect",
]

# Evidence weights. Single source of truth — verifier.py imports these, never redefines.
EVIDENCE_WEIGHTS: dict[str, float] = {
    "vision": 0.60,
    "vlm": 0.55,
    "worker_report": 0.30,
    "simulation": 0.95,
    "host_override": 1.00,
    "timing": 0.10,
    "inference": 0.15,
    # Derived from HIVE's own verified records (e.g. "all prerequisites verified").
    # Not a sensor reading, so it is not hedged like one.
    "system": 0.90,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── geometry ────────────────────────────────────────────────────────────────


class _Base(BaseModel):
    model_config = ConfigDict(use_enum_values=True)


class Point(_Base):
    x: float = 0.5
    y: float = 0.5

    def dist(self, other: "Point") -> float:
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5


class Rect(_Base):
    x: float
    y: float
    w: float
    h: float

    def contains(self, p: Point, margin: float = 0.0) -> bool:
        return (
            self.x - margin <= p.x <= self.x + self.w + margin
            and self.y - margin <= p.y <= self.y + self.h + margin
        )

    @property
    def center(self) -> Point:
        return Point(x=self.x + self.w / 2, y=self.y + self.h / 2)


# ── perception ──────────────────────────────────────────────────────────────


class Descriptor(_Base):
    """Everything here is MEASURED from the frame. Nothing is configured."""

    dominant_hsv: tuple[int, int, int] = (0, 0, 0)
    color_name: str = "unknown"
    color_hex: str = "#888888"
    area_norm: float = 0.0
    aspect: float = 1.0
    circularity: float = 0.0
    shape_hint: str = "irregular"


class ObservedObject(_Base):
    id: str
    descriptor: Descriptor = Field(default_factory=Descriptor)
    position: Point = Field(default_factory=Point)
    zone: str = "field"
    visible: bool = True
    confidence: float = 0.9
    first_seen_at: str = Field(default_factory=now_iso)
    last_updated_at: str = Field(default_factory=now_iso)
    source: str = "simulation"  # vision | vlm | simulation | host_override

    # assigned by semantic labeling + grounding, not by perception
    semantic_label: str | None = None
    role: str | None = None
    role_confidence: float = 0.0

    # runtime
    held_by: str | None = None
    stacked_on: str | None = None
    locked_by: str | None = None

    def display_label(self) -> str:
        if self.role:
            return self.role
        if self.semantic_label:
            return self.semantic_label
        d = self.descriptor
        return f"{d.color_name} {d.shape_hint} object".strip()


class Zone(_Base):
    id: str
    label: str
    bounds: Rect
    occupancy: list[str] = Field(default_factory=list)
    status: ZoneStatus = "unknown"
    source: str = "drawn"  # detected | drawn | inferred


class Scene(_Base):
    objects: list[ObservedObject] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)
    scanned_at: str = Field(default_factory=now_iso)
    labeling_source: str = "none"  # vlm | descriptor | none
    stable: bool = True
    object_count: int = 0

    def by_id(self, oid: str) -> ObservedObject | None:
        return next((o for o in self.objects if o.id == oid), None)

    def zone_by_id(self, zid: str) -> Zone | None:
        return next((z for z in self.zones if z.id == zid), None)

    def zone_label(self, zid: str) -> str:
        z = self.zone_by_id(zid)
        return z.label if z else ("the open floor" if zid == "field" else zid)

    def label_of(self, oid: str) -> str:
        o = self.by_id(oid)
        return o.display_label() if o else oid

    @property
    def visible_objects(self) -> list["ObservedObject"]:
        return [o for o in self.objects if o.visible]


class WorldState(_Base):
    """Camera/vision status. The scene itself lives on HiveState.scene."""

    # Convenience mirror of scene.objects that the vision pipeline writes. Excluded from
    # serialization so the wire payload keeps exactly one source of truth for objects.
    objects: list[ObservedObject] = Field(default_factory=list, exclude=True)
    mode: WorldMode = "simulation"
    camera_online: bool = False
    vision_fps: float = 0.0
    last_frame_at: str | None = None
    vlm_fast_online: bool = False
    vlm_reason_online: bool = False
    vlm_hz: float = 0.0
    narration: str | None = None
    people: list[dict[str, Any]] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)


# ── workers ─────────────────────────────────────────────────────────────────


class Worker(_Base):
    id: str
    display_name: str
    callsign: str
    color: str
    role: str = "Operator"
    status: WorkerStatus = "disconnected"
    connected: bool = False
    available: bool = True
    current_action_id: str | None = None
    reachable_zones: list[str] = Field(default_factory=list)
    supported_actions: list[str] = Field(default_factory=lambda: list(SUPPORTED_ACTIONS))
    position: Point = Field(default_factory=Point)
    last_seen_at: str = Field(default_factory=now_iso)
    assignment_count: int = 0
    confidence: float = 1.0
    session_token: str | None = None

    def public_dict(self) -> dict[str, Any]:
        """Never leak session_token outward."""
        d = self.model_dump()
        d.pop("session_token", None)
        return d


# ── plan ────────────────────────────────────────────────────────────────────


class Predicate(_Base):
    type: PredicateType
    subject: str
    object: str | None = None
    tolerance: float | None = 0.12  # None where proximity is meaningless


class Evidence(_Base):
    kind: EvidenceKind
    confidence: float = 1.0
    weight: float = 0.0
    detail: str = ""
    at: str = Field(default_factory=now_iso)

    def model_post_init(self, __ctx: Any) -> None:  # noqa: D105
        if not self.weight:
            self.weight = EVIDENCE_WEIGHTS.get(self.kind, 0.1)


class Instruction(_Base):
    id: str
    action_id: str
    worker_id: str
    display_text: str
    spoken_text: str
    detail_text: str = ""
    urgency: Literal["normal", "high", "critical"] = "normal"
    expected_duration_seconds: int = 12
    requires_verification: bool = True
    correction_text: str | None = None
    issued_at: str = Field(default_factory=now_iso)


class Action(_Base):
    id: str
    type: str  # validated by planner.validator, not by the model
    description: str
    object_id: str | None = None
    target_object_id: str | None = None
    target_zone: str | None = None
    assigned_worker_id: str | None = None
    assignment_reason: str = ""
    dependencies: list[str] = Field(default_factory=list)
    status: ActionStatus = "queued"
    priority: int = 50
    timeout_seconds: int = 25
    expected_predicates: list[Predicate] = Field(default_factory=list)
    instruction: Instruction | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = 0.0
    retry_count: int = 0
    max_retries: int = 2
    attempt: int = 0
    is_recovery: bool = False
    origin_zone: str | None = None
    blocked_reason: str | None = None
    lock_targets: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    dispatched_at: str | None = None
    completed_at: str | None = None

    @property
    def terminal(self) -> bool:
        return self.status in ("verified", "cancelled")


class Goal(_Base):
    id: str = "goal_1"
    raw_text: str = ""
    normalized_intent: str = ""
    status: GoalStatus = "draft"
    success_predicates: list[Predicate] = Field(default_factory=list)
    plan_source: PlanSource = "template"
    planner_notes: str = ""
    warnings: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)


# ── events & metrics ────────────────────────────────────────────────────────


class Event(_Base):
    id: str
    seq: int
    timestamp: str
    type: str
    severity: Severity = "info"
    actor: str = "hive"
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunMetrics(_Base):
    actions_total: int = 0
    actions_verified: int = 0
    parallel_peak: int = 0
    recoveries: int = 0
    reassignments: int = 0
    deviations: int = 0
    conflicts: int = 0
    avg_confidence: float = 0.0
    worker_idle_seconds: float = 0.0
    started_at: str | None = None
    completed_at: str | None = None
    elapsed_seconds: float = 0.0


# ── transport ───────────────────────────────────────────────────────────────


class InboundMessage(_Base):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    worker_id: str | None = None
    role: str = "host"


class Envelope(_Base):
    type: str
    payload: Any = None
    ts: str = Field(default_factory=now_iso)
    seq: int = 0


# Aliases so modules authored against the contract's alternate names keep working.
Vec2 = Point
Bounds = Rect


class PlannerState(_Base):
    """List-shaped snapshot the planner and scheduler read.

    The live `HiveState` (app/state.py) keys workers and actions by id because everything
    else looks them up that way; these subsystems iterate. `orchestrator._SchedulerView`
    adapts the real state onto this shape, and tests construct it directly.
    """

    mode: str = "simulation"
    scenario_id: str | None = None
    goal: Goal | None = None
    workers: list[Worker] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    scene: Scene = Field(default_factory=Scene)
    locks: dict[str, str] = Field(default_factory=dict)
    events: list[Event] = Field(default_factory=list)
    lexicon: dict[str, str] = Field(default_factory=dict)

    @property
    def objects(self) -> list[ObservedObject]:
        return self.scene.objects

    @property
    def zones(self) -> list[Zone]:
        return self.scene.zones

    def worker_by_id(self, worker_id: str) -> Worker | None:
        return next((w for w in self.workers if w.id == worker_id), None)

    def action_by_id(self, action_id: str) -> Action | None:
        return next((a for a in self.actions if a.id == action_id), None)


# Name the planner suite was authored against.
HiveState = PlannerState


# Alias: the vision workstream calls this one `utc_now`.
utc_now = now_iso

"""Shared data model. Must stay structurally identical to frontend/src/types/hive.ts —
snake_case field names on both sides. See docs/CONTRACTS.md, which is authoritative."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


class GoalStatus(str, Enum):
    draft = "draft"
    compiling = "compiling"
    compiled = "compiled"
    executing = "executing"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    aborted = "aborted"


class PlanSource(str, Enum):
    llm = "llm"
    template = "template"
    demo_script = "demo_script"
    manual = "manual"


class WorldMode(str, Enum):
    live = "live"
    assisted = "assisted"
    simulation = "simulation"


class Severity(str, Enum):
    debug = "debug"
    info = "info"
    warn = "warn"
    critical = "critical"
    success = "success"


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


class EvidenceKind(str, Enum):
    vision = "vision"
    vlm = "vlm"
    worker_report = "worker_report"
    host_override = "host_override"
    simulation = "simulation"
    timing = "timing"
    inference = "inference"


class PerceptionTier(str, Enum):
    cv = "cv"
    vlm_fast = "vlm_fast"
    vlm_reason = "vlm_reason"


# Evidence kind -> weight, per docs/CONTRACTS.md §2. Do not redefine elsewhere.
EVIDENCE_WEIGHTS: dict[EvidenceKind, float] = {
    EvidenceKind.vision: 0.60,
    EvidenceKind.vlm: 0.55,
    EvidenceKind.worker_report: 0.30,
    EvidenceKind.simulation: 0.95,
    EvidenceKind.host_override: 1.00,
    EvidenceKind.timing: 0.10,
    EvidenceKind.inference: 0.15,
}


class Point(BaseModel):
    x: float
    y: float


class Rect(BaseModel):
    x: float
    y: float
    w: float
    h: float


class ObjectDescriptor(BaseModel):
    dominant_hsv: tuple[int, int, int] = (0, 0, 0)
    color_name: str = "grey"
    color_hex: str = "#888888"
    area_norm: float = 0.0
    aspect: float = 1.0
    circularity: float = 0.0
    shape_hint: str = "irregular"


class ObservedObject(BaseModel):
    id: str
    descriptor: ObjectDescriptor = Field(default_factory=ObjectDescriptor)
    position: Point = Field(default_factory=lambda: Point(x=0.5, y=0.5))
    zone: str = "field"
    visible: bool = True
    confidence: float = 0.5
    first_seen_at: str = Field(default_factory=now_iso)
    last_updated_at: str = Field(default_factory=now_iso)
    source: str = "simulation"

    semantic_label: str | None = None
    role: str | None = None
    role_confidence: float | None = None

    held_by: str | None = None
    stacked_on: str | None = None
    locked_by: str | None = None

    def display_label(self) -> str:
        return self.role or self.semantic_label or f"{self.descriptor.color_name} {self.descriptor.shape_hint} object"


class Zone(BaseModel):
    id: str
    label: str
    bounds: Rect
    occupancy: list[str] = Field(default_factory=list)
    status: str = "unknown"
    source: str = "drawn"


class Scene(BaseModel):
    objects: list[ObservedObject] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)
    scanned_at: str = Field(default_factory=now_iso)
    object_count: int = 0
    labeling_source: str = "none"
    stable: bool = True

    def by_id(self, object_id: str) -> ObservedObject | None:
        return next((o for o in self.objects if o.id == object_id), None)

    def zone_label(self, zone_id: str) -> str:
        z = next((z for z in self.zones if z.id == zone_id), None)
        return z.label if z else zone_id


class Worker(BaseModel):
    id: str
    display_name: str
    callsign: str
    color: str
    status: WorkerStatus = WorkerStatus.disconnected
    connected: bool = False
    available: bool = True
    current_action_id: str | None = None
    reachable_zones: list[str] = Field(default_factory=list)
    role: str = ""
    supported_actions: list[str] = Field(default_factory=list)
    position: Point = Field(default_factory=lambda: Point(x=0.5, y=0.5))
    last_seen_at: str = Field(default_factory=now_iso)
    assignment_count: int = 0
    confidence: float = 1.0
    session_token: str | None = None

    def public_dict(self) -> dict[str, Any]:
        d = self.model_dump()
        d.pop("session_token", None)
        return d


WORKER_SEED: list[tuple[str, str, str, str]] = [
    ("worker_a", "Worker A", "ALPHA", "#5AC8FA"),
    ("worker_b", "Worker B", "BRAVO", "#5E5CE6"),
    ("worker_c", "Worker C", "CHARLIE", "#30D158"),
    ("worker_d", "Worker D", "DELTA", "#FF9F0A"),
    ("worker_e", "Worker E", "ECHO", "#FF375F"),
]


class Predicate(BaseModel):
    type: PredicateType
    subject: str
    object: str | None = None
    tolerance: float = 0.08


class Evidence(BaseModel):
    kind: EvidenceKind
    confidence: float
    weight: float
    detail: str = ""
    at: str = Field(default_factory=now_iso)


class Instruction(BaseModel):
    id: str
    action_id: str
    worker_id: str
    display_text: str
    spoken_text: str
    detail_text: str = ""
    urgency: str = "normal"
    expected_duration_seconds: int = 12
    requires_verification: bool = True
    correction_text: str | None = None
    issued_at: str = Field(default_factory=now_iso)


class Action(BaseModel):
    id: str
    type: ActionType
    description: str
    object_id: str | None = None
    target_object_id: str | None = None
    target_zone: str | None = None
    assigned_worker_id: str | None = None
    assignment_reason: str = ""
    dependencies: list[str] = Field(default_factory=list)
    status: ActionStatus = ActionStatus.queued
    priority: int = 50
    timeout_seconds: int = 25
    expected_predicates: list[Predicate] = Field(default_factory=list)
    instruction: Instruction | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    attempt: int = 0
    retry_count: int = 0
    max_retries: int = 2
    is_recovery: bool = False
    lock_targets: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None
    created_at: str = Field(default_factory=now_iso)
    dispatched_at: str | None = None
    completed_at: str | None = None


class Goal(BaseModel):
    id: str
    raw_text: str
    normalized_intent: str = ""
    status: GoalStatus = GoalStatus.draft
    success_predicates: list[Predicate] = Field(default_factory=list)
    plan_source: PlanSource = PlanSource.template
    planner_notes: str = ""
    created_at: str = Field(default_factory=now_iso)


class Event(BaseModel):
    id: str
    seq: int
    timestamp: str = Field(default_factory=now_iso)
    type: str
    severity: Severity = Severity.info
    actor: str = "hive"
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorldState(BaseModel):
    mode: WorldMode = WorldMode.simulation
    objects: list[ObservedObject] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)
    camera_online: bool = False
    vision_fps: float = 0.0
    last_frame_at: str = Field(default_factory=now_iso)


class RunMetrics(BaseModel):
    actions_total: int = 0
    actions_verified: int = 0
    parallel_peak: int = 0
    recoveries: int = 0
    reassignments: int = 0
    deviations: int = 0
    avg_confidence: float = 0.0
    worker_idle_seconds: float = 0.0
    started_at: str | None = None
    completed_at: str | None = None


class VerificationResult(BaseModel):
    score: float
    verified: bool
    evidence: list[Evidence]
    summary: str

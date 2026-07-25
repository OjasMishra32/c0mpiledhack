"""HIVE shared data models — the Pydantic source of truth (docs/CONTRACTS.md §1–§2).

Field names are snake_case and structurally identical to `frontend/src/types/hive.ts`.

Nothing in here is scenario-specific. Objects and zones are *discovered* at runtime with
measured descriptors; meaning arrives later from grounding and lives in `role`. There is no
object manifest anywhere in HIVE.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ── §1 Enums ────────────────────────────────────────────────────────────────────
# Strings, not ints. They serialize as-is.


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


SUPPORTED_ACTIONS: list[str] = [t.value for t in ActionType]

EVIDENCE_WEIGHTS: dict[str, float] = {
    EvidenceKind.vision.value: 0.60,
    EvidenceKind.vlm.value: 0.55,
    EvidenceKind.worker_report.value: 0.30,
    EvidenceKind.host_override.value: 1.00,
    EvidenceKind.simulation.value: 0.95,
    EvidenceKind.timing.value: 0.10,
    EvidenceKind.inference.value: 0.15,
}


# ── §2 Core models ──────────────────────────────────────────────────────────────


class Base(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=True)


class Vec2(Base):
    x: float
    y: float


class Bounds(Base):
    """Normalized rectangle, origin top-left of the camera frame. There are no pixels here."""

    x: float
    y: float
    w: float
    h: float

    def contains(self, p: Vec2) -> bool:
        return self.x <= p.x <= self.x + self.w and self.y <= p.y <= self.y + self.h

    @property
    def center(self) -> Vec2:
        return Vec2(x=self.x + self.w / 2, y=self.y + self.h / 2)


class Descriptor(Base):
    """Everything here is MEASURED by the vision pipeline. Nothing is configured."""

    dominant_hsv: list[int] = Field(default_factory=lambda: [0, 0, 0])
    color_name: str = "unknown"
    color_hex: str = "#8E8E93"
    area_norm: float = 0.0
    aspect: float = 1.0
    circularity: float = 0.0
    shape_hint: str = "irregular"  # round | rectangular | irregular


class ObservedObject(Base):
    id: str

    # measured
    descriptor: Descriptor = Field(default_factory=Descriptor)
    position: Vec2 = Field(default_factory=lambda: Vec2(x=0.5, y=0.5))
    zone: str = "field"
    visible: bool = True
    confidence: float = 1.0
    first_seen_at: datetime = Field(default_factory=utc_now)
    last_updated_at: datetime = Field(default_factory=utc_now)
    source: str = "vision"  # vision | simulation | host_override

    # assigned by semantic labeling + grounding
    semantic_label: str | None = None
    role: str | None = None
    role_confidence: float | None = None

    # runtime
    held_by: str | None = None
    stacked_on: str | None = None
    locked_by: str | None = None

    def display_label(self) -> str:
        """role ?? semantic_label ?? "{color_name} {shape_hint} object"."""
        if self.role:
            return self.role
        if self.semantic_label:
            return self.semantic_label
        return f"{self.descriptor.color_name} {self.descriptor.shape_hint} object"


class Zone(Base):
    id: str
    label: str
    bounds: Bounds
    occupancy: list[str] = Field(default_factory=list)
    status: str = "pending"  # unknown | pending | active | satisfied | blocked
    source: str = "detected"  # detected | drawn | inferred


class Scene(Base):
    """The discovery result. Produced on *Scan Scene*, refreshed continuously."""

    objects: list[ObservedObject] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)
    scanned_at: datetime = Field(default_factory=utc_now)
    object_count: int = 0
    labeling_source: str = "descriptor"  # vlm | descriptor | none
    stable: bool = True

    def by_id(self, object_id: str) -> ObservedObject | None:
        return next((o for o in self.objects if o.id == object_id), None)

    def zone_by_id(self, zone_id: str) -> Zone | None:
        return next((z for z in self.zones if z.id == zone_id), None)

    def zone_label(self, zone_id: str) -> str:
        zone = self.zone_by_id(zone_id)
        return zone.label if zone else ("the floor" if zone_id == "field" else zone_id)

    @property
    def visible_objects(self) -> list[ObservedObject]:
        return [o for o in self.objects if o.visible]


class Worker(Base):
    id: str
    display_name: str
    callsign: str
    color: str
    status: str = WorkerStatus.disconnected.value
    connected: bool = False
    available: bool = True
    current_action_id: str | None = None
    reachable_zones: list[str] = Field(default_factory=lambda: ["field"])
    role: str | None = None
    supported_actions: list[str] = Field(default_factory=lambda: list(SUPPORTED_ACTIONS))
    position: Vec2 = Field(default_factory=lambda: Vec2(x=0.5, y=0.5))
    last_seen_at: datetime | None = None
    assignment_count: int = 0
    confidence: float = 1.0
    # never sent to the host UI, never logged — excluded from every model_dump()
    session_token: str | None = Field(default=None, repr=False, exclude=True)


class Predicate(Base):
    type: str
    subject: str
    object: str | None = None
    tolerance: float | None = None


class Instruction(Base):
    """What lands on a phone. Generated at dispatch time, never before.

    `id` MUST be unique per (action, attempt) — the worker client keys speech off it.
    """

    id: str
    action_id: str
    worker_id: str
    display_text: str
    spoken_text: str
    detail_text: str | None = None
    urgency: str = "normal"  # normal | high | critical
    expected_duration_seconds: int = 12
    requires_verification: bool = True
    correction_text: str | None = None
    issued_at: datetime = Field(default_factory=utc_now)


class Action(Base):
    id: str
    type: str
    description: str
    object_id: str | None = None
    target_object_id: str | None = None
    target_zone: str | None = None
    assigned_worker_id: str | None = None
    assignment_reason: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    status: str = ActionStatus.queued.value
    priority: int = 70
    timeout_seconds: int = 25
    expected_predicates: list[Predicate] = Field(default_factory=list)
    instruction: Instruction | None = None
    retry_count: int = 0
    max_retries: int = 2
    is_recovery: bool = False
    blocked_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    dispatched_at: datetime | None = None
    completed_at: datetime | None = None
    lock_targets: list[str] = Field(default_factory=list)


class Evidence(Base):
    kind: str
    confidence: float
    weight: float
    detail: str | None = None
    at: datetime = Field(default_factory=utc_now)


class Goal(Base):
    id: str = "goal_1"
    raw_text: str
    normalized_intent: str = ""
    status: str = GoalStatus.draft.value
    success_predicates: list[Predicate] = Field(default_factory=list)
    plan_source: str = PlanSource.template.value
    planner_notes: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class Event(Base):
    id: str
    seq: int
    timestamp: datetime = Field(default_factory=utc_now)
    type: str
    severity: str = Severity.info.value
    actor: str = "hive"
    message: str = ""
    metadata: dict = Field(default_factory=dict)


class WorldState(Base):
    mode: str = WorldMode.simulation.value
    objects: list[ObservedObject] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)
    camera_online: bool = False
    vision_fps: float = 0.0
    last_frame_at: datetime | None = None


class HiveState(Base):
    """Provisional shape of the snapshot the host renders.

    `orchestrator.py` owns the real one (Ojas). The planner and scheduler only read the
    fields below and duck-type them, so a richer HiveState is a drop-in replacement.
    """

    mode: str = WorldMode.simulation.value
    scenario_id: str | None = None
    goal: Goal | None = None
    workers: list[Worker] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    scene: Scene = Field(default_factory=Scene)
    locks: dict[str, str] = Field(default_factory=dict)  # lock_target -> action_id
    events: list[Event] = Field(default_factory=list)
    lexicon: dict[str, str] = Field(default_factory=dict)

    # convenience accessors the scheduler uses
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

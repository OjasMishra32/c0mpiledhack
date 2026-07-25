"""Single source of truth.

Extends the vision-workstream slice (world/scene/host_overrides — Steven's original
state.py) with the orchestration core (Ojas.md §4) that everything else duck-types
against: `workers`, `actions`, `goal`, `locks` (see `scheduler.SchedulerState` and
`planner.base.PlanContext`). Only `orchestrator.py` mutates `Action.status` /
`Worker.status` / `locks` (docs/CONTRACTS.md §1).

`Action` has no `evidence` field in the shared model (docs/CONTRACTS.md §2), so
per-action evidence accumulated before a predicate re-check (a worker's completed
report, a host override) lives in `state.evidence`, keyed by action id.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from app.models import (
    Action,
    Event,
    Evidence,
    Goal,
    Scene,
    Worker,
    WorkerStatus,
    WorldState,
)


@dataclass
class HostOverride:
    object_id: str
    expires_at: float


@dataclass
class InboundMessage:
    type: str
    payload: dict
    worker_id: str | None = None
    role: str = "host"


WORKER_SEED: list[tuple[str, str, str, str]] = [
    ("worker_a", "Worker A", "ALPHA", "#5AC8FA"),
    ("worker_b", "Worker B", "BRAVO", "#5E5CE6"),
    ("worker_c", "Worker C", "CHARLIE", "#30D158"),
    ("worker_d", "Worker D", "DELTA", "#FF9F0A"),
    ("worker_e", "Worker E", "ECHO", "#FF375F"),
]


class HiveState:
    def __init__(self) -> None:
        # ── vision slice (Steven) ──
        self.world = WorldState()
        self.scene = Scene()
        self.events: list[Event] = []
        self.host_overrides: dict[str, HostOverride] = {}
        self._seq = 0
        self._lock = asyncio.Lock()
        self._world_dirty = False

        # ── orchestration core (Ojas.md §4) ──
        self.workers: list[Worker] = [
            Worker(id=wid, display_name=name, callsign=callsign, color=color,
                   reachable_zones=["zone_1", "zone_2", "zone_3", "zone_4", "field"])
            for wid, name, callsign, color in WORKER_SEED
        ]
        self.actions: list[Action] = []
        self.goal: Goal | None = None
        self.locks: dict[str, str] = {}
        self.scenario_id: str | None = None
        self.lexicon: dict[str, str] = {}
        self.inbox: list = []
        self.execution_status: str = "idle"
        self.escalation_armed: bool = False
        self.evidence: dict[str, list[Evidence]] = {}

    def worker_by_id(self, worker_id: str) -> Worker | None:
        return next((w for w in self.workers if w.id == worker_id), None)

    def action_by_id(self, action_id: str) -> Action | None:
        return next((a for a in self.actions if a.id == action_id), None)

    def mark_world_dirty(self) -> None:
        self._world_dirty = True

    def consume_dirty(self) -> bool:
        was = self._world_dirty
        self._world_dirty = False
        return was

    async def emit(self, type: str, message: str, severity: str = "info",
                    actor: str = "hive", metadata: dict | None = None) -> Event:
        async with self._lock:
            self._seq += 1
            evt = Event(
                id=f"evt_{self._seq:06d}",
                seq=self._seq,
                type=type,
                severity=severity,
                actor=actor,
                message=message,
                metadata=metadata or {},
            )
            self.events.append(evt)
            self.events = self.events[-200:]
        from app.websocket_manager import ws  # lazy import: avoid circular import

        await ws.broadcast("event", evt.model_dump(mode="json"))
        return evt

    def emit_nowait(self, type: str, message: str, severity: str = "info",
                     actor: str = "vision", metadata: dict | None = None) -> None:
        """Fire-and-forget emit for hot paths (the vision tick) that aren't async."""
        self._seq += 1
        evt = Event(
            id=f"evt_{self._seq:06d}",
            seq=self._seq,
            type=type,
            severity=severity,
            actor=actor,
            message=message,
            metadata=metadata or {},
        )
        self.events.append(evt)
        self.events = self.events[-200:]

    def set_host_override(self, object_id: str, ttl_seconds: float = 20.0) -> None:
        self.host_overrides[object_id] = HostOverride(object_id, time.time() + ttl_seconds)

    def override_active(self, object_id: str) -> bool:
        ov = self.host_overrides.get(object_id)
        if ov is None:
            return False
        if time.time() >= ov.expires_at:
            del self.host_overrides[object_id]
            return False
        return True

    async def reset(self, scenario_id: str | None = None) -> None:
        """Rebuild orchestration state for a fresh run without dropping sockets.

        Note: no scenario library (`demo/scenarios.py`) exists yet — this only
        resets the orchestration fields (goal/actions/locks/workers). The scene
        itself is Steven's `Simulator`/vision pipeline's responsibility.
        """
        self.scenario_id = scenario_id or self.scenario_id
        self.goal = None
        self.actions = []
        self.locks = {}
        self.evidence = {}
        self.execution_status = "idle"
        for w in self.workers:
            w.status = WorkerStatus.ready.value if w.connected else WorkerStatus.disconnected.value
            w.available = True
            w.current_action_id = None
            w.assignment_count = 0
        self.events = []
        self._seq = 0
        from app.websocket_manager import ws  # lazy import: avoid circular import

        await ws.broadcast_snapshot()
        await self.emit("system_reset", "HIVE reset. Collective standing by.", severity="info")


state = HiveState()

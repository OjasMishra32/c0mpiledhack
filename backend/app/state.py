"""HiveState — the single source of truth.

Rules that hold this together:
  * Only the orchestrator tick mutates Action.status / Worker.status / locks.
  * emit() is the ONLY place event.seq increments. Ordering is stable everywhere.
  * reset() rebuilds state from a scenario WITHOUT dropping sockets.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from .config import settings
from .demo.scenarios import DEFAULT_SCENARIO, SCENARIOS, WORKER_SEATS, Scenario
from .models import (
    Action,
    Event,
    ExecutionStatus,
    Goal,
    InboundMessage,
    Point,
    RunMetrics,
    Scene,
    Severity,
    Worker,
    WorldState,
    now_iso,
)

log = logging.getLogger("hive.state")

WORKER_SPECS: list[tuple[str, str, str, str]] = [
    ("worker_a", "Worker A", "ALPHA", "#5AC8FA"),
    ("worker_b", "Worker B", "BRAVO", "#5E5CE6"),
    ("worker_c", "Worker C", "CHARLIE", "#30D158"),
    ("worker_d", "Worker D", "DELTA", "#FF9F0A"),
    ("worker_e", "Worker E", "ECHO", "#FF375F"),
]


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
        self.scenario: Scenario = SCENARIOS[DEFAULT_SCENARIO]
        self.goal: Goal | None = None
        self.actions: dict[str, Action] = {}
        self.workers: dict[str, Worker] = {}
        self.scene: Scene = Scene()
        self.world: WorldState = WorldState(mode=settings.world_mode)  # type: ignore[arg-type]
        self.locks: dict[str, str] = {}
        self.events: deque[Event] = deque(maxlen=500)
        self.inbox: deque[InboundMessage] = deque()
        self.execution_status: ExecutionStatus = "idle"
        self.metrics = RunMetrics()
        self.pending_grounding: dict[str, Any] | None = None
        self.escalation_armed: bool = False
        self.last_call: dict[str, Any] | None = None
        self.host_overrides: dict[str, Any] = {}
        self._seq = 0
        self._lock = asyncio.Lock()
        self._started_monotonic: float | None = None
        self._pending_events: list[Event] = []
        self._build_workers()

    # ── construction ────────────────────────────────────────────────────────

    def _build_workers(self) -> None:
        for wid, name, callsign, color in WORKER_SPECS:
            seat = WORKER_SEATS[wid]
            self.workers[wid] = Worker(
                id=wid,
                display_name=name,
                callsign=callsign,
                color=color,
                role=self.scenario.worker_roles.get(wid, "Operator"),
                reachable_zones=self.scenario.worker_reachability.get(wid, ["field"]),
                position=Point(x=seat[0], y=seat[1]),
            )

    def _apply_scenario(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.scene.zones = scenario.build_zones()
        for wid, w in self.workers.items():
            w.role = scenario.worker_roles.get(wid, "Operator")
            w.reachable_zones = scenario.worker_reachability.get(wid, ["field"])

    # ── events ──────────────────────────────────────────────────────────────

    async def emit(
        self,
        type: str,
        message: str,
        *,
        severity: Severity = "info",
        actor: str = "hive",
        **meta: Any,
    ) -> Event:
        """The ONLY place seq increments. Broadcast happens via the ws manager."""
        async with self._lock:
            self._seq += 1
            ev = Event(
                id=f"evt_{self._seq:06d}",
                seq=self._seq,
                timestamp=now_iso(),
                type=type,
                severity=severity,
                actor=actor,
                message=message,
                metadata=meta,
            )
            self.events.append(ev)
        from .websocket_manager import ws  # local import: avoids a cycle

        await ws.broadcast_host("event", ev.model_dump())
        return ev

    def emit_soon(
        self, type: str, message: str, *, severity: Severity = "info", actor: str = "hive", **meta: Any
    ) -> Event:
        """Synchronous emit for use inside the tick. Broadcast is coalesced by the tick."""
        self._seq += 1
        ev = Event(
            id=f"evt_{self._seq:06d}",
            seq=self._seq,
            timestamp=now_iso(),
            type=type,
            severity=severity,
            actor=actor,
            message=message,
            metadata=meta,
        )
        self.events.append(ev)
        self._pending_events.append(ev)
        return ev

    # ── vision-facing helpers ───────────────────────────────────────────────

    def override_active(self, object_id: str) -> bool:
        """True while a host-assisted observation for this object still stands.

        An operator correcting the model outranks the tracker for a short window; the
        bridge owns the TTL so both call sites agree.
        """
        from .vision import bridge

        return bridge.override_active(object_id)

    def set_host_override(self, object_id: str) -> None:
        from .vision import bridge

        bridge._overrides[object_id] = __import__("time").monotonic()

    def mark_world_dirty(self) -> None:
        """Flag the world as changed. Broadcasts are coalesced by the tick rather than
        pushed per-change, so the vision loop can call this freely at 10-20 Hz."""
        from .websocket_manager import ws

        ws.mark_world_dirty()

    def consume_dirty(self) -> bool:
        from .websocket_manager import ws

        was = ws._world_dirty
        ws._world_dirty = False
        return was

    def emit_nowait(self, type: str, message: str, severity: str = "info",
                    actor: str = "vision", metadata: dict | None = None) -> None:
        """Fire-and-forget emit for synchronous hot paths (the vision tick).

        Same sequence counter as emit()/emit_soon(), so ordering stays gap-free across
        every producer; the tick flushes it with everything else.
        """
        self.emit_soon(type, message, severity=severity, actor=actor, **(metadata or {}))

    def drain_pending_events(self) -> list[Event]:
        out, self._pending_events = self._pending_events, []
        return out

    # ── queries ─────────────────────────────────────────────────────────────

    def actions_with_status(self, *statuses: str) -> list[Action]:
        return [a for a in self.actions.values() if a.status in statuses]

    def worker(self, wid: str | None) -> Worker | None:
        return self.workers.get(wid) if wid else None

    def callsign(self, wid: str | None) -> str:
        w = self.worker(wid)
        return w.callsign if w else "—"

    def free_locks_for(self, action_id: str) -> None:
        for key, holder in list(self.locks.items()):
            if holder == action_id:
                self.locks.pop(key, None)
        for obj in self.scene.objects:
            if obj.locked_by == action_id:
                obj.locked_by = None

    def label_of(self, oid: str | None) -> str:
        return self.scene.label_of(oid) if oid else "the item"

    def zone_label(self, zid: str | None) -> str:
        return self.scene.zone_label(zid) if zid else "the floor"

    # ── lifecycle ───────────────────────────────────────────────────────────

    async def reset(self, scenario_id: str | None = None) -> None:
        """Total reset. Keeps sockets and worker connectivity — nobody re-scans a QR code."""
        scenario = SCENARIOS.get(scenario_id or self.scenario.id, SCENARIOS[DEFAULT_SCENARIO])
        self.goal = None
        self.actions = {}
        self.locks = {}
        self.inbox.clear()
        self.execution_status = "idle"
        self.metrics = RunMetrics()
        self.pending_grounding = None
        self._started_monotonic = None
        self._apply_scenario(scenario)

        # Reset must be TOTAL. Every module that keeps state between ticks gets cleared,
        # or a second run inherits the first one's debounce counters, locks and overrides.
        # We press this button a dozen times during a demo.
        from . import orchestrator, recovery, scheduler
        from .attribution import attribution
        from .integrations import voygr
        from .vision import bridge

        bridge.reset()
        recovery.reset()
        orchestrator.reset_adjudication()
        attribution.reset()
        voygr.reset_cooldown()
        with contextlib.suppress(Exception):
            scheduler.reset()

        bridge.scan(self)

        for w in self.workers.values():
            w.status = "ready" if w.connected else "disconnected"
            w.available = True
            w.current_action_id = None
            w.assignment_count = 0
            w.confidence = 1.0

        self.events.clear()
        self._pending_events = []
        self._seq = 0

        from .websocket_manager import ws

        await ws.broadcast_snapshot()
        await self.emit(
            "system_reset",
            f"HIVE reset. {scenario.title} loaded. Collective standing by.",
            severity="info",
            scenario_id=scenario.id,
        )

    def start_run(self) -> None:
        self.execution_status = "executing"
        self._started_monotonic = time.monotonic()
        self.metrics.started_at = now_iso()
        self.metrics.actions_total = len(self.actions)

    def tick_metrics(self) -> None:
        live = len(self.actions_with_status("dispatched", "acknowledged", "executing"))
        self.metrics.parallel_peak = max(self.metrics.parallel_peak, live)
        if self._started_monotonic is not None:
            self.metrics.elapsed_seconds = round(time.monotonic() - self._started_monotonic, 1)
        verified = self.actions_with_status("verified")
        self.metrics.actions_verified = len(verified)
        if verified:
            self.metrics.avg_confidence = round(
                sum(a.confidence for a in verified) / len(verified), 2
            )
        idle = sum(
            1 for w in self.workers.values() if w.connected and w.available and not w.current_action_id
        )
        self.metrics.worker_idle_seconds += idle / max(settings.tick_hz, 0.1)

    # ── snapshot ────────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        return {
            "execution_status": self.execution_status,
            "scenario": {
                "id": self.scenario.id,
                "title": self.scenario.title,
                "subtitle": self.scenario.subtitle,
                "suggested_goal": self.scenario.build_goal(self.scene),
                "comms_profile": self.scenario.comms_profile,
                "lexicon": self.scenario.lexicon,
                "recommended_failure": self.scenario.recommended_failure,
            },
            "scenarios": [
                {"id": s.id, "title": s.title, "subtitle": s.subtitle, "suggested_goal": s.suggested_goal}
                for s in SCENARIOS.values()
            ],
            "goal": self.goal.model_dump() if self.goal else None,
            "actions": [a.model_dump() for a in self.actions.values()],
            "workers": [w.public_dict() for w in self.workers.values()],
            "scene": self.scene.model_dump(),
            "world": self.world.model_dump(),
            "locks": self.locks,
            "metrics": self.metrics.model_dump(),
            "contributions": _contributions(self),
            "events": [e.model_dump() for e in list(self.events)[-200:]],
            "pending_grounding": self.pending_grounding,
            "escalation_armed": self.escalation_armed,
            "last_call": self.last_call,
            "demo_mode": settings.demo_mode,
        }


def _contributions(s: "HiveState") -> list[dict[str, Any]]:
    from .attribution import attribution

    return attribution.leaderboard(s)

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

"""The single source of truth. See docs/CONTRACTS.md and Ojas.md §4.

`emit()` is the only place `seq` increments — this keeps the event timeline gap-free
and consistently orderable across every connected client (sort by seq, never by
timestamp, since clocks are not the ordering authority)."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field

from app.demo.scenarios import SCENARIOS
from app.models import (
    Action,
    Event,
    Goal,
    RunMetrics,
    Severity,
    Worker,
    WorkerStatus,
    WORKER_SEED,
    WorldState,
    now_iso,
)


@dataclass
class InboundMessage:
    type: str
    payload: dict
    worker_id: str | None = None
    role: str = "host"


@dataclass
class HiveState:
    mode: str = "simulation"
    goal: Goal | None = None
    actions: dict[str, Action] = field(default_factory=dict)
    workers: dict[str, Worker] = field(default_factory=dict)
    world: WorldState = field(default_factory=WorldState)
    locks: dict[str, str] = field(default_factory=dict)
    events: deque[Event] = field(default_factory=lambda: deque(maxlen=500))
    inbox: deque[InboundMessage] = field(default_factory=deque)
    execution_status: str = "idle"
    metrics: RunMetrics = field(default_factory=RunMetrics)
    scenario_id: str = "incident_stabilization"
    escalation_armed: bool = False
    _seq: int = 0
    _world_dirty: bool = False

    def __post_init__(self) -> None:
        self._lock = asyncio.Lock()
        for wid, name, callsign, color in WORKER_SEED:
            self.workers[wid] = Worker(
                id=wid,
                display_name=name,
                callsign=callsign,
                color=color,
                reachable_zones=["zone_1", "zone_2", "zone_3", "zone_4", "field"],
                supported_actions=[
                    "pick_up", "move_to_zone", "place_in_zone", "place_on",
                    "hold", "release", "inspect", "standby",
                ],
            )

    async def emit(self, type: str, message: str, *, severity: Severity | str = Severity.info,
                   actor: str = "hive", **meta) -> Event:
        async with self._lock:
            self._seq += 1
            ev = Event(
                id=f"evt_{self._seq:06d}",
                seq=self._seq,
                timestamp=now_iso(),
                type=type,
                severity=Severity(severity) if not isinstance(severity, Severity) else severity,
                actor=actor,
                message=message,
                metadata=meta,
            )
            self.events.append(ev)
        from app.websocket_manager import ws  # lazy import: avoid circular import

        await ws.broadcast("event", ev.model_dump())
        return ev

    def mark_world_dirty(self) -> None:
        self._world_dirty = True

    async def reset(self, scenario_id: str | None = None) -> None:
        scenario_id = scenario_id or self.scenario_id
        scenario = SCENARIOS[scenario_id]
        self.scenario_id = scenario_id
        self.goal = None
        self.actions = {}
        self.locks = {}
        self.world = scenario.build_world()
        self.execution_status = "idle"
        self.metrics = RunMetrics()
        for w in self.workers.values():
            w.status = WorkerStatus.ready if w.connected else WorkerStatus.disconnected
            w.available = True
            w.current_action_id = None
            w.assignment_count = 0
        self.events.clear()
        self._seq = 0
        from app.websocket_manager import ws  # lazy import: avoid circular import

        await ws.broadcast_snapshot()
        await self.emit("system_reset", "HIVE reset. Collective standing by.", severity="info")


state = HiveState()

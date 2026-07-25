"""Capability-aware scheduler.

Two things matter here, in this order:

1. **Assignments must be defensibly correct** — hard filters no scoring can override, and a
   deliberately dumb lock model that cannot break on stage.
2. **The explanation must read as reasoning.** The string this file generates is the most
   quoted thing on the screen, so it names the top contributing factors *and* says why not the
   runner-up. It is generated from explicit scoring factors, not from model chain-of-thought,
   which is exactly what we can honestly claim.

Weights are tuned for legibility, not optimality: the opening wave should visibly use several
different workers. If two workers get everything, an audience reads it as a script.

The scheduler never mutates `Action.status` — it returns decisions and the orchestrator
applies them (docs/CONTRACTS.md §1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Protocol

from .models import (
    Action,
    ActionStatus,
    ActionType,
    ObservedObject,
    Scene,
    Worker,
    WorkerStatus,
    SUPPORTED_ACTIONS,
)

# ── weights ────────────────────────────────────────────────────────────────────

W_DISTANCE = 2.0
W_WORKLOAD = 1.5
W_REACHABILITY = 1.0
W_CAPABILITY = 3.0
W_COLLISION = 2.5
W_FAIRNESS = 0.8
W_RISK = 2.0

LIVE_STATUSES = {
    ActionStatus.assigned.value,
    ActionStatus.dispatched.value,
    ActionStatus.acknowledged.value,
    ActionStatus.executing.value,
    ActionStatus.awaiting_verification.value,
}
UNAVAILABLE_WORKER_STATUSES = {
    WorkerStatus.unavailable.value,
    WorkerStatus.paused.value,
    WorkerStatus.emergency.value,
    WorkerStatus.executing.value,
    WorkerStatus.disconnected.value,
    WorkerStatus.joining.value,
}
TERMINAL_STATUSES = {ActionStatus.verified.value, ActionStatus.cancelled.value}
DEADLOCK_TICKS = 3


class SchedulerState(Protocol):
    """Whatever the orchestrator owns, as long as it exposes these."""

    workers: list[Worker]
    actions: list[Action]
    scene: Scene
    locks: dict[str, str]  # lock_target -> action_id


@dataclass
class Assignment:
    worker_id: str
    callsign: str = ""
    score: float = 0.0
    reason: str = ""  # one sentence, shown in the UI
    reason_short: str = ""  # used inside another assignment's counterfactual
    factors: dict[str, float] = field(default_factory=dict)  # the receipts, shown on hover
    viable: bool = True
    blocked_by: str = ""


@dataclass
class SchedulerTick:
    batch: list[tuple[Action, Assignment]] = field(default_factory=list)
    deadlock: str | None = None


# ── small helpers ──────────────────────────────────────────────────────────────


def _object_of(action: Action, scene: Scene) -> ObservedObject | None:
    return scene.by_id(action.object_id) if action.object_id else None


def _label(action: Action, scene: Scene) -> str:
    obj = _object_of(action, scene)
    if obj is not None:
        label = obj.display_label()
        return label if label.lower().startswith(("the ", "a ", "an ")) else f"the {label}"
    if action.target_zone:
        return scene.zone_label(action.target_zone)
    return "this step"


def _distance(worker: Worker, action: Action, scene: Scene) -> float:
    obj = _object_of(action, scene)
    if obj is not None:
        target = obj.position
    elif action.target_zone:
        zone = scene.zone_by_id(action.target_zone)
        if zone is None:
            return 0.5
        target = zone.bounds.center
    else:
        return 0.5
    d = math.hypot(worker.position.x - target.x, worker.position.y - target.y)
    return min(1.0, d / math.sqrt(2))


def _is_native(worker: Worker, zone_id: str | None, scene: Scene) -> bool:
    """Is the worker standing in that area already?"""
    if not zone_id or zone_id == "field":
        return False
    zone = scene.zone_by_id(zone_id)
    return bool(zone and zone.bounds.contains(worker.position))


def _held_locks(state: SchedulerState, action: Action) -> list[str]:
    return [lock for lock, holder in (state.locks or {}).items() if lock in action.lock_targets and holder != action.id]


def _live_actions(state: SchedulerState) -> list[Action]:
    return [a for a in state.actions if a.status in LIVE_STATUSES]


def _dep_map(state: SchedulerState) -> dict[str, Action]:
    return {a.id: a for a in state.actions}


# ── scoring ────────────────────────────────────────────────────────────────────


def score_workers(action: Action, state: SchedulerState) -> list[Assignment]:
    """Lower is better. Returns candidates sorted ascending; index 0 is the pick."""
    scene = state.scene
    workers = list(state.workers)
    total_assignments = max(1, sum(w.assignment_count for w in workers))
    live = [a for a in _live_actions(state) if a.id != action.id]
    zone_busy = {a.target_zone for a in live if a.target_zone}
    holders = _held_locks(state, action)

    out: list[Assignment] = []
    for w in workers:
        a = Assignment(worker_id=w.id, callsign=w.callsign)

        # ── hard filters: nothing below can override these ──
        if not w.connected or not w.available or w.status in UNAVAILABLE_WORKER_STATUSES:
            a.viable, a.blocked_by, a.reason_short = False, "unavailable", "not available"
            out.append(a)
            continue
        if action.type not in w.supported_actions:
            a.viable, a.blocked_by = False, "capability"
            a.reason_short = f"cannot perform {action.type.replace('_', ' ')}"
            out.append(a)
            continue
        obj = _object_of(action, scene)
        if obj is not None and obj.zone not in w.reachable_zones and obj.zone != "field":
            a.viable, a.blocked_by = False, "reach_object"
            a.reason_short = f"cannot reach {scene.zone_label(obj.zone)}"
            out.append(a)
            continue
        if action.target_zone and action.target_zone not in w.reachable_zones and action.target_zone != "field":
            a.viable, a.blocked_by = False, "reach_target"
            a.reason_short = f"cannot reach {scene.zone_label(action.target_zone)}"
            out.append(a)
            continue
        if holders:
            a.viable, a.blocked_by = False, "lock"
            a.reason_short = "the resource is in use"
            out.append(a)
            continue
        if obj is not None and any(
            o.held_by == w.id and o.id != obj.id for o in scene.objects
        ):
            a.viable, a.blocked_by, a.reason_short = False, "hands_full", "already holding something else"
            out.append(a)
            continue

        # ── soft score ──
        distance_cost = _distance(w, action, scene)
        workload_penalty = 1.0 if w.current_action_id else 0.0
        native = _is_native(w, action.target_zone, scene) or (obj is not None and _is_native(w, obj.zone, scene))
        reachability_penalty = 0.0 if native else 0.5
        partial = len(w.supported_actions) < len(SUPPORTED_ACTIONS)
        capability_penalty = 0.3 if partial else 0.0
        collision_penalty = 1.0 if action.target_zone and action.target_zone in zone_busy else 0.0
        fairness_penalty = w.assignment_count / total_assignments
        risk_penalty = max(0.0, 1.0 - w.confidence)

        a.factors = {
            "distance_cost": round(distance_cost, 3),
            "workload_penalty": workload_penalty,
            "reachability_penalty": reachability_penalty,
            "capability_penalty": capability_penalty,
            "collision_penalty": collision_penalty,
            "fairness_penalty": round(fairness_penalty, 3),
            "risk_penalty": round(risk_penalty, 3),
        }
        a.score = round(
            W_DISTANCE * distance_cost
            + W_WORKLOAD * workload_penalty
            + W_REACHABILITY * reachability_penalty
            + W_CAPABILITY * capability_penalty
            + W_COLLISION * collision_penalty
            + W_FAIRNESS * fairness_penalty
            + W_RISK * risk_penalty,
            4,
        )
        a.reason_short = _reason_short(a, action, scene)
        out.append(a)

    out.sort(key=lambda c: (not c.viable, c.score))
    viable = [c for c in out if c.viable]
    for i, cand in enumerate(viable):
        runner_up = viable[i + 1] if i + 1 < len(viable) else None
        cand.reason = explain(action, cand, runner_up, cand.factors, scene)
    return out


def _reason_short(cand: Assignment, action: Action, scene: Scene) -> str:
    f = cand.factors
    if f.get("workload_penalty"):
        return "already on another task"
    if f.get("collision_penalty"):
        return f"working in {scene.zone_label(action.target_zone) if action.target_zone else 'that area'} already"
    if f.get("risk_penalty", 0) > 0.1:
        return "still recovering from a failed step"
    if f.get("distance_cost", 0) > 0.5:
        return "further away"
    if f.get("fairness_penalty", 0) > 0.4:
        return "carrying the most work already"
    return "a close second"


def explain(
    action: Action,
    winner: Assignment,
    runner_up: Assignment | None,
    factors: dict[str, float],
    scene: Scene,
) -> str:
    """Generated from the top contributing factors — never generic, always with a counterfactual."""
    label = _label(action, scene)
    bits: list[str] = []
    if factors.get("distance_cost", 1.0) < 0.25:
        bits.append(f"closest responder to {label}")
    if not factors.get("workload_penalty"):
        bits.append("currently idle")
    if not factors.get("reachability_penalty"):
        bits.append("already standing in that area")
    if not factors.get("collision_penalty") and action.target_zone:
        bits.append("no conflicting activity in that zone")
    if factors.get("fairness_penalty", 1.0) < 0.2:
        bits.append("lowest current workload")
    if not factors.get("risk_penalty"):
        bits.append("no recent failures")
    if not bits:
        bits.append(f"the only viable responder for {label}")
    why_not = f" {runner_up.callsign} was {runner_up.reason_short}." if runner_up else ""
    return f"{winner.callsign} selected: {', '.join(bits[:3])}.{why_not}"


# ── blocking explanations ──────────────────────────────────────────────────────


def describe_block(action: Action, state: SchedulerState) -> str:
    """Powers the host's honest idle explanations. Matters more than it looks."""
    scene = state.scene
    by_id = _dep_map(state)

    unmet = [by_id[d] for d in action.dependencies if d in by_id and by_id[d].status != ActionStatus.verified.value]
    if unmet:
        dep = unmet[0]
        target = _label(dep, scene)
        if dep.type == ActionType.inspect.value:
            return f"waiting: {target} has not been confirmed yet"
        return f"waiting: this depends on {target}, which has not arrived yet"

    holders = _held_locks(state, action)
    if holders:
        holder_id = state.locks[holders[0]]
        return f"waiting: {_label(action, scene)} is in use by {holder_id}"

    candidates = score_workers(action, state)
    if not candidates:
        return "waiting: no workers have joined yet"
    if any(c.viable for c in candidates):
        return "ready"

    reasons = [c.blocked_by for c in candidates]
    if action.target_zone and all(r in ("reach_target", "reach_object") for r in reasons):
        return f"waiting: no worker can reach {scene.zone_label(action.target_zone)}"
    if all(r == "capability" for r in reasons):
        return f"waiting: no worker can perform {action.type.replace('_', ' ')}"
    if all(r in ("unavailable", "hands_full") for r in reasons):
        return "waiting: every worker is already committed"
    first = next((c for c in candidates if c.reason_short), None)
    return f"waiting: {first.reason_short}" if first else "waiting: no viable responder"


# ── batch selection ────────────────────────────────────────────────────────────


class Scheduler:
    """Holds only the deadlock counter. Everything else is a pure function of state."""

    def __init__(self) -> None:
        self._empty_ticks = 0

    def reset(self) -> None:
        self._empty_ticks = 0

    def select_batch(self, state: SchedulerState) -> list[tuple[Action, Assignment]]:
        """Pick a *set* per tick, not one action at a time."""
        available = sorted(
            (a for a in state.actions if a.status == ActionStatus.available.value),
            key=lambda a: (-a.priority, a.id),
        )
        claimed_locks: set[str] = set(state.locks or {})
        claimed_workers: set[str] = set()
        batch: list[tuple[Action, Assignment]] = []

        for a in available:
            if claimed_locks & set(a.lock_targets):  # resource contention inside this tick
                a.blocked_reason = describe_block(a, state)
                continue
            scored = score_workers(a, state)
            candidates = [c for c in scored if c.viable and c.worker_id not in claimed_workers]
            if not candidates:
                a.blocked_reason = (
                    "waiting: everyone who can do this is already committed this cycle"
                    if any(c.viable for c in scored)
                    else describe_block(a, state)
                )
                continue
            best = candidates[0]
            a.blocked_reason = None
            batch.append((a, best))
            claimed_locks |= set(a.lock_targets)
            claimed_workers.add(best.worker_id)
        return batch

    def tick(self, state: SchedulerState) -> SchedulerTick:
        """One scheduling tick, plus the deadlock guard.

        A demo that freezes with no explanation is far worse than one that says
        "no viable responder — rebuilding plan."
        """
        batch = self.select_batch(state)
        outstanding = [a for a in state.actions if a.status not in TERMINAL_STATUSES]
        executing = _live_actions(state)

        if batch or executing or not outstanding:
            self._empty_ticks = 0
            return SchedulerTick(batch=batch)

        self._empty_ticks += 1
        if self._empty_ticks < DEADLOCK_TICKS:
            return SchedulerTick(batch=batch)

        self._empty_ticks = 0
        stuck = [a for a in outstanding if a.status in (ActionStatus.available.value, ActionStatus.queued.value)]
        reason = describe_block(stuck[0], state) if stuck else "no actions can proceed"
        blocker = reason.removeprefix("waiting: ")
        return SchedulerTick(batch=batch, deadlock=f"No action can proceed — {blocker}. Rebuilding the plan.")


_default = Scheduler()


def select_batch(state: SchedulerState) -> list[tuple[Action, Assignment]]:
    return _default.select_batch(state)


def assign_actions(state: SchedulerState) -> SchedulerTick:
    """The orchestrator's entry point: one tick of scheduling decisions."""
    return _default.tick(state)


def reset() -> None:
    _default.reset()


def best_worker(action: Action, state: SchedulerState) -> Assignment | None:
    return next((c for c in score_workers(action, state) if c.viable), None)


def unlock_dependents(state: SchedulerState, verified: Iterable[str]) -> list[Action]:
    """Actions whose dependencies are now all verified. The orchestrator flips their status."""
    done = {a.id for a in state.actions if a.status == ActionStatus.verified.value} | set(verified)
    ready = []
    for a in state.actions:
        if a.status == ActionStatus.queued.value and all(d in done for d in a.dependencies):
            ready.append(a)
    return ready

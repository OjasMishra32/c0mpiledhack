"""Capability-aware scheduler.

Two things matter here, in this order:

1. **Assignments must be defensibly correct** — hard filters no scoring can override, and a
   deliberately dumb lock model that cannot break on stage.
2. **The explanation must read as reasoning.** The string this file generates is the most
   quoted thing on the screen, so it names the top contributing factors *and* always says why
   not somebody else — an explanation that says what but never why-not is half an explanation.
   When only one worker is viable the counterfactual falls back to the blocked worker who came
   closest, because "nobody else could reach it" is itself the reasoning. It is generated from
   explicit scoring factors, not from model chain-of-thought, which is exactly what we can
   honestly claim.

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

# Same weights, keyed by factor name and ordered heaviest-first. Used to answer "which factor
# actually decided this?" when naming a counterfactual, so the sentence blames the factor that
# moved the decision rather than the first one that happens to differ. Insertion order is the
# tie-break, which keeps the wording identical across hash seeds.
FACTOR_WEIGHTS: dict[str, float] = {
    "capability_penalty": W_CAPABILITY,
    "collision_penalty": W_COLLISION,
    "distance_cost": W_DISTANCE,
    "risk_penalty": W_RISK,
    "workload_penalty": W_WORKLOAD,
    "reachability_penalty": W_REACHABILITY,
    "fairness_penalty": W_FAIRNESS,
}

# How close a *blocked* worker came to being usable, lowest first. Momentary blockers rank ahead
# of structural ones (they'd have been fine a moment later), and a worker who never showed up
# ranks last — someone offline is the least interesting alternative to name on screen.
BLOCK_PROXIMITY: dict[str, int] = {
    "hands_full": 0,
    "lock": 1,
    "reach_object": 2,
    "reach_target": 2,
    "capability": 3,
    "unavailable": 4,
}

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
    reason_short: str = ""  # used inside another assignment's counterfactual, and by describe_block
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


def _zone_phrase(action: Action, scene: Scene) -> str:
    """The place this action is *about*, by its label — never a generic "that zone".

    Returns "" when there is no named zone, and callers drop the clause rather than print a
    vague one. The open floor is not a place you can be conflicted in, so it doesn't count.
    """
    obj = _object_of(action, scene)
    for zone_id in (action.target_zone, obj.zone if obj is not None else None):
        if zone_id and zone_id != "field":
            label = scene.zone_label(zone_id)
            if label and label != zone_id:
                return label
    return ""


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
    blocked_alternative = _strongest_blocked(out)
    for i, cand in enumerate(viable):
        # Every explanation gets a counterfactual, in order of how much of an alternative the
        # other worker really was: the next viable candidate, else the blocked worker who came
        # closest (the common "only one person can reach that zone" case), else the strongest
        # candidate ahead of this one. Only a roster of exactly one has nobody to name.
        alternative = viable[i + 1] if i + 1 < len(viable) else (blocked_alternative or (viable[0] if i else None))
        cand.reason = explain(action, cand, alternative, cand.factors, scene)
    return out


def _strongest_blocked(candidates: list[Assignment]) -> Assignment | None:
    """The blocked worker who came closest to being usable — the fallback counterfactual."""
    blocked = [c for c in candidates if not c.viable]
    if not blocked:
        return None
    return min(blocked, key=lambda c: (BLOCK_PROXIMITY.get(c.blocked_by, len(BLOCK_PROXIMITY)), c.callsign))


def _reason_short(cand: Assignment, action: Action, scene: Scene) -> str:
    f = cand.factors
    if f.get("workload_penalty"):
        return "already on another task"
    if f.get("collision_penalty"):
        zone = _zone_phrase(action, scene)  # named, or not mentioned at all — never "that area"
        return f"working in {zone} already" if zone else "working in the same place already"
    if f.get("risk_penalty", 0) > 0.1:
        return "still recovering from a failed step"
    if f.get("distance_cost", 0) > 0.5:
        return "further away"
    if f.get("fairness_penalty", 0) > 0.4:
        return "carrying the most work already"
    return "a close second"


def _factor_phrase(key: str, action: Action, scene: Scene) -> str:
    """How the *loser* of one factor reads in a sentence. "" when it can't be said precisely."""
    if key == "distance_cost":
        return f"further from {_label(action, scene)}"
    if key == "workload_penalty":
        return "mid-task"
    if key == "risk_penalty":
        return "still recovering from a failed step"
    if key == "fairness_penalty":
        return "carrying more of the load already"
    if key == "capability_penalty":
        return "cleared for fewer kinds of work"
    zone = _zone_phrase(action, scene)  # the rest only mean something with a place to name
    if not zone:
        return ""
    if key == "reachability_penalty":
        return f"not in {zone} yet"
    if key == "collision_penalty":
        return f"already busy in {zone}"
    return ""


def _losing_factors(winner: dict[str, float], other: dict[str, float]) -> list[str]:
    """Factors the other candidate lost on, worst weighted gap first."""
    gaps = []
    for i, (key, weight) in enumerate(FACTOR_WEIGHTS.items()):
        gap = weight * (other.get(key, 0.0) - winner.get(key, 0.0))
        if gap > 1e-9:
            gaps.append((-gap, i, key))
    return [key for _, _, key in sorted(gaps)]


def _why_not(action: Action, winner: Assignment, other: Assignment | None, scene: Scene) -> str:
    """The counterfactual half of every explanation. Never empty, and never invents a worker."""
    if other is None:
        return "No other responder was available."
    if not other.viable:
        # Blocked workers already carry the reason they were excluded; just make it a sentence.
        if not other.reason_short:
            return f"{other.callsign} was not an option this cycle."
        if other.blocked_by in ("capability", "reach_object", "reach_target"):
            return f"{other.callsign} {other.reason_short}."  # already reads as a verb phrase
        if other.blocked_by == "lock":
            return f"{other.callsign} could not start while {other.reason_short}."
        return f"{other.callsign} was {other.reason_short}."
    if other.score < winner.score - 1e-9:
        # Naming someone who actually scored better only happens for a fallback pick; say so
        # plainly rather than inventing a fault they don't have.
        return f"{other.callsign} was the stronger option overall."
    for key in _losing_factors(winner.factors, other.factors):
        phrase = _factor_phrase(key, action, scene)
        if phrase:
            return f"{other.callsign} was {phrase}."
    return f"{other.callsign} scored identically and lost the tie-break."


def explain(
    action: Action,
    winner: Assignment,
    runner_up: Assignment | None,
    factors: dict[str, float],
    scene: Scene,
) -> str:
    """One sentence of positive factors, then always a counterfactual.

    `runner_up` is whichever worker was the strongest alternative — usually the next viable
    candidate, but a *blocked* one when nobody else was viable, in which case the sentence says
    what excluded them. Zones are named by their label; at most three factors, so it stays
    quotable; and the positive half is never generic, because a hard filter this worker cleared
    is more informative than the word "selected" on its own.
    """
    label = _label(action, scene)
    zone = _zone_phrase(action, scene)
    bits: list[str] = []
    if factors.get("distance_cost", 1.0) < 0.25:
        bits.append(f"closest to {label}")
    if not factors.get("workload_penalty"):
        bits.append("currently idle")
    standing = not factors.get("reachability_penalty") and bool(zone)
    if standing:
        bits.append(f"already standing in {zone}")
    if not factors.get("collision_penalty") and action.target_zone and zone:
        # "there" is only ever used after the zone has just been named, so it always has one.
        bits.append("no conflicting activity there" if standing else f"no conflicting activity in {zone}")
    if factors.get("fairness_penalty", 1.0) < 0.2:
        bits.append("lowest current workload")
    if not factors.get("risk_penalty"):
        bits.append("no recent failures")
    if not bits:
        # Nothing scored well. Fall back to the hard filters they *did* clear — honest, specific,
        # and it still tells the room why this assignment is legal.
        bits.append(f"hands free and cleared to work in {zone}" if zone else "hands free and cleared for this step")
    return f"{winner.callsign} selected: {', '.join(bits[:3])}. {_why_not(action, winner, runner_up, scene)}"


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

"""Capability-aware scheduler.

OWNER: Zechariah. Working implementation — extend in place.

Lower score is better. Hard filters first (viability), then a weighted soft score tuned
for LEGIBILITY, not optimality: the demo must visibly pick the near worker, visibly
spread work across all five, and visibly avoid a worker who just failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import Action, Worker

W_DISTANCE = 2.0
W_WORKLOAD = 1.5
W_REACH = 1.0
W_CAPABILITY = 3.0
W_COLLISION = 2.5
W_FAIRNESS = 0.8
W_RISK = 2.0


@dataclass
class Assignment:
    worker_id: str
    callsign: str
    score: float
    reason: str
    factors: dict[str, float] = field(default_factory=dict)
    viable: bool = True
    blocked_by: str = ""


def _object_zone(state: Any, action: Action) -> str | None:
    if not action.object_id:
        return action.target_zone
    obj = state.scene.by_id(action.object_id)
    return obj.zone if obj else None


def score_workers(action: Action, state: Any) -> list[Assignment]:
    """Return candidates sorted best-first. Non-viable candidates are included with
    viable=False so the UI can explain why nobody was chosen."""
    out: list[Assignment] = []
    total_assignments = max(1, sum(w.assignment_count for w in state.workers.values()))
    obj = state.scene.by_id(action.object_id) if action.object_id else None
    src_zone = _object_zone(state, action)

    for w in state.workers.values():
        blocked = _hard_filter(w, action, state, src_zone)
        if blocked:
            out.append(
                Assignment(w.id, w.callsign, 999.0, blocked, {}, viable=False, blocked_by=blocked)
            )
            continue

        f: dict[str, float] = {}
        f["distance_cost"] = round(w.position.dist(obj.position), 3) if obj else 0.3
        f["workload_penalty"] = 1.0 if w.current_action_id else 0.0
        native_src = src_zone in w.reachable_zones or src_zone == "field"
        native_dst = action.target_zone in w.reachable_zones or action.target_zone in (None, "field")
        f["reachability_penalty"] = 0.0 if (native_src and native_dst) else 0.5
        f["capability_penalty"] = 0.0
        f["collision_penalty"] = 1.0 if _zone_busy(state, action) else 0.0
        f["fairness_penalty"] = round(w.assignment_count / total_assignments, 3)
        f["risk_penalty"] = round(1.0 - w.confidence, 3)

        score = round(
            W_DISTANCE * f["distance_cost"]
            + W_WORKLOAD * f["workload_penalty"]
            + W_REACH * f["reachability_penalty"]
            + W_CAPABILITY * f["capability_penalty"]
            + W_COLLISION * f["collision_penalty"]
            + W_FAIRNESS * f["fairness_penalty"]
            + W_RISK * f["risk_penalty"],
            3,
        )
        out.append(Assignment(w.id, w.callsign, score, "", f))

    viable = sorted([a for a in out if a.viable], key=lambda a: a.score)
    for i, a in enumerate(viable):
        a.reason = explain(a, viable[i + 1] if i + 1 < len(viable) else None, action, state)
    return viable + [a for a in out if not a.viable]


def _hard_filter(w: Worker, action: Action, state: Any, src_zone: str | None) -> str:
    if not w.connected:
        return "offline"
    if not w.available or w.status in ("unavailable", "paused", "emergency"):
        return "unavailable"
    if w.current_action_id and w.current_action_id != action.id:
        return "already executing"
    if action.type not in w.supported_actions:
        return "cannot perform this action type"
    if src_zone and src_zone != "field" and src_zone not in w.reachable_zones:
        return f"cannot reach {state.zone_label(src_zone)}"
    if action.target_zone and action.target_zone != "field" and action.target_zone not in w.reachable_zones:
        return f"cannot reach {state.zone_label(action.target_zone)}"
    for key, holder in state.locks.items():
        if key in action.lock_targets and holder != action.id:
            return "resource locked by another action"
    return ""


def _zone_busy(state: Any, action: Action) -> bool:
    if not action.target_zone:
        return False
    for a in state.actions.values():
        if a.id == action.id:
            continue
        if a.status in ("dispatched", "acknowledged", "executing") and a.target_zone == action.target_zone:
            return True
    return False


def explain(winner: Assignment, runner_up: Assignment | None, action: Action, state: Any) -> str:
    """Generated from explicit scoring factors — not model chain-of-thought."""
    f = winner.factors
    bits: list[str] = []
    if f.get("distance_cost", 1) < 0.28:
        bits.append(f"closest to {state.label_of(action.object_id)}")
    if f.get("workload_penalty", 1) == 0:
        bits.append("currently idle")
    if f.get("collision_penalty", 1) == 0 and action.target_zone:
        bits.append(f"no conflicting activity in {state.zone_label(action.target_zone)}")
    if f.get("fairness_penalty", 1) < 0.18:
        bits.append("lowest current workload")
    if f.get("reachability_penalty", 1) == 0 and len(bits) < 2:
        bits.append("both locations in reach")
    if not bits:
        bits.append("best available on distance and load")

    tail = ""
    if runner_up:
        rf = runner_up.factors
        if rf.get("workload_penalty"):
            tail = f" {runner_up.callsign} is mid-task."
        elif rf.get("distance_cost", 0) > f.get("distance_cost", 0) + 0.1:
            tail = f" {runner_up.callsign} is further away."
        elif rf.get("fairness_penalty", 0) > f.get("fairness_penalty", 0):
            tail = f" {runner_up.callsign} has carried more work."
    return f"{winner.callsign} selected: {', '.join(bits[:3])}.{tail}"


def describe_block(action: Action, state: Any, candidates: list[Assignment]) -> str:
    reasons = {c.blocked_by for c in candidates if not c.viable and c.blocked_by}
    if not reasons:
        return "waiting for a free worker"
    if reasons == {"already executing"}:
        return "waiting: every capable worker is mid-task"
    if any("cannot reach" in r for r in reasons):
        zone = state.zone_label(action.target_zone)
        return f"waiting: no available worker can reach {zone}"
    if "resource locked by another action" in reasons:
        return f"waiting: {state.label_of(action.object_id)} is locked by another action"
    return "waiting: " + ", ".join(sorted(reasons))


def select_batch(state: Any) -> list[tuple[Action, Assignment]]:
    """Pick a SET of (action, worker) per tick — this is what produces visible parallelism.

    Within a single tick, two actions whose lock_targets intersect may never both be
    dispatched, and one worker may only take one action.
    """
    available = sorted(
        state.actions_with_status("available"), key=lambda a: (-a.priority, a.id)
    )
    claimed_locks: set[str] = set(state.locks.keys())
    claimed_workers: set[str] = set()
    batch: list[tuple[Action, Assignment]] = []

    for a in available:
        if claimed_locks & set(a.lock_targets):
            a.blocked_reason = f"waiting: {state.label_of(a.object_id)} is in use"
            continue
        cands = score_workers(a, state)
        viable = [c for c in cands if c.viable and c.worker_id not in claimed_workers]
        if not viable:
            a.blocked_reason = describe_block(a, state, cands)
            continue
        best = viable[0]
        a.blocked_reason = None
        batch.append((a, best))
        claimed_locks |= set(a.lock_targets)
        claimed_workers.add(best.worker_id)
    return batch

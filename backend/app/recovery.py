"""Deviation detection and recovery.

OWNER: Nikki. Working implementation — extend in place.

The central design decision: ISOLATE, DON'T RESTART. When something goes wrong we pause
only the dependency chain that actually depends on the affected resource. Everything else
keeps running. That is the whole pitch, and it is why we never call state.reset() here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .models import Action, Predicate, now_iso

DEBOUNCE_TICKS = 2
DISCONNECT_GRACE = 8.0


@dataclass
class DeviationTrigger:
    kind: str
    message: str
    action_ids: list[str] = field(default_factory=list)
    object_id: str | None = None
    worker_id: str | None = None
    expected: str = ""
    observed: str = ""


@dataclass
class RecoveryPlan:
    trigger: DeviationTrigger
    cancel_action_ids: list[str] = field(default_factory=list)
    pause_action_ids: list[str] = field(default_factory=list)
    release_locks: list[str] = field(default_factory=list)
    free_workers: list[str] = field(default_factory=list)
    insert_actions: list[Action] = field(default_factory=list)
    reassign: dict[str, str | None] = field(default_factory=dict)
    narration: str = ""
    confidence: float = 1.0


# ── detection ───────────────────────────────────────────────────────────────

_candidates: dict[str, int] = {}
_disconnect_since: dict[str, float] = {}


def reset() -> None:
    """Clear debounce and grace-period state so a reset run starts clean."""
    _candidates.clear()
    _disconnect_since.clear()


def _debounce(key: str) -> bool:
    """Require N consecutive ticks before a trigger fires. One flaky read must never
    put a red banner on the projector."""
    _candidates[key] = _candidates.get(key, 0) + 1
    return _candidates[key] >= DEBOUNCE_TICKS


def _clear(key: str) -> None:
    _candidates.pop(key, None)


def detect(state: Any) -> list[DeviationTrigger]:
    triggers: list[DeviationTrigger] = []
    now = time.monotonic()

    # 1. Verification regressed — the strongest demo trigger. A predicate we already
    #    verified is now false, which means reality moved after we believed it.
    for a in state.actions.values():
        if a.status != "verified" or not a.expected_predicates:
            continue
        from .verifier import check_predicate

        for pred in a.expected_predicates:
            holds, _ = check_predicate(pred, state)
            if holds:
                _clear(f"regress:{a.id}")
                continue
            if not _debounce(f"regress:{a.id}"):
                continue
            _clear(f"regress:{a.id}")
            obj = state.scene.by_id(pred.subject)
            triggers.append(
                DeviationTrigger(
                    kind="verification_regressed",
                    message=(
                        f"{state.label_of(pred.subject)} left "
                        f"{state.zone_label(pred.object)} after verification."
                    ),
                    action_ids=[a.id],
                    object_id=pred.subject,
                    expected=f"{state.label_of(pred.subject)} · {state.zone_label(pred.object)}",
                    observed=(
                        f"{state.label_of(pred.subject)} · {state.zone_label(obj.zone)}"
                        if obj
                        else "not visible"
                    ),
                )
            )
            break

    # 2. Object left its target zone while its action was in flight.
    for a in state.actions.values():
        if a.status not in ("dispatched", "acknowledged", "executing", "awaiting_verification"):
            continue
        if not a.object_id or not a.target_zone:
            continue
        obj = state.scene.by_id(a.object_id)
        if not obj or obj.held_by:
            continue
        # An object still sitting where it started is PENDING WORK, not a deviation.
        # Only flag it when it has landed somewhere that is neither origin nor target.
        wrong_zone = obj.zone not in (a.target_zone, "field", a.origin_zone)
        if not wrong_zone:
            _clear(f"stray:{a.id}")
            continue
        if not _debounce(f"stray:{a.id}"):
            continue
        _clear(f"stray:{a.id}")
        triggers.append(
            DeviationTrigger(
                kind="left_target_zone",
                message=(
                    f"{obj.display_label()} detected in {state.zone_label(obj.zone)}, "
                    f"expected {state.zone_label(a.target_zone)}."
                ),
                action_ids=[a.id],
                object_id=obj.id,
                expected=f"{obj.display_label()} · {state.zone_label(a.target_zone)}",
                observed=f"{obj.display_label()} · {state.zone_label(obj.zone)}",
            )
        )

    # 3. Timeouts.
    for a in state.actions.values():
        if a.status not in ("dispatched", "acknowledged", "executing"):
            continue
        if not a.dispatched_at:
            continue
        elapsed = _elapsed(a.dispatched_at)
        if elapsed > a.timeout_seconds:
            triggers.append(
                DeviationTrigger(
                    kind="worker_timeout",
                    message=(
                        f"No confirmation from {state.callsign(a.assigned_worker_id)} "
                        f"within {a.timeout_seconds}s."
                    ),
                    action_ids=[a.id],
                    worker_id=a.assigned_worker_id,
                )
            )

    # 4. Disconnects holding work (grace period — Wi-Fi blips constantly).
    for w in state.workers.values():
        if w.connected or not w.current_action_id:
            _disconnect_since.pop(w.id, None)
            continue
        first = _disconnect_since.setdefault(w.id, now)
        if now - first >= DISCONNECT_GRACE:
            _disconnect_since.pop(w.id, None)
            triggers.append(
                DeviationTrigger(
                    kind="worker_disconnected",
                    message=f"{w.callsign} offline while holding an assignment.",
                    action_ids=[w.current_action_id],
                    worker_id=w.id,
                )
            )

    # 5. Explicitly unavailable / paused workers holding work.
    for w in state.workers.values():
        if w.current_action_id and (not w.available or w.status in ("unavailable", "paused")):
            triggers.append(
                DeviationTrigger(
                    kind="worker_unavailable",
                    message=f"{w.callsign} unavailable. Releasing held resources.",
                    action_ids=[w.current_action_id],
                    worker_id=w.id,
                )
            )

    # 6. Blocked actions.
    for a in state.actions_with_status("blocked"):
        triggers.append(
            DeviationTrigger(
                kind="worker_blocked",
                message=f"{state.callsign(a.assigned_worker_id)} cannot complete this action.",
                action_ids=[a.id],
                worker_id=a.assigned_worker_id,
            )
        )

    return triggers


def _elapsed(iso: str) -> float:
    from datetime import datetime, timezone

    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except Exception:
        return 0.0


# ── planning ────────────────────────────────────────────────────────────────


def dependents_of(state: Any, action_ids: list[str]) -> list[str]:
    """Transitive closure of everything downstream of these actions."""
    out: set[str] = set()
    frontier = list(action_ids)
    while frontier:
        cur = frontier.pop()
        for a in state.actions.values():
            if cur in a.dependencies and a.id not in out:
                out.add(a.id)
                frontier.append(a.id)
    return sorted(out)


def plan_recovery(trigger: DeviationTrigger, state: Any) -> RecoveryPlan:
    plan = RecoveryPlan(trigger=trigger)

    if trigger.kind in ("worker_disconnected", "worker_unavailable", "worker_blocked"):
        return _reassign(trigger, state, plan)
    if trigger.kind == "worker_timeout":
        return _timeout_ladder(trigger, state, plan)
    if trigger.kind in ("verification_regressed", "left_target_zone", "wrong_object_moved"):
        return _retrieve_and_restore(trigger, state, plan)

    plan.narration = trigger.message
    plan.confidence = 0.3
    return plan


def _reassign(trigger: DeviationTrigger, state: Any, plan: RecoveryPlan) -> RecoveryPlan:
    for aid in trigger.action_ids:
        a = state.actions.get(aid)
        if not a:
            continue
        plan.release_locks += a.lock_targets
        plan.reassign[aid] = None  # rescore from scratch
    if trigger.worker_id:
        plan.free_workers.append(trigger.worker_id)
    cs = state.callsign(trigger.worker_id)
    plan.narration = (
        f"{cs} unavailable. Releasing held resources and reassigning by reachability and load."
    )
    return plan


def _timeout_ladder(trigger: DeviationTrigger, state: Any, plan: RecoveryPlan) -> RecoveryPlan:
    aid = trigger.action_ids[0]
    a = state.actions.get(aid)
    if not a:
        return plan
    cs = state.callsign(a.assigned_worker_id)
    if a.retry_count < a.max_retries:
        # Attempts 1 & 2: reissue (often the phone was in a pocket), escalating urgency.
        plan.reassign[aid] = a.assigned_worker_id
        plan.narration = f"No confirmation from {cs}. Reissuing instruction with higher urgency."
    else:
        # Attempt 3: drop that worker's confidence so the scheduler's risk penalty avoids
        # them, and hand the action to someone else.
        plan.reassign[aid] = None
        plan.release_locks += a.lock_targets
        if a.assigned_worker_id:
            plan.free_workers.append(a.assigned_worker_id)
        plan.narration = f"{cs} unresponsive after {a.max_retries} attempts. Reassigning."
    return plan


def _retrieve_and_restore(trigger: DeviationTrigger, state: Any, plan: RecoveryPlan) -> RecoveryPlan:
    """The flagship recovery. Pause ONLY the chain that depends on this resource; insert a
    high-priority retrieval; let every unrelated action continue untouched."""
    oid = trigger.object_id
    affected = [a for a in state.actions.values() if a.object_id == oid and a.status == "verified"]
    root_ids = trigger.action_ids or [a.id for a in affected]

    downstream = dependents_of(state, root_ids)
    pausable = [
        aid
        for aid in downstream
        if state.actions[aid].status
        in ("queued", "available", "assigned", "dispatched", "acknowledged", "executing")
    ]
    plan.pause_action_ids = pausable

    target_zone = None
    for aid in root_ids:
        a = state.actions.get(aid)
        if a and a.target_zone:
            target_zone = a.target_zone
            break
    if target_zone is None and affected:
        target_zone = affected[0].target_zone

    if oid and target_zone:
        rid = f"r{len([a for a in state.actions.values() if a.is_recovery]) + 1}"
        plan.insert_actions.append(
            Action(
                id=rid,
                type="place_in_zone",
                description=(
                    f"Return the {state.label_of(oid)} to {state.zone_label(target_zone)}."
                ),
                object_id=oid,
                target_zone=target_zone,
                priority=105,
                is_recovery=True,
                status="queued",
                timeout_seconds=20,
                lock_targets=[f"object:{oid}", f"zone:{target_zone}"],
                expected_predicates=[
                    Predicate(type="object_in_zone", subject=oid, object=target_zone)
                ],
                created_at=now_iso(),
            )
        )
        # Roots go back to queued so they re-verify once the item is restored.
        for aid in root_ids:
            a = state.actions.get(aid)
            if a and a.status == "verified":
                plan.reassign.setdefault(aid, None)

    n = len(pausable)
    plan.narration = (
        f"{trigger.message} {n} dependent action{'s' if n != 1 else ''} paused. "
        f"Dispatching retrieval; unrelated work continues."
    )
    return plan

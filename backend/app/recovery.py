"""Deviation detection and recovery.

OWNER: Nikki. Working implementation — extend in place.

The central design decision: ISOLATE, DON'T RESTART. When something goes wrong we pause
only the dependency chain that actually depends on the affected resource. Everything else
keeps running. That is the whole pitch, and it is why we never call state.reset() here.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .config import settings
from .models import Action, Predicate, now_iso

log = logging.getLogger("hive.recovery")

DEBOUNCE_TICKS = 2
DISCONNECT_GRACE = 8.0

# Below this a deterministic strategy is admitting it is only an approximation, and the model
# is asked to recompile the remaining objective instead. At or above it nothing is escalated —
# the model improves the answer, it never gates it.
ESCALATION_CONFIDENCE = 0.5

IN_FLIGHT = ("dispatched", "acknowledged", "executing", "awaiting_verification")


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
    # Actions a recompiled plan replaces. Unlike `cancel_action_ids` this may include work
    # that was verified and no longer holds — a delivery the world has since undone is not a
    # result any more, and leaving it verified would re-trigger the same deviation forever.
    supersede_action_ids: list[str] = field(default_factory=list)
    narration: str = ""
    confidence: float = 1.0
    source: str = "deterministic"  # deterministic | llm — which strategy produced this plan


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
    tail = (
        "Dispatching retrieval; unrelated work continues."
        if plan.insert_actions
        else "Nothing to retrieve towards — holding the chain."
    )
    plan.narration = (
        f"{trigger.message} {n} dependent action{'s' if n != 1 else ''} paused. {tail}"
    )

    # How much this strategy believes its own answer. A surgical retrieval is exactly right
    # for ONE displaced object. When several have moved at once it repairs one of them, leaves
    # a duplicate action per object behind, and says nothing about the order the rest of the
    # objective should now run in — which is the case worth handing to the model.
    displaced = set(displaced_objects(state)) | ({oid} if oid else set())
    if not plan.insert_actions:
        plan.confidence = 0.35  # no object, or nowhere to put it back
    elif len(displaced) > 1:
        plan.confidence = 0.45
    return plan


def displaced_objects(state: Any) -> list[str]:
    """Objects that are not where the plan has already established they belong.

    Counts a delivery HIVE verified and then lost, and an object that has landed somewhere
    that is neither its origin nor its target while its action is in flight. An item that
    simply has not moved yet is PENDING WORK, not displacement — same rule as `detect`.
    """
    out: list[str] = []
    for a in state.actions.values():
        oid = a.object_id
        if not oid or not a.target_zone or oid in out:
            continue
        obj = state.scene.by_id(oid)
        if obj is None or obj.held_by:
            continue
        if a.status == "verified":
            if obj.zone != a.target_zone:
                out.append(oid)
        elif a.status in IN_FLIGHT and obj.zone not in (a.target_zone, "field", a.origin_zone):
            out.append(oid)
    return sorted(out)


# ── escalation ──────────────────────────────────────────────────────────────


async def plan_recovery_async(trigger: DeviationTrigger, state: Any) -> RecoveryPlan:
    """`plan_recovery`, with the model as an escalation above it — never as a gate.

    The deterministic strategies are the floor and they always ship. Only when one of them
    reports `confidence < ESCALATION_CONFIDENCE` (several objects displaced at once, or
    nothing to aim a retrieval at) does the model get asked to recompile the *remaining*
    objective from where the world actually is. Timeout, exception, prose, an empty plan, a
    plan that drops work — any of them and the deterministic plan stands.

    This runs inside the orchestrator tick, so it must never raise. It doesn't.
    """
    plan = plan_recovery(trigger, state)
    if plan.confidence >= ESCALATION_CONFIDENCE:
        return plan
    if not settings.nvidia_api_key:
        return plan  # a keyless run IS the floor, by design, and stays quiet about it
    try:
        return await _escalate(plan, state)
    except Exception as exc:
        # Containment. A failed escalation is a log line and one warn event on the timeline;
        # the deterministic plan is already correct, just blunter.
        log.warning("recovery escalation failed: %s", exc)
        _emit(
            state,
            "replan_declined",
            f"Replanning unavailable ({type(exc).__name__}). Deterministic recovery stands.",
            kind=trigger.kind,
        )
        return plan


async def _escalate(plan: RecoveryPlan, state: Any) -> RecoveryPlan:
    """Ask the planner to recompile from current state; adopt it only if it is strictly usable."""
    trigger = plan.trigger
    owed = _owed_objects(state)
    if not owed:
        return plan  # the objective asks for nothing the world is missing — nothing to recompile

    _emit(
        state,
        "replanning",
        f"Recovery confidence {int(plan.confidence * 100)}% — recompiling the remaining "
        f"objective from the world as it is now.",
        kind=trigger.kind,
        confidence=plan.confidence,
        objects=sorted(owed),
    )

    from .planner.base import replan_from_state  # local: keeps the planner off the import path

    # `replan_from_state` owns its own 8s window and its own deterministic fallback, so there
    # is nothing to wrap here — only something to judge on the way out.
    try:
        result = await replan_from_state(_replan_view(state), _deviation_payload(trigger, state))
    except Exception as exc:
        log.warning("replan_from_state failed: %s", exc)
        _emit(
            state,
            "replan_declined",
            f"Replanning failed ({type(exc).__name__}). Deterministic recovery stands.",
            kind=trigger.kind,
        )
        return plan

    reason = _unusable(result, owed)
    if reason:
        _emit(
            state,
            "replan_declined",
            f"Replan declined: {reason}. Deterministic recovery stands.",
            kind=trigger.kind,
        )
        return plan
    return _adopt(result, plan, state)


def _unusable(result: Any, owed: set[str]) -> str | None:
    """Why this replan may not be trusted, or None if it may. Conservative on purpose."""
    if result is None or not getattr(result, "actions", None):
        return "the model returned no actions"
    if getattr(result, "source", "") != "llm":
        # base already fell through to its own deterministic compile — a whole fresh graph,
        # which is a worse answer than the surgical recovery we are already holding.
        return f"the model did not answer (fell back to {result.source})"
    covered = {a.object_id for a in result.actions if a.object_id}
    missing = sorted(owed - covered)
    if missing:
        return f"it dropped {', '.join(missing)}"
    return None


def _adopt(result: Any, plan: RecoveryPlan, state: Any) -> RecoveryPlan:
    """Turn a recompiled plan into a RecoveryPlan the orchestrator applies as one atomic step.

    The recompiled graph owns the remaining objective, so everything it replaces has to stop
    competing for the same objects: pending actions AND verified deliveries the world has
    since undone. Two live sets over one object is a deadlock — both hold `object:<id>`, so
    neither ever runs.
    """
    trigger = plan.trigger
    superseded: list[str] = []
    for a in state.actions.values():
        if not a.terminal or _regressed(a, state):
            superseded.append(a.id)

    used = set(state.actions)
    rename: dict[str, str] = {}
    fresh: list[Action] = []
    for a in result.actions:
        rid = _next_recovery_id(used)
        used.add(rid)
        rename[a.id] = rid
        a.id = rid
        a.is_recovery = True
        a.status = "queued"  # the orchestrator opens the ones whose dependencies allow it
        a.assigned_worker_id = None
        a.assignment_reason = ""
        a.instruction = None
        a.evidence = []
        a.retry_count = 0
        a.attempt = 0
        a.created_at = now_iso()
        if not a.lock_targets and a.object_id:
            a.lock_targets = [f"object:{a.object_id}"]
        if trigger.object_id and a.object_id == trigger.object_id:
            a.priority = max(a.priority, 105)  # the repair goes first
        fresh.append(a)
    for a in fresh:
        a.dependencies = [rename[d] for d in a.dependencies if d in rename]

    n_new, n_old = len(fresh), len(superseded)
    escalated = RecoveryPlan(
        trigger=trigger,
        supersede_action_ids=sorted(superseded),
        insert_actions=fresh,
        confidence=0.8,
        source=result.source,
        narration=(
            f"{trigger.message} Remaining objective recompiled from the current world state: "
            f"{n_new} action{'s' if n_new != 1 else ''} replacing {n_old}. "
            f"Verified deliveries that still hold are kept."
        ),
    )
    _emit(
        state,
        "plan_replanned",
        f"Objective recompiled from where the world actually is: {n_new} "
        f"action{'s' if n_new != 1 else ''} replacing {n_old}.",
        kind=trigger.kind,
        source=result.source,
        actions=n_new,
        superseded=n_old,
        notes=getattr(result, "notes", ""),
    )
    return escalated


def _regressed(a: Action, state: Any) -> bool:
    """A verified delivery the world no longer agrees with."""
    if a.status != "verified" or not a.object_id or not a.target_zone:
        return False
    obj = state.scene.by_id(a.object_id)
    return obj is not None and obj.zone != a.target_zone


def _next_recovery_id(used: set[str]) -> str:
    n = 1
    while f"r{n}" in used:
        n += 1
    return f"r{n}"


def _owed_objects(state: Any) -> set[str]:
    """Objects the objective still requires somewhere they are not — what a replan must cover."""
    from .planner.base import unsatisfied_deliveries

    goal = getattr(state, "goal", None)
    predicates = list(getattr(goal, "success_predicates", None) or [])
    return {oid for oid, _ in unsatisfied_deliveries(state.scene, predicates)}


def _replan_view(state: Any) -> Any:
    """List-shaped snapshot of the live state, which is what the planner reads.

    HiveState keys actions and workers by id; `replan_from_state` iterates them and wants
    `.goal` as well. Same one-adapter-beats-changing-either-side call as
    `orchestrator._SchedulerView`. The scene and goal are passed through by reference.
    """
    from .models import PlannerState

    workers, actions = state.workers, state.actions
    return PlannerState(
        scenario_id=getattr(getattr(state, "scenario", None), "id", None),
        goal=state.goal,
        workers=list(workers.values()) if isinstance(workers, dict) else list(workers),
        actions=list(actions.values()) if isinstance(actions, dict) else list(actions),
        scene=state.scene,
        locks=dict(getattr(state, "locks", None) or {}),
    )


def _deviation_payload(trigger: DeviationTrigger, state: Any) -> dict:
    return {
        "kind": trigger.kind,
        "message": trigger.message,
        "object_id": trigger.object_id,
        "worker_id": trigger.worker_id,
        "expected": trigger.expected,
        "observed": trigger.observed,
        "action_ids": list(trigger.action_ids),
        "verified_summary": _verified_summary(state),
    }


def _verified_summary(state: Any) -> str:
    """What is already true and must not be re-done. Only deliveries that STILL hold count."""
    parts = [
        f"{state.label_of(a.object_id)} in {state.zone_label(a.target_zone)}"
        for a in state.actions.values()
        if a.status == "verified" and a.object_id and a.target_zone and not _regressed(a, state)
    ]
    return ", ".join(parts) or "nothing yet"


def _emit(state: Any, type: str, message: str, *, severity: str = "warn", **meta: Any) -> None:
    """The tick's own emitter, guarded. Telemetry must never be the thing that breaks recovery."""
    emit = getattr(state, "emit_soon", None)
    if emit is None:
        return
    try:
        emit(type, message, severity=severity, **meta)
    except Exception as exc:  # pragma: no cover — an emitter that raises is already broken
        log.warning("emit(%s) failed: %s", type, exc)

"""Deviation detection + recovery planning — the differentiator. See Nikki.md §4.

Deterministic strategies always produce a valid plan; an LLM replanner (Zechariah's)
would only ever improve it, never gate it. The critical design decision throughout is
**isolate, don't restart**: pause only the dependency chain touched by a deviation,
and let everything else keep running.

Debounce: every detector requires 2 consecutive ticks (~0.5s) before firing, so a
single-frame vision glitch never triggers the big red overlay. `detect_all()` is the
debounced entry point the orchestrator calls each tick; the individual `detect_*`
functions are undebounced pure checks, useful to unit test directly.

Scheduler deadlocks are detected by `scheduler.Scheduler.tick()` itself (the 3-tick
empty-batch guard, Zechariah's) — the orchestrator turns that signal into a
`DeviationTrigger(kind="scheduler_deadlock", ...)` and calls `plan_recovery` directly;
`detect_all()` does not duplicate that detector."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.models import Action, ActionStatus, Predicate, WorkerStatus
from app import verifier


@dataclass
class DeviationTrigger:
    kind: str
    action_id: str | None = None
    worker_id: str | None = None
    object_id: str | None = None
    detail: str = ""
    human_readable: str = ""
    attempted_summary: str = ""


@dataclass
class RecoveryPlan:
    cancel_action_ids: list[str] = field(default_factory=list)
    pause_action_ids: list[str] = field(default_factory=list)
    release_locks: list[str] = field(default_factory=list)
    free_workers: list[str] = field(default_factory=list)
    insert_actions: list[Action] = field(default_factory=list)
    reassign: dict[str, str | None] = field(default_factory=dict)
    narration: str = ""
    expected_predicates: list[Predicate] = field(default_factory=list)
    confidence: float = 1.0


NARRATION = {
    "wrong_object_moved":
        "{object} detected in {actual} — expected {expected}. {n} dependent actions paused. Rerouting.",
    "object_missing":
        "{object} not visible. Dependent actions paused pending confirmation.",
    "left_target_zone":
        "{object} left {zone} before verification completed. Reissuing instruction.",
    "verification_regressed":
        "{object} left {zone} after verification. Confidence withdrawn. Corrective action dispatched.",
    "worker_timeout":
        "No confirmation from {callsign} within {t}s. Reissuing instruction, then reassigning.",
    "worker_disconnected":
        "{callsign} offline. Releasing held resources and reassigning by reachability and current load.",
    "worker_blocked":
        "{callsign} reported unable to complete the action. Reassigning.",
    "worker_paused":
        "{callsign} paused. Releasing their assignment.",
    "conflicting_manipulation":
        "Two actions targeting {object} at once. Serializing.",
    "scheduler_deadlock":
        "No viable responder for the remaining actions. Rebuilding the plan.",
}


def _dependents(action_id: str, state) -> list[Action]:
    seen: set[str] = set()
    frontier = [action_id]
    out: list[Action] = []
    while frontier:
        aid = frontier.pop()
        for a in state.actions:
            if aid in a.dependencies and a.id not in seen:
                seen.add(a.id)
                out.append(a)
                frontier.append(a.id)
    return out


def _dependents_of_object(object_id: str | None, state) -> list[Action]:
    if not object_id:
        return []
    roots = [a.id for a in state.actions if a.object_id == object_id or a.target_object_id == object_id]
    out: list[Action] = []
    for r in roots:
        out.extend(_dependents(r, state))
    return out


LIVE_ACTION_STATUSES = {
    ActionStatus.dispatched.value,
    ActionStatus.executing.value,
    ActionStatus.awaiting_verification.value,
}

# ── individual, undebounced detectors ───────────────────────────────────────────

def detect_wrong_object_moved(state) -> list[DeviationTrigger]:
    out = []
    active_object_ids = {a.object_id for a in state.actions if a.status in LIVE_ACTION_STATUSES}
    for obj in state.scene.objects:
        if obj.id in active_object_ids:
            continue
        if obj.id not in _LAST_ZONE:
            continue  # first sighting — nothing to compare against yet
        prev = _LAST_ZONE[obj.id]
        if prev != obj.zone:
            out.append(DeviationTrigger(
                kind="wrong_object_moved", object_id=obj.id,
                detail=f"moved {prev} -> {obj.zone} with no active action",
            ))
    return out


def detect_object_missing(state) -> list[DeviationTrigger]:
    out = []
    for obj in state.scene.objects:
        if obj.held_by:
            continue
        if obj.confidence < 0.25:
            out.append(DeviationTrigger(kind="object_missing", object_id=obj.id, detail="confidence decayed below 0.25"))
    return out


def detect_left_target_zone(state) -> list[DeviationTrigger]:
    out = []
    for a in state.actions:
        if a.status not in (ActionStatus.dispatched.value, ActionStatus.executing.value):
            continue
        if not a.target_zone or not a.object_id:
            continue
        obj = state.scene.by_id(a.object_id)
        if obj and obj.zone != a.target_zone and obj.zone != "field":
            out.append(DeviationTrigger(kind="left_target_zone", action_id=a.id, object_id=obj.id,
                                         detail=f"expected {a.target_zone}, observed {obj.zone}"))
    return out


def detect_verification_regressed(state) -> list[DeviationTrigger]:
    out = []
    for a in state.actions:
        if a.status != ActionStatus.verified.value:
            continue
        for pred in a.expected_predicates:
            if verifier.check_predicate(pred, state) is None:
                out.append(DeviationTrigger(kind="verification_regressed", action_id=a.id,
                                             object_id=pred.subject, detail="verified predicate no longer holds"))
                break
    return out


def detect_worker_disconnected(state) -> list[DeviationTrigger]:
    out = []
    now = datetime.now(timezone.utc)
    for w in state.workers:
        if w.connected or not w.current_action_id or w.last_seen_at is None:
            continue
        if (now - w.last_seen_at).total_seconds() > 8:
            out.append(DeviationTrigger(kind="worker_disconnected", worker_id=w.id, action_id=w.current_action_id))
    return out


def detect_worker_blocked(state) -> list[DeviationTrigger]:
    return [
        DeviationTrigger(kind="worker_blocked", action_id=a.id, worker_id=a.assigned_worker_id)
        for a in state.actions if a.status == ActionStatus.blocked.value
    ]


def detect_worker_paused(state) -> list[DeviationTrigger]:
    return [
        DeviationTrigger(kind="worker_paused", worker_id=w.id, action_id=w.current_action_id)
        for w in state.workers if w.status == WorkerStatus.paused.value and w.current_action_id
    ]


def detect_conflicting_manipulation(state) -> list[DeviationTrigger]:
    out = []
    active = [a for a in state.actions
              if a.status in (ActionStatus.dispatched.value, ActionStatus.executing.value) and a.object_id]
    seen: set[str] = set()
    for i, a in enumerate(active):
        for b in active[i + 1:]:
            if a.object_id == b.object_id and a.id not in seen and b.id not in seen:
                seen.add(a.id)
                seen.add(b.id)
                out.append(DeviationTrigger(kind="conflicting_manipulation", action_id=a.id, object_id=a.object_id,
                                             detail=f"also targeted by {b.id}"))
    return out


_DETECTORS = [
    detect_wrong_object_moved,
    detect_object_missing,
    detect_left_target_zone,
    detect_verification_regressed,
    detect_worker_disconnected,
    detect_worker_blocked,
    detect_worker_paused,
    detect_conflicting_manipulation,
]

_LAST_ZONE: dict[str, str] = {}
_DEBOUNCE: dict[str, int] = {}


def _debounce_key(t: DeviationTrigger) -> str:
    return f"{t.kind}:{t.action_id or ''}:{t.object_id or ''}:{t.worker_id or ''}"


def detect_all(state, *, threshold: int = 2) -> list[DeviationTrigger]:
    """Debounced. Call once per tick — a trigger only fires once it has been observed
    on `threshold` consecutive calls. A discrepancy that disappears before then (a
    one-frame glitch) is dropped silently, never fires."""
    candidates: list[DeviationTrigger] = []
    for detector in _DETECTORS:
        candidates.extend(detector(state))

    fired: list[DeviationTrigger] = []
    seen_keys = set()
    for t in candidates:
        key = _debounce_key(t)
        seen_keys.add(key)
        n = _DEBOUNCE.get(key, 0) + 1
        _DEBOUNCE[key] = n
        if n >= threshold:
            fired.append(t)
            _DEBOUNCE[key] = 0
            if t.kind == "wrong_object_moved" and t.object_id:
                obj = state.scene.by_id(t.object_id)
                if obj:
                    _LAST_ZONE[t.object_id] = obj.zone
    for key in list(_DEBOUNCE):
        if key not in seen_keys:
            del _DEBOUNCE[key]

    for obj in state.scene.objects:
        if obj.id not in _LAST_ZONE:
            _LAST_ZONE[obj.id] = obj.zone
    return fired


# ── recovery strategies — deterministic, always produce a valid plan ───────────

def retrieve_and_restore(trigger: DeviationTrigger, state) -> RecoveryPlan:
    dependents = _dependents_of_object(trigger.object_id, state)
    pause_ids = [a.id for a in dependents if a.status not in (ActionStatus.verified.value, ActionStatus.cancelled.value)]
    released = [lock for lock in state.locks if trigger.object_id and trigger.object_id in lock]
    obj = state.scene.by_id(trigger.object_id) if trigger.object_id else None
    recovery_action = Action(
        id=f"recovery_{trigger.object_id}_{len(state.actions) + 1}",
        type="move_to_zone",
        description=f"Retrieve {obj.display_label() if obj else trigger.object_id} and return it to position.",
        object_id=trigger.object_id,
        priority=105,
        is_recovery=True,
        lock_targets=[f"object:{trigger.object_id}"] if trigger.object_id else [],
        status="available",
    )
    return RecoveryPlan(
        pause_action_ids=pause_ids,
        release_locks=released,
        insert_actions=[recovery_action],
        narration=NARRATION["wrong_object_moved"].format(
            object=obj.display_label() if obj else trigger.object_id,
            actual=obj.zone if obj else "?", expected="its prior position", n=len(pause_ids),
        ),
        confidence=0.8,
    )


def pause_dependents_and_query(trigger: DeviationTrigger, state) -> RecoveryPlan:
    dependents = _dependents_of_object(trigger.object_id, state)
    pause_ids = [a.id for a in dependents if a.status not in (ActionStatus.verified.value, ActionStatus.cancelled.value)]
    obj = state.scene.by_id(trigger.object_id) if trigger.object_id else None
    return RecoveryPlan(
        pause_action_ids=pause_ids,
        narration=NARRATION["object_missing"].format(object=obj.display_label() if obj else trigger.object_id),
        confidence=0.6,
    )


def reissue_with_correction(trigger: DeviationTrigger, state) -> RecoveryPlan:
    obj = state.scene.by_id(trigger.object_id) if trigger.object_id else None
    a = state.action_by_id(trigger.action_id) if trigger.action_id else None
    return RecoveryPlan(
        reassign={a.id: a.assigned_worker_id} if a else {},
        narration=NARRATION["left_target_zone"].format(
            object=obj.display_label() if obj else trigger.object_id,
            zone=a.target_zone if a and a.target_zone else "?",
        ),
        confidence=0.7,
    )


def reverify_then_correct(trigger: DeviationTrigger, state) -> RecoveryPlan:
    a = state.action_by_id(trigger.action_id) if trigger.action_id else None
    obj = state.scene.by_id(trigger.object_id) if trigger.object_id else None
    return RecoveryPlan(
        reassign={a.id: a.assigned_worker_id} if a else {},
        narration=NARRATION["verification_regressed"].format(
            object=obj.display_label() if obj else (trigger.object_id or "object"),
            zone=a.target_zone if a and a.target_zone else "its zone",
        ),
        confidence=0.75,
    )


RETRY_LADDER_MAX = 3


def retry_then_reassign(trigger: DeviationTrigger, state) -> RecoveryPlan:
    a = state.action_by_id(trigger.action_id) if trigger.action_id else None
    w = state.worker_by_id(trigger.worker_id) if trigger.worker_id else None
    narration = NARRATION["worker_timeout"].format(
        callsign=w.callsign if w else "responder", t=a.timeout_seconds if a else 25,
    )
    if not a:
        return RecoveryPlan(narration=narration, confidence=0.5)

    attempt = a.retry_count + 1
    if attempt < RETRY_LADDER_MAX:
        return RecoveryPlan(
            reassign={a.id: a.assigned_worker_id},
            narration=narration,
            confidence=0.6,
        )
    return RecoveryPlan(
        reassign={a.id: None},
        free_workers=[a.assigned_worker_id] if a.assigned_worker_id else [],
        narration=narration,
        confidence=0.8,
    )


def reassign(trigger: DeviationTrigger, state) -> RecoveryPlan:
    a = state.action_by_id(trigger.action_id) if trigger.action_id else None
    w = state.worker_by_id(trigger.worker_id) if trigger.worker_id else None
    released = [lock for lock, holder in state.locks.items() if a and holder == a.id]
    return RecoveryPlan(
        reassign={a.id: None} if a else {},
        release_locks=released,
        free_workers=[w.id] if w else [],
        narration=NARRATION.get(trigger.kind, "Reassigning.").format(callsign=w.callsign if w else "responder"),
        confidence=0.85,
    )


def freeze_and_serialize(trigger: DeviationTrigger, state) -> RecoveryPlan:
    a = state.action_by_id(trigger.action_id) if trigger.action_id else None
    obj = state.scene.by_id(trigger.object_id) if trigger.object_id else None
    same_object = [x for x in state.actions if x.object_id == trigger.object_id]
    lowest_priority = min((x.priority for x in same_object), default=0)
    pause_ids = [a.id] if a and a.priority == lowest_priority else []
    return RecoveryPlan(
        pause_action_ids=pause_ids,
        narration=NARRATION["conflicting_manipulation"].format(object=obj.display_label() if obj else trigger.object_id),
        confidence=0.7,
    )


def rebuild_remaining_graph(trigger: DeviationTrigger, state) -> RecoveryPlan:
    stuck_ids = [a.id for a in state.actions if a.status in (ActionStatus.queued.value, ActionStatus.blocked.value)]
    return RecoveryPlan(
        reassign={aid: None for aid in stuck_ids},
        narration=NARRATION["scheduler_deadlock"],
        confidence=0.4,
    )


STRATEGIES = {
    "wrong_object_moved": retrieve_and_restore,
    "object_missing": pause_dependents_and_query,
    "left_target_zone": reissue_with_correction,
    "verification_regressed": reverify_then_correct,
    "worker_timeout": retry_then_reassign,
    "worker_disconnected": reassign,
    "worker_blocked": reassign,
    "worker_paused": reassign,
    "conflicting_manipulation": freeze_and_serialize,
    "scheduler_deadlock": rebuild_remaining_graph,
}


def plan_recovery(trigger: DeviationTrigger, state) -> RecoveryPlan:
    strategy = STRATEGIES.get(trigger.kind)
    if not strategy:
        return RecoveryPlan(narration=f"Unhandled deviation: {trigger.kind}", confidence=0.0)
    return strategy(trigger, state)


async def apply_recovery_plan(plan: RecoveryPlan, state) -> None:
    """Mutates state per the plan and emits the narration. Isolate, don't restart:
    only the affected branch is paused/cancelled/reassigned — everything else keeps
    running untouched."""
    for aid in plan.cancel_action_ids:
        a = state.action_by_id(aid)
        if a:
            a.status = ActionStatus.cancelled.value

    for aid in plan.pause_action_ids:
        a = state.action_by_id(aid)
        if a and a.status not in (ActionStatus.verified.value, ActionStatus.cancelled.value):
            a.status = ActionStatus.blocked.value

    for lock in plan.release_locks:
        state.locks.pop(lock, None)

    for wid in plan.free_workers:
        w = state.worker_by_id(wid)
        if w:
            w.status = WorkerStatus.ready.value
            w.current_action_id = None
            w.confidence = min(w.confidence, 0.6)

    for action in plan.insert_actions:
        state.actions.append(action)

    for aid, worker_id in plan.reassign.items():
        a = state.action_by_id(aid)
        if not a:
            continue
        if worker_id is None:
            a.assigned_worker_id = None
            a.status = ActionStatus.available.value
            a.retry_count += 1
        else:
            a.assigned_worker_id = worker_id
            a.status = ActionStatus.assigned.value
            a.retry_count += 1

    if plan.narration:
        await state.emit("recovery_started", plan.narration, severity="warn", metadata={"confidence": plan.confidence})

"""The 4Hz tick — the orchestration core (Ojas.md §4, §6).

Fixed order, do not reorder:
drain_inbox -> verify_pending -> complete_actions -> unlock_dependents ->
detect_deviations -> detect_timeouts -> run_scheduler -> dispatch -> check_goal ->
flush_broadcasts

Only `orchestrator.py` mutates `Action.status` / `Worker.status` / `locks`
(docs/CONTRACTS.md §1) — `scheduler.py` and `recovery.py` return decisions, this file
applies them. Vision's own async loop (main.py's `_vision_loop`) mutates
`state.scene`/`state.world` independently; this tick never touches the camera."""

from __future__ import annotations

import asyncio
import logging
import time

from app import recovery, scheduler, verifier
from app.config import settings
from app.models import (
    Action,
    ActionStatus,
    Evidence,
    EvidenceKind,
    EVIDENCE_WEIGHTS,
    Goal,
    Instruction,
    WorkerStatus,
    utc_now,
)
from app.state import state
from app.websocket_manager import ws

log = logging.getLogger("hive.orchestrator")


# ── inbox handlers ───────────────────────────────────────────────────────────────

def drain_inbox() -> None:
    while state.inbox:
        msg = state.inbox.pop(0)
        handler = HANDLERS.get(msg.type)
        if not handler:
            continue
        try:
            handler(msg)
        except Exception:
            log.exception("handler %s failed", msg.type)


def on_worker_completed(msg) -> None:
    a = state.action_by_id(msg.payload.get("action_id"))
    if not a or a.assigned_worker_id != msg.worker_id:
        return
    if a.status in (ActionStatus.verified.value, ActionStatus.cancelled.value, ActionStatus.failed.value):
        return
    evidence = state.evidence.setdefault(a.id, [])
    if any(e.kind == EvidenceKind.worker_report.value for e in evidence):
        return
    evidence.append(Evidence(
        kind=EvidenceKind.worker_report.value,
        confidence=msg.payload.get("confidence") or 1.0,
        weight=EVIDENCE_WEIGHTS[EvidenceKind.worker_report.value],
    ))
    a.status = ActionStatus.awaiting_verification.value


def on_worker_acknowledged(msg) -> None:
    a = state.action_by_id(msg.payload.get("action_id"))
    if a and a.status == ActionStatus.dispatched.value:
        a.status = ActionStatus.acknowledged.value


def on_worker_blocked(msg) -> None:
    a = state.action_by_id(msg.payload.get("action_id"))
    if a:
        a.status = ActionStatus.blocked.value


def on_worker_pause(msg) -> None:
    w = state.worker_by_id(msg.worker_id)
    if w:
        w.status = WorkerStatus.paused.value


def on_worker_ready(msg) -> None:
    w = state.worker_by_id(msg.worker_id)
    if w and w.status != WorkerStatus.executing.value:
        w.status = WorkerStatus.ready.value


def on_worker_emergency(msg) -> None:
    state.execution_status = "emergency"
    asyncio.create_task(state.emit(
        "emergency_stop", f"Emergency stop triggered by {msg.worker_id or 'host'}.", severity="critical",
    ))


def on_host_manual_verify(msg) -> None:
    action_id = msg.payload.get("action_id")
    if not msg.payload.get("verified", True):
        return
    a = state.action_by_id(action_id)
    if not a:
        return
    state.evidence.setdefault(a.id, []).append(Evidence(
        kind=EvidenceKind.host_override.value, confidence=1.0,
        weight=EVIDENCE_WEIGHTS[EvidenceKind.host_override.value],
    ))


def on_host_start_execution(msg) -> None:
    if state.actions:
        state.execution_status = "executing"


def on_host_pause_all(msg) -> None:
    if state.execution_status == "executing":
        state.execution_status = "paused"


def on_host_resume_all(msg) -> None:
    if state.execution_status == "paused":
        state.execution_status = "executing"


def on_host_reset(msg) -> None:
    asyncio.create_task(state.reset(msg.payload.get("scenario_id")))


def on_host_compile_goal(msg) -> None:
    asyncio.create_task(_compile_goal_task(msg.payload.get("text", ""), msg.payload.get("scenario_id")))


async def _compile_goal_task(text: str, scenario_id: str | None) -> None:
    """Calls Zechariah's planner (the only entry point: `compile_goal`) and installs
    the result. Grounding-ambiguous plans emit `grounding_ambiguous` themselves
    (planner/base.py) and return a pending PlanResult — resuming that with
    `host_bind_object` is grounding/host-UI scope, not wired here yet."""
    from app.planner.base import NotReady, compile_goal

    def _emit(kind: str, payload: dict):
        return state.emit(kind, kind, metadata=payload)

    try:
        result = await compile_goal(text, state.scene, state.workers, scenario_id=scenario_id, emit=_emit)
    except NotReady as exc:
        await state.emit("error_event", str(exc), severity="warn")
        return
    except Exception:
        log.exception("compile_goal failed")
        await state.emit("error_event", "Goal compilation failed unexpectedly.", severity="warn")
        return

    if result.is_pending:
        return

    state.goal = Goal(
        raw_text=text, normalized_intent=result.normalized_intent, status="compiled",
        success_predicates=result.success_predicates, plan_source=result.source,
        planner_notes=result.notes,
    )
    state.actions = result.actions
    state.scenario_id = scenario_id or state.scenario_id
    await state.emit("plan_compiled", result.notes or "Plan compiled.",
                      metadata={"source": result.source, "stats": result.stats})
    await ws.broadcast_snapshot()


HANDLERS = {
    "worker_completed": on_worker_completed,
    "worker_acknowledged": on_worker_acknowledged,
    "worker_blocked": on_worker_blocked,
    "worker_pause": on_worker_pause,
    "worker_ready": on_worker_ready,
    "worker_emergency": on_worker_emergency,
    "host_manual_verify": on_host_manual_verify,
    "host_start_execution": on_host_start_execution,
    "host_pause_all": on_host_pause_all,
    "host_resume_all": on_host_resume_all,
    "host_reset": on_host_reset,
    "host_compile_goal": on_host_compile_goal,
}


# ── tick steps ───────────────────────────────────────────────────────────────────

def verify_pending() -> None:
    for a in state.actions:
        if a.status != ActionStatus.awaiting_verification.value:
            continue
        result = verifier.evaluate(a, state)
        if result.verified:
            a.status = ActionStatus.verified.value
            state.mark_world_dirty()


def complete_actions() -> None:
    for a in state.actions:
        if a.status == ActionStatus.verified.value and a.completed_at is None:
            a.completed_at = utc_now()
            for lock in a.lock_targets:
                if state.locks.get(lock) == a.id:
                    del state.locks[lock]
            w = state.worker_by_id(a.assigned_worker_id) if a.assigned_worker_id else None
            if w:
                w.current_action_id = None
                w.status = WorkerStatus.ready.value


def unlock_dependents() -> None:
    for a in scheduler.unlock_dependents(state, []):
        a.status = ActionStatus.available.value


async def detect_deviations() -> None:
    for trigger in recovery.detect_all(state):
        plan = recovery.plan_recovery(trigger, state)
        await recovery.apply_recovery_plan(plan, state)


async def detect_timeouts() -> None:
    now = utc_now()
    for a in state.actions:
        if a.status not in (ActionStatus.dispatched.value, ActionStatus.executing.value):
            continue
        if not a.dispatched_at:
            continue
        if (now - a.dispatched_at).total_seconds() > a.timeout_seconds:
            trigger = recovery.DeviationTrigger(kind="worker_timeout", action_id=a.id,
                                                 worker_id=a.assigned_worker_id, detail="timeout")
            plan = recovery.plan_recovery(trigger, state)
            await recovery.apply_recovery_plan(plan, state)


async def run_scheduler() -> None:
    """Zechariah's scheduler: pure scoring + a set-per-tick batch. This is the only
    place that turns its decisions into mutation (assignment + lock acquisition)."""
    result = scheduler.assign_actions(state)
    for action, assignment in result.batch:
        action.assigned_worker_id = assignment.worker_id
        action.assignment_reason = assignment.reason
        action.status = ActionStatus.assigned.value
        for lock in action.lock_targets:
            state.locks[lock] = action.id
        w = state.worker_by_id(assignment.worker_id)
        if w:
            w.assignment_count += 1

    if result.deadlock:
        trigger = recovery.DeviationTrigger(kind="scheduler_deadlock", detail=result.deadlock,
                                             human_readable=result.deadlock)
        plan = recovery.plan_recovery(trigger, state)
        await recovery.apply_recovery_plan(plan, state)


def dispatch() -> None:
    for a in state.actions:
        if a.status != ActionStatus.assigned.value or not a.assigned_worker_id:
            continue
        w = state.worker_by_id(a.assigned_worker_id)
        if not w:
            continue
        attempt = a.retry_count + 1
        a.instruction = Instruction(
            id=f"instr_{a.id}_{attempt}",
            action_id=a.id,
            worker_id=w.id,
            display_text=a.description.upper(),
            spoken_text=a.description,
            expected_duration_seconds=a.timeout_seconds,
        )
        a.status = ActionStatus.dispatched.value
        a.dispatched_at = utc_now()
        w.status = WorkerStatus.assigned.value
        w.current_action_id = a.id
        asyncio.create_task(ws.send(w.id, "instruction_created", a.instruction.model_dump(mode="json")))


def check_goal() -> None:
    if not state.goal or not state.actions:
        return
    if all(a.status == ActionStatus.verified.value for a in state.actions):
        state.goal.status = "completed"
        state.execution_status = "completed"


async def flush_broadcasts() -> None:
    if state.consume_dirty():
        await ws.broadcast("world_state_changed", state.world.model_dump(mode="json"))


async def tick() -> None:
    if state.execution_status in ("idle", "paused", "completed", "emergency"):
        await flush_broadcasts()
        return
    drain_inbox()
    verify_pending()
    complete_actions()
    unlock_dependents()
    await detect_deviations()
    await detect_timeouts()
    await run_scheduler()
    dispatch()
    check_goal()
    await flush_broadcasts()


async def run_forever() -> None:
    interval = 1.0 / settings.tick_hz
    while True:
        t0 = time.perf_counter()
        try:
            await tick()
        except Exception:
            log.exception("tick failed")
            await state.emit("system_warning", "Internal exception contained. Coordination continuing.",
                              severity="warn")
        await asyncio.sleep(max(0.0, interval - (time.perf_counter() - t0)))

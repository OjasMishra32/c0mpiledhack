"""The 4Hz tick. See docs/CONTRACTS.md and Ojas.md §6.

Fixed order, do not reorder:
drain_inbox -> world_model.refresh -> verifier.evaluate -> complete_actions ->
unlock_dependents -> detect_deviations -> detect_timeouts -> run_recovery ->
assign_actions -> dispatch -> check_goal -> flush_broadcasts

Steps 3-11 are synchronous pure-ish functions; only 1 and 12 touch I/O.

NOTE: world_model.refresh (Steven), assign_actions' real scoring (Zechariah), and the
planner (Zechariah) are not built yet. This file wires Nikki's verifier/recovery into
a real tick loop and stubs the rest minimally so the app runs end to end in
simulation/demo mode without those pieces."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from app.config import settings
from app.models import ActionStatus, Instruction, WorkerStatus
from app.state import state
from app.websocket_manager import ws
from app import recovery, verifier

log = logging.getLogger("hive.orchestrator")


def drain_inbox() -> None:
    while state.inbox:
        msg = state.inbox.popleft()
        handler = HANDLERS.get(msg.type)
        if not handler:
            continue
        try:
            handler(msg)
        except Exception:
            log.exception("handler %s failed", msg.type)


def on_worker_completed(msg) -> None:
    a = state.actions.get(msg.payload.get("action_id"))
    if not a:
        return
    if a.status in (ActionStatus.verified, ActionStatus.cancelled, ActionStatus.failed):
        return
    if a.assigned_worker_id != msg.worker_id:
        return
    if any(e.kind.value == "worker_report" for e in a.evidence):
        return
    from app.models import Evidence, EvidenceKind

    a.evidence.append(Evidence(
        kind=EvidenceKind.worker_report,
        confidence=msg.payload.get("confidence") or 1.0,
        weight=0.30,
    ))
    a.status = ActionStatus.awaiting_verification


def on_worker_blocked(msg) -> None:
    a = state.actions.get(msg.payload.get("action_id"))
    if a:
        a.status = ActionStatus.blocked


def on_worker_pause(msg) -> None:
    w = state.workers.get(msg.worker_id)
    if w:
        w.status = WorkerStatus.paused


def on_worker_ready(msg) -> None:
    w = state.workers.get(msg.worker_id)
    if w and w.status != WorkerStatus.executing:
        w.status = WorkerStatus.ready


HANDLERS = {
    "worker_completed": on_worker_completed,
    "worker_blocked": on_worker_blocked,
    "worker_pause": on_worker_pause,
    "worker_ready": on_worker_ready,
}


def verify_pending() -> None:
    for a in state.actions.values():
        if a.status != ActionStatus.awaiting_verification:
            continue
        result = verifier.evaluate(a, state)
        if result.verified:
            a.status = ActionStatus.verified
            state.mark_world_dirty()


def complete_actions() -> None:
    for a in state.actions.values():
        if a.status == ActionStatus.verified and a.completed_at is None:
            from app.models import now_iso

            a.completed_at = now_iso()
            for lock in a.lock_targets:
                if state.locks.get(lock) == a.id:
                    del state.locks[lock]
            w = state.workers.get(a.assigned_worker_id or "")
            if w:
                w.current_action_id = None
                w.status = WorkerStatus.ready
            state.metrics.actions_verified += 1


def unlock_dependents() -> None:
    for a in state.actions.values():
        if a.status != ActionStatus.queued:
            continue
        deps = [state.actions.get(d) for d in a.dependencies]
        if all(d is not None and d.status == ActionStatus.verified for d in deps):
            a.status = ActionStatus.available


async def detect_deviations() -> None:
    triggers = recovery.detect_all(state)
    for trigger in triggers:
        state.metrics.deviations += 1
        plan = recovery.plan_recovery(trigger, state)
        await recovery.apply_recovery_plan(plan, state)


async def detect_timeouts() -> None:
    now = datetime.now(timezone.utc)
    for a in state.actions.values():
        if a.status not in (ActionStatus.dispatched, ActionStatus.executing):
            continue
        if not a.dispatched_at:
            continue
        dispatched = datetime.fromisoformat(a.dispatched_at)
        if (now - dispatched).total_seconds() > a.timeout_seconds:
            trigger = recovery.DeviationTrigger(kind="worker_timeout", action_id=a.id,
                                                 worker_id=a.assigned_worker_id, detail="timeout")
            plan = recovery.plan_recovery(trigger, state)
            await recovery.apply_recovery_plan(plan, state)


def assign_actions() -> list[tuple[str, str]]:
    """Stub scheduler (Zechariah's real scorer is not built yet): assign to the first
    idle, connected, non-locked-conflicting worker."""
    assigned: list[tuple[str, str]] = []
    claimed_locks = set(state.locks.keys())
    claimed_workers: set[str] = set()
    available = sorted(
        (a for a in state.actions.values() if a.status == ActionStatus.available),
        key=lambda a: -a.priority,
    )
    for a in available:
        if claimed_locks & set(a.lock_targets):
            continue
        candidate = next(
            (w for w in state.workers.values()
             if w.connected and w.status == WorkerStatus.ready and w.id not in claimed_workers),
            None,
        )
        if not candidate:
            a.blocked_reason = "no available responder"
            continue
        a.assigned_worker_id = candidate.id
        a.assignment_reason = f"{candidate.callsign} selected: first available responder."
        a.status = ActionStatus.assigned
        for lock in a.lock_targets:
            state.locks[lock] = a.id
        claimed_locks |= set(a.lock_targets)
        claimed_workers.add(candidate.id)
        assigned.append((a.id, candidate.id))
    return assigned


def dispatch() -> None:
    from app.models import now_iso

    for a in list(state.actions.values()):
        if a.status != ActionStatus.assigned or not a.assigned_worker_id:
            continue
        w = state.workers[a.assigned_worker_id]
        a.attempt += 1
        a.instruction = Instruction(
            id=f"instr_{a.id}_{a.attempt}",
            action_id=a.id,
            worker_id=w.id,
            display_text=a.description.upper(),
            spoken_text=a.description,
            expected_duration_seconds=a.timeout_seconds,
        )
        a.status = ActionStatus.dispatched
        a.dispatched_at = now_iso()
        w.status = WorkerStatus.assigned
        w.current_action_id = a.id
        w.assignment_count += 1
        asyncio.create_task(ws.send(w.id, "instruction_created", a.instruction.model_dump()))


def check_goal() -> None:
    if not state.goal:
        return
    if not state.actions:
        return
    if all(a.status == ActionStatus.verified for a in state.actions.values()):
        state.goal.status = state.goal.status.__class__.completed
        state.execution_status = "completed"
        from app.models import now_iso

        state.metrics.completed_at = now_iso()


async def flush_broadcasts() -> None:
    if state._world_dirty:
        await ws.broadcast("world_state_changed", state.world.model_dump())
        state._world_dirty = False


async def tick() -> None:
    if state.execution_status in ("idle", "paused", "completed", "emergency"):
        await flush_broadcasts()
        return
    drain_inbox()
    # world_model.refresh(state) — Steven's tracker, not built yet
    verify_pending()
    complete_actions()
    unlock_dependents()
    await detect_deviations()
    await detect_timeouts()
    assign_actions()
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

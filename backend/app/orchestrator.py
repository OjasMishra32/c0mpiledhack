"""The orchestration loop — one clock, one writer, never raises.

Only this module mutates Action.status, Worker.status, and locks. Everything else queues
an inbox message and waits its turn. That single rule removes the entire class of
"two things changed the same action in the same tick" bugs.

Steps 2-11 of tick() are synchronous, so tests can call tick() directly.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from . import recovery as recovery_engine
from . import scheduler, verifier
from .config import settings
from .models import Action, Evidence, Instruction, now_iso
from .state import state
from .vision import world_model
from .websocket_manager import ws

log = logging.getLogger("hive.orchestrator")

DISPATCHABLE = ("dispatched", "acknowledged", "executing")


# ── main loop ───────────────────────────────────────────────────────────────


async def run_forever() -> None:
    interval = 1.0 / max(settings.tick_hz, 0.5)
    while True:
        t0 = time.perf_counter()
        try:
            await tick()
        except Exception:
            # Not optional. A traceback here would be a dead demo; instead it is a shrug.
            log.exception("tick failed")
            state.emit_soon(
                "system_warning",
                "Internal exception contained. Coordination continuing.",
                severity="warn",
            )
        await asyncio.sleep(max(0.0, interval - (time.perf_counter() - t0)))


async def tick() -> None:
    await drain_inbox_async()

    if state.execution_status in ("idle", "paused", "emergency"):
        if world_model.refresh(state):
            ws.mark_world_dirty()
        await flush_outbox()
        await ws.flush()
        return

    if state.execution_status == "completed":
        # Keep watching after the objective is met. A completed plan is a claim about the
        # world, and the world can still change — an operator moving a verified item must
        # reopen the objective rather than be silently ignored.
        if world_model.refresh(state):
            ws.mark_world_dirty()
        handle_deviations()
        if state.actions_with_status("queued", "available", "assigned", *DISPATCHABLE):
            state.execution_status = "executing"
            if state.goal:
                state.goal.status = "executing"
            state.emit_soon(
                "goal_reopened",
                "Objective reopened: the world no longer matches the verified state.",
                severity="critical",
            )
        state.tick_metrics()
        await flush_outbox()
        await ws.flush()
        return

    if world_model.refresh(state):
        ws.mark_world_dirty()

    evaluate_verifications()
    complete_actions()
    unlock_dependents()
    handle_deviations()
    schedule_actions()
    dispatch()
    check_goal()

    state.tick_metrics()
    await flush_outbox()
    await ws.flush()


# ── 1. inbox ────────────────────────────────────────────────────────────────


async def drain_inbox_async() -> None:
    """Three workers tapping Completed at the same instant become three ORDERED inbox
    entries, not three concurrent mutations. This is the race-condition answer.

    Worker messages are synchronous handlers. Host commands may need to await (planning,
    reasoning bursts), so they are dispatched here rather than inside the state machine.
    """
    from .host_commands import HOST_HANDLERS

    while state.inbox:
        msg = state.inbox.popleft()
        try:
            if msg.type in HOST_HANDLERS:
                await HOST_HANDLERS[msg.type](msg.payload or {})
                continue
            handler = HANDLERS.get(msg.type)
            if handler:
                handler(msg.payload or {}, msg.worker_id)
        except Exception:
            log.exception("handler %s", msg.type)


def drain_inbox() -> None:
    """Synchronous variant for tests that only push worker messages."""
    while state.inbox:
        msg = state.inbox.popleft()
        handler = HANDLERS.get(msg.type)
        if handler:
            try:
                handler(msg.payload or {}, msg.worker_id)
            except Exception:
                log.exception("handler %s", msg.type)


# ── 3-4. verification ───────────────────────────────────────────────────────


def evaluate_verifications() -> None:
    for a in state.actions_with_status("awaiting_verification", *DISPATCHABLE):
        result = verifier.evaluate(a, state)
        a.confidence = result.score
        if result.verified and a.status in ("awaiting_verification", *DISPATCHABLE):
            _mark_verified(a, result.summary)


def _mark_verified(a: Action, summary: str) -> None:
    a.status = "verified"
    a.completed_at = now_iso()
    state.free_locks_for(a.id)
    w = state.worker(a.assigned_worker_id)
    if w:
        w.current_action_id = None
        w.status = "ready" if w.connected and w.available else w.status
    what = state.label_of(a.object_id) if a.object_id else a.description
    where = f" at {state.zone_label(a.target_zone)}" if a.target_zone else ""
    state.emit_soon(
        "action_verified",
        f"{what.capitalize()} confirmed{where}. {summary}.",
        severity="success",
        action_id=a.id,
        confidence=a.confidence,
    )
    ws.mark_actions_dirty()
    ws.mark_workers_dirty()


def complete_actions() -> None:
    """Anything verified must hold no locks and own no worker."""
    for a in state.actions_with_status("verified", "cancelled"):
        state.free_locks_for(a.id)


# ── 5. dependency unlocking ─────────────────────────────────────────────────


def unlock_dependents() -> None:
    for a in state.actions.values():
        if a.status != "queued":
            continue
        deps = [state.actions.get(d) for d in a.dependencies]
        if all(d is not None and d.status == "verified" for d in deps):
            a.status = "available"
            ws.mark_actions_dirty()


# ── 6-8. deviation + recovery ───────────────────────────────────────────────


def handle_deviations() -> None:
    triggers = recovery_engine.detect(state)
    if not triggers:
        return
    for trigger in triggers[:2]:  # never storm the UI
        plan = recovery_engine.plan_recovery(trigger, state)
        _apply_recovery(plan)


def _apply_recovery(plan: recovery_engine.RecoveryPlan) -> None:
    t = plan.trigger
    state.metrics.deviations += 1

    state.emit_soon(
        "deviation_detected",
        t.message,
        severity="critical",
        kind=t.kind,
        expected=t.expected,
        observed=t.observed,
        action_ids=t.action_ids,
        object_id=t.object_id,
    )

    for aid in plan.cancel_action_ids:
        a = state.actions.get(aid)
        if a and not a.terminal:
            a.status = "cancelled"
            state.free_locks_for(aid)
            _release_worker(a)

    # Pause ONLY the dependency chain that depends on the affected resource.
    # Everything else keeps running — this is the whole pitch.
    for aid in plan.pause_action_ids:
        a = state.actions.get(aid)
        if a and a.status in ("available", "assigned", *DISPATCHABLE):
            _cancel_instruction(a, "paused pending recovery")
            a.status = "queued"
            a.blocked_reason = "paused pending recovery"
            state.free_locks_for(aid)
            _release_worker(a)

    for key in plan.release_locks:
        state.locks.pop(key, None)

    for wid in plan.free_workers:
        w = state.worker(wid)
        if w:
            w.current_action_id = None
            if w.status not in ("unavailable", "paused", "emergency", "disconnected"):
                w.status = "ready"

    for aid, wid in plan.reassign.items():
        a = state.actions.get(aid)
        if not a:
            continue
        if wid is None:
            prev = a.assigned_worker_id
            _cancel_instruction(a, "reassigning")
            a.assigned_worker_id = None
            a.status = "available" if not a.dependencies or _deps_met(a) else "queued"
            a.retry_count += 1
            _release_worker_id(prev)
            if prev:
                pw = state.worker(prev)
                if pw and t.kind == "worker_timeout":
                    pw.confidence = 0.6  # scheduler's risk penalty now avoids them
                state.metrics.reassignments += 1
        else:
            a.status = "assigned"  # reissue to the same worker with higher urgency
            a.retry_count += 1

    for new in plan.insert_actions:
        if new.id not in state.actions:
            state.actions[new.id] = new
            new.status = "available"
            state.metrics.actions_total = len(state.actions)
            state.emit_soon(
                "recovery_action_created",
                f"Recovery action created: {new.description}",
                severity="warn",
                action_id=new.id,
            )

    state.metrics.recoveries += 1
    state.emit_soon("recovery_started", plan.narration, severity="warn", kind=t.kind)
    ws.mark_actions_dirty()
    ws.mark_workers_dirty()


def _deps_met(a: Action) -> bool:
    return all((d := state.actions.get(dep)) and d.status == "verified" for dep in a.dependencies)


def _release_worker(a: Action) -> None:
    _release_worker_id(a.assigned_worker_id)


def _release_worker_id(wid: str | None) -> None:
    w = state.worker(wid)
    if w and w.current_action_id:
        w.current_action_id = None
        if w.status in ("assigned", "executing"):
            w.status = "ready" if w.connected and w.available else "disconnected"


def _cancel_instruction(a: Action, reason: str) -> None:
    if a.instruction and a.assigned_worker_id:
        _queue_send(
            a.assigned_worker_id,
            "instruction_cancelled",
            {"instruction_id": a.instruction.id, "action_id": a.id, "reason": reason},
        )
    a.instruction = None


# ── 9. scheduling ───────────────────────────────────────────────────────────


def schedule_actions() -> None:
    for action, assignment in scheduler.select_batch(state):
        action.assigned_worker_id = assignment.worker_id
        action.assignment_reason = assignment.reason
        action.status = "assigned"
        w = state.worker(assignment.worker_id)
        if w:
            w.assignment_count += 1
        for key in action.lock_targets:
            state.locks[key] = action.id
        obj = state.scene.by_id(action.object_id) if action.object_id else None
        if obj:
            obj.locked_by = action.id
        state.emit_soon(
            "action_assigned",
            assignment.reason,
            severity="info",
            action_id=action.id,
            worker_id=assignment.worker_id,
            factors=assignment.factors,
        )
        ws.mark_actions_dirty()


# ── 10. dispatch ────────────────────────────────────────────────────────────


def dispatch() -> None:
    for a in state.actions_with_status("assigned"):
        w = state.worker(a.assigned_worker_id)
        if not w:
            a.status = "available"
            continue
        a.attempt += 1
        obj = state.scene.by_id(a.object_id) if a.object_id else None
        a.origin_zone = obj.zone if obj else None
        a.instruction = build_instruction(a, w)
        a.status = "dispatched"
        a.dispatched_at = now_iso()
        w.status = "assigned"
        w.current_action_id = a.id
        _queue_send(w.id, "instruction_created", a.instruction.model_dump())
        state.emit_soon(
            "instruction_created",
            f"{w.callsign}: {a.instruction.display_text}",
            severity="info",
            action_id=a.id,
            worker_id=w.id,
        )
        ws.mark_actions_dirty()
        ws.mark_workers_dirty()


def build_instruction(a: Action, w: Any) -> Instruction:
    """Instruction ids are unique per (action, attempt). That uniqueness is what makes
    speak-once work on the phone: a re-render is silent, a genuine re-issue speaks."""
    label = state.label_of(a.object_id) if a.object_id else ""
    zone = state.zone_label(a.target_zone) if a.target_zone else ""
    urgency = "critical" if a.is_recovery or a.retry_count >= 2 else ("high" if a.retry_count else "normal")

    if a.type in ("place_in_zone", "move_to_zone"):
        display = f"MOVE THE {label.upper()} TO {zone.upper()}"
        spoken = f"Move the {label} to {zone}."
        detail = "Set it down inside the marked area, then step back."
    elif a.type == "hold":
        display = f"HOLD THE {label.upper()} STEADY"
        spoken = f"Hold the {label} steady. Do not let it move."
        detail = "Keep it still until told to release."
    elif a.type == "release":
        display = f"RELEASE THE {label.upper()}"
        spoken = f"Release the {label} and move your hand back."
        detail = "Step back from the workspace."
    elif a.type == "pick_up":
        display = f"PICK UP THE {label.upper()}"
        spoken = f"Pick up the {label}."
        detail = "Hold it and wait for the next instruction."
    elif a.type == "inspect":
        display = f"CHECK {zone.upper()}" if zone else "CHECK THE AREA"
        spoken = f"Check that {zone} is correct, then confirm." if zone else "Check the area, then confirm."
        detail = "Confirm when everything is in place."
    else:
        display = a.description.upper()
        spoken = a.description
        detail = ""

    if a.retry_count:
        spoken = f"{w.callsign}. {spoken}"

    return Instruction(
        id=f"instr_{a.id}_{a.attempt}",
        action_id=a.id,
        worker_id=w.id,
        display_text=display,
        spoken_text=spoken,
        detail_text=detail,
        urgency=urgency,  # type: ignore[arg-type]
        expected_duration_seconds=max(6, a.timeout_seconds - 6),
        requires_verification=True,
        correction_text=("Correction. " + spoken) if a.is_recovery else None,
    )


_outbox: list[tuple[str, str, Any]] = []


def _queue_send(worker_id: str, type: str, payload: Any) -> None:
    _outbox.append((worker_id, type, payload))


async def flush_outbox() -> None:
    global _outbox
    pending, _outbox = _outbox, []
    for wid, type, payload in pending:
        await ws.send(wid, type, payload)


# ── 11. goal completion ─────────────────────────────────────────────────────


def check_goal() -> None:
    if not state.goal or state.goal.status == "completed":
        return
    if not state.actions:
        return
    if any(a.status not in ("verified", "cancelled") for a in state.actions.values()):
        return

    state.goal.status = "completed"
    state.execution_status = "completed"
    state.metrics.completed_at = now_iso()
    m = state.metrics
    lex = state.scenario.lexicon.get("complete", "OBJECTIVE COMPLETE")
    state.emit_soon(
        "goal_completed",
        f"{lex}. {m.actions_verified} actions verified · {m.parallel_peak} peak parallel · "
        f"{m.recoveries} recovery · {m.reassignments} reassignment · "
        f"{int(m.avg_confidence * 100)}% mean confidence.",
        severity="success",
        report=m.model_dump(),
    )
    ws.mark_actions_dirty()


# ── inbox handlers ──────────────────────────────────────────────────────────


def on_worker_acknowledged(p: dict, wid: str | None) -> None:
    a = state.actions.get(p.get("action_id", ""))
    if a and a.assigned_worker_id == wid and a.status == "dispatched":
        a.status = "acknowledged"
        w = state.worker(wid)
        if w:
            w.status = "executing"
        ws.mark_actions_dirty()


def on_worker_completed(p: dict, wid: str | None) -> None:
    a = state.actions.get(p.get("action_id", ""))
    if not a:
        return
    if a.status in ("verified", "cancelled", "failed"):
        return  # already resolved
    if a.assigned_worker_id != wid:
        return  # not yours
    if any(e.kind == "worker_report" for e in a.evidence):
        return  # already counted — double tap, or a retry after a socket blip
    a.evidence.append(
        Evidence(kind="worker_report", confidence=float(p.get("confidence") or 1.0), detail="worker reported done")
    )
    a.status = "awaiting_verification"
    state.emit_soon(
        "worker_reported",
        f"{state.callsign(wid)} reported completion. Verifying against the world state.",
        severity="info",
        actor=wid or "hive",
        action_id=a.id,
    )
    ws.mark_actions_dirty()


def on_worker_blocked(p: dict, wid: str | None) -> None:
    a = state.actions.get(p.get("action_id", "")) or _current_action(wid)
    if a and a.assigned_worker_id == wid:
        a.status = "blocked"
        a.blocked_reason = p.get("reason") or "worker cannot complete"
        ws.mark_actions_dirty()


def on_worker_help(p: dict, wid: str | None) -> None:
    state.emit_soon(
        "worker_help",
        f"{state.callsign(wid)} requested assistance.",
        severity="warn",
        actor=wid or "hive",
        note=p.get("note"),
    )


def on_worker_pause(p: dict, wid: str | None) -> None:
    w = state.worker(wid)
    if w:
        w.status = "paused"
        w.available = False
        ws.mark_workers_dirty()
        state.emit_soon(f"worker_paused", f"{w.callsign} paused themselves.", severity="warn", actor=w.id)


def on_worker_resume(p: dict, wid: str | None) -> None:
    w = state.worker(wid)
    if w:
        w.status = "ready"
        w.available = True
        ws.mark_workers_dirty()
        state.emit_soon("worker_resumed", f"{w.callsign} back online.", severity="success", actor=w.id)


def on_worker_emergency(p: dict, wid: str | None) -> None:
    state.execution_status = "emergency"
    for a in state.actions.values():
        if a.status in ("assigned", *DISPATCHABLE):
            _cancel_instruction(a, "emergency stop")
            a.status = "queued"
    state.emit_soon(
        "emergency_stop",
        f"EMERGENCY STOP triggered by {state.callsign(wid)}. All activity halted.",
        severity="critical",
        actor=wid or "host",
    )
    ws.mark_actions_dirty()


def on_worker_heartbeat(p: dict, wid: str | None) -> None:
    w = state.worker(wid)
    if w:
        w.last_seen_at = now_iso()


def on_worker_ready(p: dict, wid: str | None) -> None:
    w = state.worker(wid)
    if w:
        w.status = "ready"
        w.available = True
        ws.mark_workers_dirty()


def _current_action(wid: str | None) -> Action | None:
    w = state.worker(wid)
    return state.actions.get(w.current_action_id) if w and w.current_action_id else None


HANDLERS: dict[str, Any] = {
    "worker_ready": on_worker_ready,
    "worker_acknowledged": on_worker_acknowledged,
    "worker_completed": on_worker_completed,
    "worker_blocked": on_worker_blocked,
    "worker_help": on_worker_help,
    "worker_pause": on_worker_pause,
    "worker_resume": on_worker_resume,
    "worker_emergency": on_worker_emergency,
    "worker_heartbeat": on_worker_heartbeat,
}

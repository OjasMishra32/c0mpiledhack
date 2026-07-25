"""Host commands — the operator surface, including Advanced Controls.

Every rescue control maps to a documented host message and goes through the SAME code
path as the real thing. Nothing here is bespoke UI logic, so the rescue controls cannot
silently diverge from the behaviour they are rescuing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .config import settings
from .models import Evidence, Point, now_iso
from .planner.base import compile_goal, make_goal, upgrade_plan
from .state import state
from .vision import world_model
from .websocket_manager import ws

log = logging.getLogger("hive.host")


# ── planning & execution ────────────────────────────────────────────────────


async def h_compile_goal(p: dict[str, Any]) -> None:
    scenario_id = p.get("scenario_id")
    if scenario_id and scenario_id != state.scenario.id:
        await state.reset(scenario_id)
    text = (p.get("text") or "").strip() or state.scenario.build_goal(state.scene)

    state.execution_status = "planning"
    await state.emit("plan_compiling", "Compiling objective into an execution graph…", severity="info")

    result = await compile_goal(text, state)

    if result.pending and result.grounding:
        state.pending_grounding = {"text": text, **result.grounding.ambiguous_payload()}
        state.execution_status = "idle"
        await ws.broadcast_host("grounding_ambiguous", state.pending_grounding)
        await state.emit(
            "grounding_ambiguous",
            state.pending_grounding["message"],
            severity="warn",
        )
        return

    if result.error:
        state.execution_status = "idle"
        await state.emit("plan_failed", result.error, severity="critical")
        await ws.broadcast_host("error_event", {"code": "plan_failed", "message": result.error})
        return

    # Bind resolved phrases onto objects so every instruction speaks human language.
    if result.grounding:
        for b in result.grounding.bindings:
            obj = state.scene.by_id(b.object_id or "")
            if obj and b.object_id and not obj.role:
                obj.role = b.phrase
                obj.role_confidence = b.confidence

    state.actions = {a.id: a for a in result.actions}
    for a in state.actions.values():
        a.status = "available" if not a.dependencies else "queued"
    state.goal = make_goal(text, result)
    state.execution_status = "idle"
    state.metrics.actions_total = len(state.actions)

    from .planner.validator import topo_layers

    layers = topo_layers(result.actions)
    parallel = max((len(l) for l in layers), default=0)

    await ws.broadcast_host(
        "plan_compiled",
        {
            "goal": state.goal.model_dump(),
            "actions": [a.model_dump() for a in result.actions],
            "layers": layers,
            "stats": {
                "actions": len(result.actions),
                "parallel_peak": parallel,
                "depth": len(layers),
                "source": result.source,
            },
        },
    )
    await state.emit(
        "plan_compiled",
        f"Objective compiled: {len(result.actions)} actions, {parallel} executable in parallel, "
        f"{len(layers)} stages deep.",
        severity="success",
        source=result.source,
    )
    for wmsg in result.warnings[:3]:
        await state.emit("plan_repaired", wmsg, severity="info")

    # Concurrently ask the hosted planner for a better graph. The template plan is already
    # on screen and executable; this only ever upgrades it, and only before we start.
    if result.grounding is not None:
        asyncio.create_task(_try_upgrade(text, result.grounding))


async def _try_upgrade(text: str, grounding) -> None:
    better = await upgrade_plan(text, grounding, state)
    if not better or not better.actions:
        return
    if state.execution_status != "idle" or not state.goal:
        return  # never swap the graph out from under running workers
    state.actions = {a.id: a for a in better.actions}
    for a in state.actions.values():
        a.status = "available" if not a.dependencies else "queued"
    state.goal.plan_source = better.source
    state.goal.planner_notes = better.notes
    state.metrics.actions_total = len(state.actions)

    from .planner.validator import topo_layers

    layers = topo_layers(better.actions)
    await ws.broadcast_host(
        "plan_compiled",
        {
            "goal": state.goal.model_dump(),
            "actions": [a.model_dump() for a in better.actions],
            "layers": layers,
            "stats": {
                "actions": len(better.actions),
                "parallel_peak": max((len(l) for l in layers), default=0),
                "depth": len(layers),
                "source": better.source,
            },
        },
    )
    await state.emit(
        "plan_upgraded",
        f"Plan refined by the AI planner: {len(better.actions)} actions.",
        severity="success",
        source=better.source,
    )


async def h_start(p: dict[str, Any]) -> None:
    if not state.actions:
        await state.emit("error", "Compile an objective before starting execution.", severity="warn")
        return
    state.start_run()
    if state.goal:
        state.goal.status = "executing"
    await ws.broadcast("execution_started", {})
    await state.emit(
        "execution_started",
        f"Execution started. {len(state.actions)} actions across "
        f"{sum(1 for w in state.workers.values() if w.connected)} connected workers.",
        severity="success",
    )


async def h_pause(p: dict[str, Any]) -> None:
    state.execution_status = "paused"
    await ws.broadcast("execution_paused", {})
    await state.emit("execution_paused", "All activity paused. Workers standing by.", severity="warn")


async def h_resume(p: dict[str, Any]) -> None:
    state.execution_status = "executing"
    await ws.broadcast("execution_resumed", {})
    await state.emit("execution_resumed", "Execution resumed.", severity="success")


async def h_reset(p: dict[str, Any]) -> None:
    await state.reset(p.get("scenario_id"))


async def h_emergency(p: dict[str, Any]) -> None:
    from .orchestrator import on_worker_emergency

    on_worker_emergency({}, None)
    await ws.broadcast("emergency_stop", {"actor": "host"})


# ── scene ───────────────────────────────────────────────────────────────────


async def h_scan_scene(p: dict[str, Any]) -> None:
    from .vision.camera import camera
    from .vision.scene_discovery import discovery, rebuild_scene

    if state.world.mode != "simulation" and camera.online:
        frame = camera.latest()
        if frame is not None:
            rebuild_scene(state.scene, discovery.discover(frame))
    else:
        from .demo.simulator import simulator

        simulator.spawn_scene(state.scene)

    world_model.refresh(state)
    n = len(state.scene.objects)
    await ws.broadcast_host("scene_discovered", state.scene.model_dump())
    await state.emit(
        "scene_discovered",
        f"Scene scanned: {n} item{'s' if n != 1 else ''} detected across "
        f"{len(state.scene.zones)} regions.",
        severity="success",
        count=n,
    )

    if p.get("relabel", True):
        await _relabel_scene()


async def _relabel_scene() -> None:
    """Semantic labelling — one burst, on scan only, never on a timer."""
    from .perception.analyzer import analyzer
    from .vision.camera import camera

    if not analyzer.enabled or not camera.online:
        return
    frames = camera.burst(count=2, seconds=1.0)
    if not frames:
        return
    a = await analyzer.describe(frames)
    if not a.ok:
        return
    reported = a.raw.get("objects") or []
    matched = 0
    for r in reported:
        pos = r.get("position") or {}
        try:
            p = Point(x=float(pos.get("x", 0.5)), y=float(pos.get("y", 0.5)))
        except Exception:
            continue
        best = min(state.scene.objects, key=lambda o: o.position.dist(p), default=None)
        if best and best.position.dist(p) < 0.18:
            best.semantic_label = str(r.get("ref") or "").strip() or best.semantic_label
            if r.get("held"):
                best.held_by = best.held_by or "unknown"
            if r.get("on_top_of"):
                best.stacked_on = best.stacked_on
            matched += 1
    if matched:
        state.scene.labeling_source = "vlm"
        ws.mark_world_dirty()
        await state.emit(
            "scene_labeled",
            f"Scene model identified {matched} item{'s' if matched != 1 else ''}. "
            f"{a.raw.get('activity') or ''}".strip(),
            severity="info",
            model=a.model,
        )


async def h_bind_object(p: dict[str, Any]) -> None:
    obj = state.scene.by_id(p.get("object_id", ""))
    if not obj:
        return
    obj.role = p.get("role") or obj.role
    obj.role_confidence = 1.0
    ws.mark_world_dirty()
    await state.emit(
        "grounding_resolved",
        f"Operator bound “{obj.role}” to {obj.display_label()}.",
        severity="info",
        object_id=obj.id,
    )
    pending = state.pending_grounding
    state.pending_grounding = None
    if pending and pending.get("text"):
        await h_compile_goal({"text": pending["text"]})


async def h_ask_feed(p: dict[str, Any]) -> None:
    """Operator Q&A over the live camera. The clearest proof this is real perception."""
    from .perception.analyzer import analyzer
    from .vision.camera import camera

    question = (p.get("question") or "").strip()
    if not question:
        return
    frames = camera.burst(count=settings.vlm_burst_frames, seconds=2.5)
    if not analyzer.enabled or not frames:
        await ws.broadcast_host(
            "feed_answer",
            {"question": question, "answer": "Scene reasoning is unavailable — running on computer vision alone.", "ok": False},
        )
        return
    summary = ", ".join(f"{o.display_label()} in {state.zone_label(o.zone)}" for o in state.scene.objects)
    a = await analyzer.ask(question, frames, summary)
    await ws.broadcast_host(
        "feed_answer",
        {"question": question, "answer": a.what_happened or "Unable to determine.", "ok": a.ok, "model": a.model},
    )
    if a.ok:
        await state.emit("feed_answer", f"“{question}” — {a.what_happened}", severity="info")


# ── world manipulation & failure injection ──────────────────────────────────


async def h_update_object(p: dict[str, Any]) -> None:
    oid = p.get("object_id", "")
    if p.get("zone"):
        world_model.set_object_zone(state, oid, p["zone"])
    elif p.get("position"):
        pos = p["position"]
        world_model.set_object_position(state, oid, Point(x=float(pos["x"]), y=float(pos["y"])))
    ws.mark_world_dirty()
    obj = state.scene.by_id(oid)
    if obj:
        await state.emit(
            "world_updated",
            f"Host-assisted observation received: {obj.display_label()} in {state.zone_label(obj.zone)}.",
            severity="info",
            object_id=oid,
        )


async def h_set_mode(p: dict[str, Any]) -> None:
    mode = p.get("mode")
    if mode not in ("live", "assisted", "simulation"):
        return
    state.world.mode = mode  # type: ignore[assignment]
    if mode in ("live", "assisted"):
        from .vision.camera import camera

        ok = camera.open()
        state.world.camera_online = ok
        if not ok:
            state.world.mode = "simulation"
            await state.emit(
                "vision_unavailable",
                "Camera unavailable — continuing in simulation.",
                severity="warn",
            )
            return
    ws.mark_world_dirty()
    await state.emit("mode_changed", f"World model source: {mode}.", severity="info", mode=mode)


async def h_set_worker(p: dict[str, Any]) -> None:
    w = state.worker(p.get("worker_id"))
    if not w:
        return
    if "available" in p:
        w.available = bool(p["available"])
        w.status = "ready" if w.available and w.connected else ("unavailable" if not w.available else w.status)
    if p.get("status"):
        w.status = p["status"]
    ws.mark_workers_dirty()
    await state.emit(
        "worker_state_changed",
        f"{w.callsign} marked {'available' if w.available else 'unavailable'}.",
        severity="warn" if not w.available else "success",
        worker_id=w.id,
    )


async def h_manual_verify(p: dict[str, Any]) -> None:
    a = state.actions.get(p.get("action_id", ""))
    if not a:
        return
    if p.get("verified", True):
        a.evidence.append(Evidence(kind="host_override", confidence=1.0, detail="operator confirmed"))
        a.status = "awaiting_verification"
    else:
        a.status = "blocked"
        a.blocked_reason = "operator marked failed"
    ws.mark_actions_dirty()
    await state.emit(
        "manual_verification",
        f"Operator {'confirmed' if p.get('verified', True) else 'rejected'} action {a.id}.",
        severity="info",
        action_id=a.id,
    )


async def h_skip_action(p: dict[str, Any]) -> None:
    a = state.actions.get(p.get("action_id", ""))
    if not a:
        return
    a.status = "cancelled"
    state.free_locks_for(a.id)
    ws.mark_actions_dirty()
    await state.emit("action_skipped", f"Action {a.id} skipped by operator.", severity="warn")


async def h_reassign(p: dict[str, Any]) -> None:
    a = state.actions.get(p.get("action_id", ""))
    if not a:
        return
    a.assigned_worker_id = p.get("worker_id")
    a.status = "assigned" if a.assigned_worker_id else "available"
    a.instruction = None
    ws.mark_actions_dirty()
    await state.emit("action_reassigned", f"Operator reassigned action {a.id}.", severity="warn")


async def h_inject_failure(p: dict[str, Any]) -> None:
    kind = p.get("kind")
    target = p.get("target_id")

    if kind == "worker_down":
        wid = target or next(
            (w.id for w in state.workers.values() if w.current_action_id), "worker_c"
        )
        await h_set_worker({"worker_id": wid, "available": False})
        return

    if kind == "wrong_object_move":
        obj = state.scene.by_id(target or "") or _busiest_object()
        if not obj:
            return
        wrong = next((z.id for z in state.scene.zones if z.id != obj.zone), "field")
        world_model.set_object_zone(state, obj.id, wrong)
        ws.mark_world_dirty()
        return

    if kind == "object_removed":
        obj = state.scene.by_id(target or "") or _busiest_object()
        if obj:
            obj.visible = False
            obj.confidence = 0.1
            ws.mark_world_dirty()
        return

    if kind == "worker_timeout":
        a = next((x for x in state.actions.values() if x.status in ("dispatched", "executing")), None)
        if a:
            a.timeout_seconds = 0
        return

    if kind == "verification_regress":
        done = [a for a in state.actions.values() if a.status == "verified" and a.object_id]
        if done:
            a = done[-1]
            wrong = next((z.id for z in state.scene.zones if z.id != a.target_zone), "field")
            world_model.set_object_zone(state, a.object_id or "", wrong)
            ws.mark_world_dirty()
        return

    if kind == "zone_blocked":
        z = state.scene.zone_by_id(target or "") or (state.scene.zones[0] if state.scene.zones else None)
        if z:
            z.status = "blocked"
            ws.mark_world_dirty()
            await state.emit("zone_blocked", f"{z.label} reported obstructed.", severity="critical")
        return


def _busiest_object():
    live = [a for a in state.actions.values() if a.status in ("dispatched", "executing", "verified")]
    for a in reversed(live):
        if a.object_id:
            return state.scene.by_id(a.object_id)
    return state.scene.objects[0] if state.scene.objects else None


async def h_set_perception(p: dict[str, Any]) -> None:
    if "vlm_enabled" in p:
        settings.vlm_enabled = bool(p["vlm_enabled"])
    ws.mark_world_dirty()


async def h_arm_escalation(p: dict[str, Any]) -> None:
    state.escalation_armed = bool(p.get("armed", True))
    await state.emit(
        "escalation_armed",
        f"Voice escalation {'armed' if state.escalation_armed else 'disarmed'}.",
        severity="warn" if state.escalation_armed else "info",
    )


async def h_escalate_call(p: dict[str, Any]) -> None:
    from .integrations.voygr import escalate

    await escalate(p.get("reason") or "Operator-initiated escalation.", state, manual=True)


HOST_HANDLERS = {
    "host_compile_goal": h_compile_goal,
    "host_start_execution": h_start,
    "host_pause_all": h_pause,
    "host_resume_all": h_resume,
    "host_reset": h_reset,
    "host_emergency_stop": h_emergency,
    "host_scan_scene": h_scan_scene,
    "host_bind_object": h_bind_object,
    "host_ask_feed": h_ask_feed,
    "host_update_object": h_update_object,
    "host_set_mode": h_set_mode,
    "host_set_worker": h_set_worker,
    "host_manual_verify": h_manual_verify,
    "host_skip_action": h_skip_action,
    "host_reassign": h_reassign,
    "host_inject_failure": h_inject_failure,
    "host_set_perception": h_set_perception,
    "host_arm_escalation": h_arm_escalation,
    "host_escalate_call": h_escalate_call,
}

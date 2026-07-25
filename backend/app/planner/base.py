"""Planner interfaces and the fallback chain.

`compile_goal()` is the only entry point the orchestrator calls. It owns grounding, the
LLM attempt, validation, and the silent drop to templates. Everything here has a timeout and
a fallback, because a planner that hangs is a demo that dies on stage.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from ..config import settings
from ..models import Action, ActionStatus, Predicate, PredicateType, Scene, Worker, SUPPORTED_ACTIONS
from . import validator
from .grounding import GroundingResult, resolve_all

log = logging.getLogger(__name__)

Emit = Callable[[str, dict], Any | Awaitable[Any]]


class NotReady(Exception):
    """The scene has not settled. Rescan before compiling."""


@dataclass
class PlanContext:
    workers: list[Worker] = field(default_factory=list)
    scene: Scene = field(default_factory=Scene)  # LIVE discovered objects. Never a manifest.
    bindings: GroundingResult = field(default_factory=GroundingResult)
    supported_actions: list[str] = field(default_factory=lambda: list(SUPPORTED_ACTIONS))
    constraints: list[str] = field(default_factory=list)
    scenario_id: str | None = None  # only supplies zone labels / lexicon / parachute graph
    world_summary: str = ""  # current state, so a *replan* prompt works from where reality is


@dataclass
class PlanResult:
    actions: list[Action] = field(default_factory=list)
    success_predicates: list[Predicate] = field(default_factory=list)
    source: str = "template"  # llm | template | demo_script | manual
    normalized_intent: str = ""
    notes: str = ""  # "11 actions · 4 parallel · 2 resource conflicts"
    warnings: list[str] = field(default_factory=list)
    grounding: GroundingResult | None = None
    template: str | None = None
    is_pending: bool = False  # waiting on the host to disambiguate a binding

    @classmethod
    def pending(cls, grounding: GroundingResult) -> "PlanResult":
        phrases = ", ".join(f"“{a.phrase}”" for a in grounding.ambiguous)
        return cls(
            source="template",
            normalized_intent="awaiting_grounding",
            notes=f"Waiting on the operator to identify {phrases}.",
            grounding=grounding,
            is_pending=True,
        )

    @property
    def stats(self) -> dict:
        return validator.plan_stats(self.actions)

    def layers(self) -> list[list[str]]:
        return validator.topo_layers(self.actions)

    def mark_ready(self) -> None:
        """Actions with no dependencies can be scheduled immediately."""
        for a in self.actions:
            if not a.dependencies and a.status == ActionStatus.queued.value:
                a.status = ActionStatus.available.value


class Planner(Protocol):
    async def compile(self, goal_text: str, ctx: PlanContext) -> PlanResult: ...


async def _emit(emit: Emit | None, kind: str, payload: dict) -> None:
    if emit is None:
        return
    try:
        result = emit(kind, payload)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:  # never let telemetry break a compile
        log.warning("emit(%s) failed: %s", kind, exc)


async def compile_goal(
    goal_text: str,
    scene: Scene,
    workers: list[Worker],
    *,
    scenario_id: str | None = None,
    hints: list[str] | None = None,
    constraints: list[str] | None = None,
    emit: Emit | None = None,
    use_llm: bool = True,
) -> PlanResult:
    """Goal sentence → validated task graph over real observed object ids."""
    if not scene.stable:
        raise NotReady("Scene still settling — rescan before compiling.")

    grounding = resolve_all(goal_text, scene, hints=hints)

    if use_llm and settings.nvidia_api_key and (grounding.ambiguous or grounding.unresolved_phrases):
        # The LLM improves the answer; it never gates it.
        try:
            from .llm_planner import llm_refine_grounding

            await asyncio.wait_for(
                llm_refine_grounding(goal_text, scene, grounding),
                timeout=settings.grounding_timeout_seconds,
            )
        except Exception as exc:
            log.warning("LLM grounding unavailable: %s", exc)

    await _emit(
        emit,
        "grounding_resolved",
        {
            "bindings": [
                {
                    "phrase": b.phrase,
                    "object_id": b.object_id,
                    "object_ids": b.object_ids,
                    "confidence": b.confidence,
                    "alternatives": b.alternatives,
                    "basis": b.basis,
                }
                for b in grounding.bindings
            ],
            "zones": [{"phrase": z.phrase, "zone_id": z.zone_id} for z in grounding.zone_bindings],
            "unbound_places": grounding.unbound_places,
            "unresolved": grounding.unresolved_phrases,
        },
    )

    if grounding.ambiguous:
        await _emit(emit, "grounding_ambiguous", grounding.ambiguous_payload())
        return PlanResult.pending(grounding)

    ctx = PlanContext(
        workers=list(workers),
        scene=scene,
        bindings=grounding,
        constraints=list(constraints or []),
        scenario_id=scenario_id,
        world_summary=describe_world(scene, workers),
    )
    return await compile_with_context(goal_text, ctx, use_llm=use_llm)


async def compile_with_context(goal_text: str, ctx: PlanContext, *, use_llm: bool = True) -> PlanResult:
    """The fallback chain: LLM → validate → template → validate → survey."""
    from .template_planner import TemplatePlanner

    if use_llm and settings.nvidia_api_key:
        try:
            from .llm_planner import LLMPlanner

            result = await asyncio.wait_for(
                LLMPlanner().compile(goal_text, ctx), timeout=settings.planner_timeout_seconds
            )
            report = validator.validate_and_repair(result, ctx)
            if report.ok:
                result.warnings = list(result.warnings) + report.repairs
                result.notes = validator.notes_for(result.actions)
                result.mark_ready()
                return result
            log.warning("LLM plan rejected: %s", report.errors)
        except asyncio.TimeoutError:
            log.warning("LLM planner timed out after %.0fs", settings.planner_timeout_seconds)
        except Exception as exc:
            log.warning("LLM planner unavailable: %s", exc)

    result = TemplatePlanner().compile_sync(goal_text, ctx)
    report = validator.validate_and_repair(result, ctx)  # templates get validated too
    if not report.ok:
        log.error("Template plan rejected: %s", report.errors)
        result = _survey_fallback(goal_text, ctx, report.errors)
        validator.validate_and_repair(result, ctx)
    result.warnings = list(result.warnings) + report.repairs
    result.notes = validator.notes_for(result.actions)
    result.mark_ready()
    return result


def _survey_fallback(goal_text: str, ctx: PlanContext, errors: list[str]) -> PlanResult:
    """Absolute floor: confirm what is present. Never returns an empty plan."""
    from .template_planner import _Builder, survey

    b = _Builder(ctx)
    survey(b)
    return PlanResult(
        actions=b.actions,
        success_predicates=b.success_predicates()
        or [Predicate(type=PredicateType.sequence_completed.value, subject="objective")],
        source="template",
        normalized_intent="survey",
        notes=validator.notes_for(b.actions),
        warnings=list(b.warnings) + errors,
        grounding=ctx.bindings,
        template="survey",
    )


def describe_world(scene: Scene, workers: list[Worker]) -> str:
    """Compact current-state table. This is what lets a replan prompt start from reality."""
    lines = ["OBJECTS (id · label · current area · held by):"]
    for o in scene.objects:
        lines.append(
            f"  {o.id} · {o.display_label()} · {scene.zone_label(o.zone)} · {o.held_by or 'nobody'}"
        )
    lines.append("AREAS (id · label · currently contains):")
    for z in scene.zones:
        lines.append(f"  {z.id} · {z.label} · {', '.join(z.occupancy) or 'empty'}")
    lines.append("WORKERS (id · callsign · status · can reach):")
    for w in workers:
        lines.append(f"  {w.id} · {w.callsign} · {w.status} · {', '.join(w.reachable_zones)}")
    return "\n".join(lines)


# ── recovery replanning (called by recovery.py) ────────────────────────────────


def unsatisfied_deliveries(scene: Scene, success_predicates: list[Predicate]) -> list[tuple[str, str]]:
    """Which parts of the objective are still not true in the world right now."""
    out: list[tuple[str, str]] = []
    for p in success_predicates:
        if p.type != PredicateType.object_in_zone.value or not p.object:
            continue
        obj = scene.by_id(p.subject)
        if obj is not None and obj.zone != p.object and (p.subject, p.object) not in out:
            out.append((p.subject, p.object))
    return out


async def replan_from_state(state: Any, deviation: dict | None = None, *, use_llm: bool = True) -> PlanResult:
    """Recompile the REMAINING objective from the world as it actually is now.

    Deterministic by default. If the LLM is available it gets 8 seconds to do better; on any
    failure the deterministic plan ships. The REPLANNING animation covers the latency either way.
    """
    scene: Scene = state.scene
    workers: list[Worker] = list(state.workers)
    goal = getattr(state, "goal", None)
    goal_text = getattr(goal, "raw_text", "") or ""
    success = list(getattr(goal, "success_predicates", []) or [])

    remaining = unsatisfied_deliveries(scene, success)
    focus = (deviation or {}).get("object_id")
    urgency = {oid: 105 if oid == focus else 90 for oid, _ in remaining}

    bindings = GroundingResult(
        goal_text=goal_text,
        deliveries=remaining,
        urgency=urgency,
        roles={o.id: o.role for o in scene.objects if o.role},
    )
    ctx = PlanContext(
        workers=workers,
        scene=scene,
        bindings=bindings,
        scenario_id=getattr(state, "scenario_id", None),
        constraints=[f"Already verified: {(deviation or {}).get('verified_summary', 'see world state')}"],
        world_summary=describe_world(scene, workers),
    )

    if use_llm and settings.nvidia_api_key:
        try:
            from .llm_planner import LLMPlanner

            result = await asyncio.wait_for(
                LLMPlanner().replan(goal_text, ctx, deviation or {}),
                timeout=settings.replan_timeout_seconds,
            )
            report = validator.validate_and_repair(result, ctx)
            if report.ok:
                _mark_recovery(result)
                result.notes = validator.notes_for(result.actions)
                result.mark_ready()
                return result
            log.warning("LLM replan rejected: %s", report.errors)
        except Exception as exc:
            log.warning("LLM replan unavailable: %s", exc)

    result = await compile_with_context(goal_text or "resume the remaining objective", ctx, use_llm=False)
    _mark_recovery(result)
    return result


def _mark_recovery(result: PlanResult) -> None:
    for a in result.actions:
        a.is_recovery = True

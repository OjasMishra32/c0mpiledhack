"""Planner entry point. Owns grounding + the fallback chain.

OWNER: Zechariah.

compile_goal() is the ONLY function the orchestrator calls. It never raises: an LLM
failure, a validation rejection, or a network outage all land on the template path.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from ..config import settings
from ..models import Action, Goal, PlanSource, Predicate, now_iso
from .grounding import GroundingResult, resolve_all
from .template_planner import compile_template
from .validator import ValidationReport, validate_and_repair

log = logging.getLogger("hive.planner")


class _PlannerProbe:
    """Measures once whether the hosted planner is fast enough to sit in the compile path."""

    def __init__(self) -> None:
        self.usable = False
        self.latency: float | None = None
        self.reason = "not probed"

    async def run(self) -> None:
        if not settings.has_model_access:
            self.reason = "no model keys configured"
            return
        import time

        from ..perception import nim_client

        t0 = time.monotonic()
        resp = await nim_client.chat(
            settings.planner_model,
            [{"role": "user", "content": 'Return only this JSON: {"ok":true}'}],
            tier="planner-probe", timeout=6.0, max_tokens=20, max_attempts=1,
        )
        self.latency = round(time.monotonic() - t0, 2)
        if resp and self.latency <= 6.0:
            self.usable = True
            self.reason = f"planner responds in {self.latency}s"
        else:
            self.reason = (
                f"planner too slow ({self.latency}s) — compiling from the template library"
                if resp else "planner unreachable — compiling from the template library"
            )
        log.info("planner probe: %s", self.reason)


planner_probe = _PlannerProbe()


@dataclass
class PlanResult:
    actions: list[Action] = field(default_factory=list)
    success_predicates: list[Predicate] = field(default_factory=list)
    source: PlanSource = "template"
    normalized_intent: str = ""
    notes: str = ""
    warnings: list[str] = field(default_factory=list)
    grounding: GroundingResult | None = None
    pending: bool = False
    error: str | None = None


async def compile_goal(goal_text: str, state: Any) -> PlanResult:
    if not state.scene.objects:
        return PlanResult(error="No items detected in the workspace. Scan the scene first.")
    if not state.scene.stable:
        return PlanResult(error="Scene still settling — rescan before compiling.")

    grounding = resolve_all(goal_text, state.scene)

    if grounding.ambiguous:
        return PlanResult(grounding=grounding, pending=True)

    # ── template path: instant, deterministic, always produces a plan ───────
    # The compile step is the one moment an operator watches a spinner, so we never block
    # it on a hosted model. The LLM runs CONCURRENTLY (see upgrade_plan below) and swaps
    # its plan in only if it wins before execution starts.
    actions, success, template, notes = compile_template(goal_text, grounding, state)
    actions, report = validate_and_repair(actions, state)
    if not report.ok:
        return PlanResult(error=report.errors[0], grounding=grounding)
    return PlanResult(
        actions=actions,
        success_predicates=success,
        source="template",
        normalized_intent=template,
        notes=notes,
        warnings=report.repairs,
        grounding=grounding,
    )


async def upgrade_plan(goal_text: str, grounding: GroundingResult, state: Any) -> PlanResult | None:
    """Try the hosted planner in the background. Returns a better plan, or None.

    Never blocks a compile. If it comes back after execution starts, the caller discards it —
    swapping the graph out from under running workers would be worse than a simpler plan.
    """
    if not settings.has_model_access:
        return None
    try:
        from .llm_planner import compile_llm

        result = await asyncio.wait_for(
            compile_llm(goal_text, grounding, state), timeout=settings.planner_timeout
        )
        if not result or not result.actions:
            return None
        actions, report = validate_and_repair(result.actions, state)
        if not report.ok:
            log.warning("LLM plan rejected: %s", report.errors)
            return None
        result.actions = actions
        result.warnings = report.repairs
        result.grounding = grounding
        return result
    except asyncio.TimeoutError:
        log.info("planner model did not return in time — keeping the template plan")
    except Exception as e:
        log.info("planner model unavailable (%s) — keeping the template plan", type(e).__name__)
    return None


def make_goal(text: str, result: PlanResult) -> Goal:
    return Goal(
        raw_text=text,
        normalized_intent=result.normalized_intent,
        status="compiled",
        success_predicates=result.success_predicates,
        plan_source=result.source,
        planner_notes=result.notes,
        warnings=result.warnings,
        created_at=now_iso(),
    )


__all__ = ["PlanResult", "compile_goal", "make_goal", "ValidationReport"]

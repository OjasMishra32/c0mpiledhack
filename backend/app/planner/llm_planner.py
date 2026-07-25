"""The LLM planner — an upgrade over the template planner, never a dependency.

NVIDIA NIM exposes an OpenAI-compatible API, so this is the `openai` async client pointed at
the NIM base URL with our single `NVIDIA_API_KEY`.

We force a **tool call** instead of begging for raw JSON, which removes most of the failure
modes people hit here. If a model ignores `tool_choice` we degrade without fighting it:

    forced tool call → response_format=json_object → strict-JSON prompt + _extract_json()

Any exception at all lands the caller on the template planner. That is the contract.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from ..config import settings
from ..models import Action, ActionStatus, Predicate, Scene, SUPPORTED_ACTIONS
from .grounding import Binding, GroundingResult
from .prompts import (
    GROUNDING_SYSTEM_PROMPT,
    REPLAN_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    render_context,
    render_grounding,
    render_replan,
)

if TYPE_CHECKING:
    from .base import PlanContext, PlanResult

log = logging.getLogger(__name__)

_PREDICATE_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string"},
        "subject": {"type": "string"},
        "object": {"type": ["string", "null"]},
    },
    "required": ["type", "subject"],
}

PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "emit_plan",
        "description": "Emit the compiled operational task graph.",
        "parameters": {
            "type": "object",
            "properties": {
                "normalized_intent": {"type": "string"},
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "type": {"type": "string", "enum": SUPPORTED_ACTIONS},
                            "description": {"type": "string"},
                            "object_id": {"type": ["string", "null"]},
                            "target_zone": {"type": ["string", "null"]},
                            "target_object_id": {"type": ["string", "null"]},
                            "dependencies": {"type": "array", "items": {"type": "string"}},
                            "priority": {"type": "integer"},
                            "expected_predicates": {"type": "array", "items": _PREDICATE_SCHEMA},
                        },
                        "required": ["id", "type", "description", "object_id", "dependencies", "priority"],
                    },
                },
                "success_predicates": {"type": "array", "items": _PREDICATE_SCHEMA},
                "notes": {"type": "string"},
            },
            "required": ["normalized_intent", "actions", "success_predicates"],
        },
    },
}

BINDINGS_TOOL = {
    "type": "function",
    "function": {
        "name": "emit_bindings",
        "description": "Bind noun phrases from the objective to observed object ids.",
        "parameters": {
            "type": "object",
            "properties": {
                "bindings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "phrase": {"type": "string"},
                            "object_id": {"type": ["string", "null"]},
                            "object_ids": {"type": "array", "items": {"type": "string"}},
                            "why": {"type": "string"},
                        },
                        "required": ["phrase"],
                    },
                }
            },
            "required": ["bindings"],
        },
    },
}


def _client():
    from openai import AsyncOpenAI  # imported lazily so a keyless run never touches it

    # Draw from the shared pool so this and perception never double-spend one key's budget.
    try:
        from ..key_pool import get_pool

        pool = get_pool(settings.nim_keys, settings.nim_per_key_rpm, settings.nim_key_strategy)
        if pool and pool.count:
            import random

            key = pool.keys[random.randrange(pool.count)]
            return AsyncOpenAI(base_url=settings.nim_base_url, api_key=key)
    except Exception:
        pass

    return AsyncOpenAI(base_url=settings.nim_base_url, api_key=settings.nvidia_api_key)


def _extract_json(text: str) -> dict:
    """Strip fences, take the outermost object, parse. Written early because you will need it."""
    if not text:
        raise ValueError("empty completion")
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in completion")
    return json.loads(cleaned[start : end + 1])


def _coerce_predicates(raw: Any) -> list[Predicate]:
    out: list[Predicate] = []
    for item in raw or []:
        if not isinstance(item, dict) or not item.get("type") or not item.get("subject"):
            continue
        out.append(
            Predicate(
                type=str(item["type"]),
                subject=str(item["subject"]),
                object=(str(item["object"]) if item.get("object") not in (None, "") else None),
                tolerance=item.get("tolerance"),
            )
        )
    return out


def to_plan_result(payload: dict, ctx: "PlanContext", source: str = "llm") -> "PlanResult":
    """Model output → typed actions. Tolerant on the way in; the validator is the gate."""
    from .base import PlanResult

    actions: list[Action] = []
    for i, raw in enumerate(payload.get("actions") or [], start=1):
        if not isinstance(raw, dict) or not raw.get("type"):
            continue
        deps = [str(d) for d in (raw.get("dependencies") or []) if isinstance(d, (str, int))]
        actions.append(
            Action(
                id=str(raw.get("id") or f"a{i}"),
                type=str(raw["type"]),
                description=str(raw.get("description") or "").strip() or "Perform the next step.",
                object_id=(str(raw["object_id"]) if raw.get("object_id") else None),
                target_object_id=(str(raw["target_object_id"]) if raw.get("target_object_id") else None),
                target_zone=(str(raw["target_zone"]) if raw.get("target_zone") else None),
                dependencies=deps,
                priority=int(raw.get("priority") or 70),
                expected_predicates=_coerce_predicates(raw.get("expected_predicates")),
                status=ActionStatus.queued.value,
            )
        )
    return PlanResult(
        actions=actions,
        success_predicates=_coerce_predicates(payload.get("success_predicates")),
        source=source,
        normalized_intent=str(payload.get("normalized_intent") or ""),
        notes=str(payload.get("notes") or ""),
        grounding=ctx.bindings,
    )


class LLMPlanner:
    """Every method here either returns a PlanResult or raises. Raising is a normal outcome."""

    async def _call(self, system: str, user: str, tool: dict, tool_name: str, max_tokens: int = 3000) -> dict:
        client = _client()
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

        # 1 — forced tool call: you cannot get prose back
        try:
            resp = await client.chat.completions.create(
                model=settings.planner_model,
                max_tokens=max_tokens,
                temperature=0.2,
                messages=messages,
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": tool_name}},
            )
            calls = resp.choices[0].message.tool_calls
            if calls:
                return json.loads(calls[0].function.arguments)
            log.warning("%s ignored tool_choice; degrading to json_object", settings.planner_model)
        except Exception as exc:
            if _is_tool_support_error(exc):
                log.warning("tool calling unsupported (%s); degrading to json_object", exc)
            else:
                raise

        # 2 — structured JSON mode
        try:
            resp = await client.chat.completions.create(
                model=settings.planner_model,
                max_tokens=max_tokens,
                temperature=0.2,
                messages=messages,
                response_format={"type": "json_object"},
            )
            return _extract_json(resp.choices[0].message.content or "")
        except Exception as exc:
            log.warning("json_object mode failed (%s); degrading to strict-JSON prompt", exc)

        # 3 — strict-JSON prompt, parsed defensively
        schema = json.dumps(tool["function"]["parameters"], separators=(",", ":"))
        resp = await client.chat.completions.create(
            model=settings.planner_model,
            max_tokens=max_tokens,
            temperature=0.2,
            messages=messages
            + [
                {
                    "role": "system",
                    "content": f"Reply with ONE JSON object matching this schema and nothing else: {schema}",
                }
            ],
        )
        return _extract_json(resp.choices[0].message.content or "")

    async def compile(self, goal_text: str, ctx: "PlanContext") -> "PlanResult":
        payload = await self._call(SYSTEM_PROMPT, render_context(goal_text, ctx), PLAN_TOOL, "emit_plan")
        result = to_plan_result(payload, ctx)
        if not result.actions:
            raise ValueError("model returned an empty plan")
        return result

    async def replan(self, goal_text: str, ctx: "PlanContext", deviation: dict) -> "PlanResult":
        payload = await self._call(
            REPLAN_SYSTEM_PROMPT, render_replan(goal_text, ctx, deviation), PLAN_TOOL, "emit_plan", max_tokens=2000
        )
        result = to_plan_result(payload, ctx)
        if not result.actions:
            raise ValueError("model returned an empty replan")
        return result


def _is_tool_support_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(s in text for s in ("tool", "function", "unsupported", "not supported", "400"))


async def llm_refine_grounding(goal_text: str, scene: Scene, grounding: GroundingResult) -> None:
    """Resolve leftovers the descriptor scorer could not: pronouns, relational language.

    Mutates `grounding` in place and only ever *fills gaps* — it never overrides a phrase the
    deterministic scorer already bound confidently.
    """
    payload = await LLMPlanner()._call(
        GROUNDING_SYSTEM_PROMPT, render_grounding(goal_text, scene), BINDINGS_TOOL, "emit_bindings", max_tokens=800
    )
    known = {o.id for o in scene.objects}
    pending = {a.phrase for a in grounding.ambiguous} | set(grounding.unresolved_phrases)
    resolved: list[str] = []

    for item in payload.get("bindings") or []:
        phrase = str(item.get("phrase") or "").strip()
        if not phrase or phrase not in pending:
            continue
        ids = [str(i) for i in (item.get("object_ids") or []) if str(i) in known]
        if not ids and item.get("object_id") and str(item["object_id"]) in known:
            ids = [str(item["object_id"])]
        if not ids:
            continue
        existing = next((b for b in grounding.bindings if b.phrase == phrase), None)
        if existing is not None:
            existing.object_ids = ids
            existing.confidence = 0.8
            existing.basis = f"resolved by the planner model — {item.get('why') or 'relational reference'}"
        else:
            grounding.bindings.append(
                Binding(
                    phrase=phrase,
                    object_ids=ids,
                    confidence=0.8,
                    basis=f"resolved by the planner model — {item.get('why') or 'relational reference'}",
                )
            )
        resolved.append(phrase)

    if not resolved:
        return
    grounding.ambiguous = [a for a in grounding.ambiguous if a.phrase not in resolved]
    grounding.unresolved_phrases = [p for p in grounding.unresolved_phrases if p not in resolved]
    grounding.bindings.sort(key=lambda b: b.span[0])
    from .grounding import _pair_deliveries  # recompute pairings with the new bindings

    grounding.deliveries = _pair_deliveries(grounding.bindings, grounding.zone_bindings, goal_text)
    grounding.apply_roles(scene)

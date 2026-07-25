"""LLM planner — NVIDIA NIM, OpenAI-compatible tool calling.

OWNER: Zechariah.

Forces a tool call so prose can't come back. Degrades: tool_choice → json_object →
strict-JSON extraction → (caller falls back to the template library).
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import settings
from ..models import SUPPORTED_ACTIONS, Action, Predicate
from ..perception import nim_client
from .grounding import GroundingResult

log = logging.getLogger("hive.planner.llm")

SYSTEM_PROMPT = """You are HIVE's operations compiler. You convert a high-level objective for a
physical operation into a minimal, validated task graph executed by individual humans who each
receive ONLY their own next instruction.

CRITICAL CONSTRAINT: workers cannot see the objective, the plan, each other's instructions, or the
state of the operation. Every action description must be fully self-contained and physically
unambiguous to someone who knows nothing else. Never write an instruction that depends on shared
knowledge, on another worker's action, or on the word "then".

RULES
- Use ONLY the object ids, zone ids, and action types provided. Invent nothing.
- One atomic physical movement per action. No compound actions.
- Express ordering ONLY through the dependencies array, never through wording.
- Maximize parallelism: actions touching different objects and different zones must NOT depend
  on each other.
- Never let two concurrent actions manipulate the same object or target the same zone.
- When one object is placed on another, add a hold action on the base object before it and a
  release action after it.
- Do not assign workers. Leave assigned_worker_id unset; a scheduler assigns by capability.
- Priority: life-safety or explicitly expedited work 100, work that blocks other work 85,
  routine 65, background 50.
- Include expected_predicates for every action and success_predicates for the objective.
- End with one final inspect action depending on all location verifications.
- At most 16 actions. Aim for 9-13.

Call emit_plan exactly once. Produce no other output."""

PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "emit_plan",
        "description": "Emit the compiled operational task graph.",
        "parameters": {
            "type": "object",
            "properties": {
                "normalized_intent": {"type": "string"},
                "notes": {"type": "string"},
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
                            "expected_predicates": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "type": {"type": "string"},
                                        "subject": {"type": "string"},
                                        "object": {"type": ["string", "null"]},
                                    },
                                    "required": ["type", "subject"],
                                },
                            },
                        },
                        "required": ["id", "type", "description", "dependencies", "priority"],
                    },
                },
                "success_predicates": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["normalized_intent", "actions", "success_predicates"],
        },
    },
}


def render_context(goal_text: str, g: GroundingResult, state: Any) -> str:
    objs = "\n".join(
        f"  - {o.id}: {o.display_label()} (colour {o.descriptor.color_name}, "
        f"shape {o.descriptor.shape_hint}, currently in {state.zone_label(o.zone)})"
        for o in state.scene.objects
    )
    zones = "\n".join(f"  - {z.id}: {z.label}" for z in state.scene.zones)
    workers = "\n".join(
        f"  - {w.id} ({w.callsign}, {w.role}): reaches {', '.join(w.reachable_zones)}"
        for w in state.workers.values()
    )
    binds = "\n".join(
        f"  - “{b.phrase}” → {b.object_id} ({b.basis}, {int(b.confidence * 100)}%)"
        for b in (g.bindings if g else [])
    )
    return f"""OBJECTIVE
{goal_text}

OBSERVED OBJECTS (these are the only objects that exist)
{objs or "  (none)"}

LOCATIONS
{zones or "  (none)"}

WORKERS
{workers}

PHRASE BINDINGS ALREADY RESOLVED FROM THE LIVE CAMERA
{binds or "  (none)"}

SUPPORTED ACTION TYPES
  {", ".join(SUPPORTED_ACTIONS)}

SUPPORTED PREDICATE TYPES
  object_in_zone, object_near_object, object_stacked_on, object_held_by,
  all_objects_in_zone, sequence_completed, object_visible

Compile the objective into a task graph over the observed object ids."""


async def compile_llm(goal_text: str, g: GroundingResult, state: Any):
    from .base import PlanResult

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": render_context(goal_text, g, state)},
    ]
    models = [settings.planner_model] + [
        m.strip() for m in (settings.planner_fallbacks or "").split(",") if m.strip()
    ]

    data = None
    used = ""
    for model in models:
        # Tool calling first (prose can't come back), then JSON mode, then extraction.
        # Endpoints differ in what they honour, so degrade rather than fail.
        resp = await nim_client.chat(
            model, messages, tier="planner", affinity="planner",
            timeout=settings.planner_timeout, max_tokens=2600, temperature=0.2,
            tools=[PLAN_TOOL], tool_choice={"type": "function", "function": {"name": "emit_plan"}},
            max_attempts=1,
        )
        data = nim_client.tool_args_of(resp, "emit_plan") or nim_client._extract_json(
            nim_client.content_of(resp)
        )
        if not data:
            resp = await nim_client.chat(
                model, messages, tier="planner", affinity="planner",
                timeout=settings.planner_timeout, max_tokens=2600, temperature=0.2,
                response_json=True, max_attempts=1,
            )
            data = nim_client._extract_json(nim_client.content_of(resp))
        if data and isinstance(data.get("actions"), list) and data["actions"]:
            used = model
            break
        data = None

    if not data:
        return None
    log.info("plan compiled by %s", used)

    actions = [_to_action(raw, state) for raw in data["actions"]]
    actions = [a for a in actions if a]
    if not actions:
        return None

    return PlanResult(
        actions=actions,  # type: ignore[arg-type]
        success_predicates=[_to_pred(p) for p in data.get("success_predicates", []) if _to_pred(p)],  # type: ignore[misc]
        source="llm",
        normalized_intent=data.get("normalized_intent", "compiled_objective"),
        notes=data.get("notes") or f"{len(actions)} actions · compiled by {used.split('/')[-1]}",
    )


def _to_action(raw: dict[str, Any], state: Any) -> Action | None:
    try:
        return Action(
            id=str(raw["id"]),
            type=raw["type"],
            description=str(raw.get("description") or "").strip() or "Perform the assigned action.",
            object_id=raw.get("object_id") or None,
            target_object_id=raw.get("target_object_id") or None,
            target_zone=raw.get("target_zone") or None,
            dependencies=[str(d) for d in (raw.get("dependencies") or [])],
            priority=int(raw.get("priority") or 60),
            timeout_seconds=settings.action_timeout,
            expected_predicates=[p for p in map(_to_pred, raw.get("expected_predicates") or []) if p],
        )
    except Exception:
        return None


def _to_pred(raw: Any) -> Predicate | None:
    if not isinstance(raw, dict):
        return None
    try:
        return Predicate(
            type=raw["type"], subject=str(raw["subject"]), object=raw.get("object") or None
        )
    except Exception:
        return None

"""Prompts for the operations compiler.

The constraint that shapes every one of these: **workers cannot see the plan.** Each person
gets one atomic instruction and nothing else, so every description has to stand entirely on
its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import SUPPORTED_ACTIONS

if TYPE_CHECKING:
    from .base import PlanContext

SYSTEM_PROMPT = f"""\
You are HIVE's operations compiler. You convert a high-level objective for a physical
operation into a minimal, validated task graph that will be executed by individual humans
who each receive ONLY their own next instruction.

CRITICAL CONSTRAINT: workers cannot see the objective, the plan, each other's instructions,
or the state of the operation. Every action description must be fully self-contained and
physically unambiguous to someone who knows nothing else. Never write an instruction that
depends on shared knowledge, on another worker's action, or on the word "then".

RULES
- Use ONLY the object ids, zone ids, worker ids, and action types provided. Invent nothing.
- One atomic physical movement per action. No compound actions.
- Express ordering ONLY through the dependencies array. Never through wording.
- Maximize parallelism: actions that touch different objects and different zones must NOT
  depend on each other.
- Never allow two concurrent actions to manipulate the same object or target the same zone.
- When an object is placed on top of another, include a hold action on the base object
  before it and a release action after it.
- Prefer plans that use several different workers over plans that use one worker repeatedly.
- Do not assign workers. Leave assigned_worker_id unset; the scheduler assigns by capability.
- Derive priority from urgency words in the objective (expedited/priority/critical/first = 100,
  blocking-dependency = 85, routine = 70, background = 50). Never from what an object is.
- Include expected_predicates for every action and success_predicates for the objective.
- End with one final inspect action that depends on all zone verifications.
- At most 20 actions. Aim for 9-13.

Action types available: {', '.join(SUPPORTED_ACTIONS)}.

Call emit_plan exactly once. Produce no other output.
"""

REPLAN_SYSTEM_PROMPT = """\
You are HIVE's operations compiler, recovering a live operation. Something in the physical
world diverged from the plan. You will be given the original objective, what is already
verified, what just went wrong, and the true current world state.

Emit ONLY the remaining actions, starting from the world as it actually is now — not from the
beginning. Do not re-do work that is already verified. Put the action that repairs the
deviation first with priority 105.

The same rules apply: one atomic movement per action, ordering only through dependencies,
maximum parallelism, no worker assignments, self-contained descriptions, at most 12 actions.

Call emit_plan exactly once. Produce no other output.
"""

GROUNDING_SYSTEM_PROMPT = """\
You resolve noun phrases in an operational objective to objects a camera has actually
discovered. You will be given the objective and a table of observed objects with measured
descriptors.

Rules:
- Bind a phrase ONLY to an object you can justify from the table. If nothing fits, return null.
- Handle relational and pronoun language: "the other two", "whichever is closest to the dock",
  "the same one".
- Never invent an object id that is not in the table.

Call emit_bindings exactly once. Produce no other output.
"""


def render_context(goal_text: str, ctx: "PlanContext") -> str:
    """Compact tables of workers, objects, zones — plus the current world state."""
    scene = ctx.scene
    lines: list[str] = [f"OBJECTIVE: {goal_text}", ""]

    lines.append("OBJECTS (id | label | colour | shape | current area):")
    for o in scene.objects:
        d = o.descriptor
        lines.append(
            f"  {o.id} | {o.display_label()} | {d.color_name} | {d.shape_hint} | {scene.zone_label(o.zone)} ({o.zone})"
        )

    lines.append("")
    lines.append("AREAS (id | label | adjacency | currently contains):")
    for z in scene.zones:
        others = [x.id for x in scene.zones if x.id != z.id]
        lines.append(f"  {z.id} | {z.label} | {', '.join(others) or 'none'} | {', '.join(z.occupancy) or 'empty'}")
    lines.append("  field | unassigned floor space | all | -")

    lines.append("")
    lines.append("WORKERS (id | callsign | role | can reach | can perform):")
    for w in ctx.workers:
        lines.append(
            f"  {w.id} | {w.callsign} | {w.role or 'operator'} | {', '.join(w.reachable_zones)} | "
            f"{', '.join(w.supported_actions)}"
        )

    if ctx.bindings and ctx.bindings.bindings:
        lines.append("")
        lines.append("PHRASES ALREADY BOUND BY HIVE'S GROUNDING (trust these):")
        for b in ctx.bindings.bindings:
            if b.object_ids:
                lines.append(f"  “{b.phrase}” → {', '.join(b.object_ids)} ({b.basis})")
        for zb in ctx.bindings.zone_bindings:
            lines.append(f"  “{zb.phrase}” → {zb.zone_id}")

    if ctx.constraints:
        lines.append("")
        lines.append("CONSTRAINTS:")
        lines.extend(f"  - {c}" for c in ctx.constraints)

    return "\n".join(lines)


def render_replan(goal_text: str, ctx: "PlanContext", deviation: dict) -> str:
    parts = [render_context(goal_text, ctx), "", "WHAT JUST WENT WRONG:"]
    for key in ("message", "expected", "observed", "action_ids"):
        if deviation.get(key):
            parts.append(f"  {key}: {deviation[key]}")
    if ctx.bindings.deliveries:
        parts.append("")
        parts.append("STILL NOT TRUE IN THE WORLD (object → required area):")
        parts.extend(f"  {oid} → {zid}" for oid, zid in ctx.bindings.deliveries)
    parts.append("")
    parts.append("CURRENT WORLD STATE:")
    parts.append(ctx.world_summary)
    return "\n".join(parts)


def render_grounding(goal_text: str, scene) -> str:
    lines = [f"OBJECTIVE: {goal_text}", "", "OBSERVED OBJECTS (id | label | colour | shape | size | area):"]
    for o in scene.objects:
        d = o.descriptor
        lines.append(
            f"  {o.id} | {o.semantic_label or 'unlabelled'} | {d.color_name} | {d.shape_hint} | "
            f"{d.area_norm:.3f} | {scene.zone_label(o.zone)}"
        )
    lines.append("")
    lines.append("AREAS: " + ", ".join(f"{z.id} ({z.label})" for z in scene.zones))
    return "\n".join(lines)

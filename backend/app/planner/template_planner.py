"""Template planner — the reliable path. Works with no API key, no network.

OWNER: Zechariah. Working implementation — extend in place.

Templates are STRUCTURAL SHAPES, not scripts. A template says "for each (object,
destination) pair emit a move; then verify each destination." It never names an object.
Objects arrive from grounding; destinations arrive from the resolved zones.

If you find yourself typing a colour literal in this file, the design has gone wrong.
"""

from __future__ import annotations

import re
from typing import Any

from ..models import Action, Predicate
from .grounding import GroundingResult

URGENT = ("expedited", "priority", "urgent", "critical", "first", "immediately", "medical", "life")
GATE_PATTERNS = [
    r"cannot (?:start|begin|proceed)[^.]*?until ([^.]+)",
    r"(?:only )?after ([^.,]+?) (?:is|are|has|have)",
    r"requires? ([^.,]+?) (?:first|before)",
    r"until ([^.]+?) (?:is|are) (?:docked|delivered|in place|staged|present)",
]

TEMPLATE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "deliver_to_zones": ("move", "deliver", "bring", "take", "put", "place", "fulfill",
                         "stabilize", "restock", "supply", "evacuate", "route"),
    "assemble_structure": ("stack", "tower", "build", "on top of", "pile"),
    "sort_by_attribute": ("sort", "matching", "each to its", "distribute", "one per"),
    "relay_chain": ("pass", "relay", "through every", "hand off", "handoff"),
    "gather": ("gather", "collect", "bring everything", "consolidate", "central"),
}


def route(goal_text: str, g: GroundingResult) -> str:
    t = goal_text.lower()
    scores = {name: sum(2 for kw in kws if kw in t) for name, kws in TEMPLATE_KEYWORDS.items()}
    if g.destination_count >= 2:
        scores["deliver_to_zones"] += 3
    if "on top of" in t or "stack" in t:
        scores["assemble_structure"] += 5
    if g.destination_count == 1 and g.object_count >= 3:
        scores["gather"] += 3
    if "through every" in t or "each worker" in t:
        scores["relay_chain"] += 5
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "deliver_to_zones"


def _priority_for(goal_text: str, phrase: str, is_gate: bool) -> int:
    t = goal_text.lower()
    window = ""
    idx = t.find(phrase.lower())
    if idx >= 0:
        window = t[max(0, idx - 60) : idx + 60]
    if any(u in window for u in URGENT):
        return 100
    if is_gate:
        return 85
    if any(u in t.split(".")[0] for u in URGENT) and idx >= 0 and idx < len(t) // 2:
        return 80
    return 65


def _find_gate_object(goal_text: str, g: GroundingResult, state: Any) -> str | None:
    """An object the objective says must arrive before other work can proceed."""
    t = goal_text.lower()
    for pat in GATE_PATTERNS:
        m = re.search(pat, t)
        if not m:
            continue
        from .grounding import resolve

        b = resolve(m.group(1), state.scene)
        if b.object_id:
            return b.object_id
    return None


def compile_template(goal_text: str, g: GroundingResult, state: Any) -> tuple[list[Action], list[Predicate], str, str]:
    """Returns (actions, success_predicates, template_name, notes)."""
    name = route(goal_text, g)
    gate = _find_gate_object(goal_text, g, state)
    actions: list[Action] = []
    n = 0

    def nid() -> str:
        nonlocal n
        n += 1
        return f"a{n}"

    by_zone: dict[str, list[str]] = {}
    delivery_action: dict[str, str] = {}

    # ── stage 1: one atomic move per (object, destination). No dependencies →
    #    these are what produce the visible parallel opening wave.
    for obj_id, zone_id in g.deliveries:
        obj = state.scene.by_id(obj_id)
        if not obj:
            continue
        phrase = obj.display_label()
        is_gate = obj_id == gate
        a = Action(
            id=nid(),
            type="place_in_zone",
            description=f"Move the {phrase} to {state.zone_label(zone_id)}.",
            object_id=obj_id,
            target_zone=zone_id,
            priority=_priority_for(goal_text, phrase, is_gate),
            timeout_seconds=state_timeout(state),
            lock_targets=[f"object:{obj_id}"],  # object exclusivity only; zone contention is a soft penalty
            expected_predicates=[Predicate(type="object_in_zone", subject=obj_id, object=zone_id)],
        )
        actions.append(a)
        by_zone.setdefault(zone_id, []).append(a.id)
        delivery_action[obj_id] = a.id

    # ── stage 2: stabilization around a gating resource. Hold it steady while the
    #    dependent deliveries land, then release. This is the serial spine of the graph.
    if gate and gate in delivery_action:
        gate_zone = next((z for o, z in g.deliveries if o == gate), None)
        hold = Action(
            id=nid(),
            type="hold",
            description=f"Hold the {state.label_of(gate)} steady in place.",
            object_id=gate,
            target_zone=gate_zone,
            priority=88,
            timeout_seconds=state_timeout(state),
            dependencies=[delivery_action[gate]],
            lock_targets=[f"object:{gate}"],
            expected_predicates=[Predicate(type="object_in_zone", subject=gate, object=gate_zone)]
            if gate_zone
            else [],
        )
        actions.append(hold)
        siblings = [aid for aid in by_zone.get(gate_zone or "", []) if aid != delivery_action[gate]]
        release = Action(
            id=nid(),
            type="release",
            description=f"Release the {state.label_of(gate)} and step back.",
            object_id=gate,
            target_zone=gate_zone,
            priority=70,
            timeout_seconds=state_timeout(state),
            dependencies=[hold.id] + siblings,
            lock_targets=[f"object:{gate}"],
            expected_predicates=[Predicate(type="object_in_zone", subject=gate, object=gate_zone)]
            if gate_zone
            else [],
        )
        actions.append(release)
        if gate_zone:
            by_zone.setdefault(gate_zone, []).append(release.id)

    # ── stage 3: one verification per destination.
    zone_checks: list[str] = []
    for zone_id, deps in by_zone.items():
        objs = [o for o, z in g.deliveries if z == zone_id]
        chk = Action(
            id=nid(),
            type="inspect",
            description=f"Confirm {state.zone_label(zone_id)} is correctly stocked.",
            target_zone=zone_id,
            priority=55,
            timeout_seconds=state_timeout(state),
            dependencies=sorted(set(deps)),
            lock_targets=[],
            expected_predicates=[
                Predicate(type="all_objects_in_zone", subject="|".join(objs), object=zone_id)
            ],
        )
        actions.append(chk)
        zone_checks.append(chk.id)

    # ── stage 4: one terminal verification depending on every destination check.
    if zone_checks:
        final = Action(
            id=nid(),
            type="inspect",
            description="Final verification across all locations.",
            priority=40,
            timeout_seconds=state_timeout(state),
            dependencies=zone_checks,
            expected_predicates=[Predicate(type="sequence_completed", subject="|".join(zone_checks))],
        )
        actions.append(final)

    success = [
        Predicate(type="object_in_zone", subject=o, object=z) for o, z in g.deliveries
    ]
    parallel = len([a for a in actions if not a.dependencies])
    conflicts = len(g.deliveries) - len({o for o, _ in g.deliveries})
    notes = (
        f"{len(actions)} actions · {parallel} parallel · "
        f"{max(conflicts, 1 if gate else 0)} resource constraint"
        f"{'s' if max(conflicts, 1 if gate else 0) != 1 else ''}"
    )
    return actions, success, name, notes


def state_timeout(state: Any) -> int:
    from ..config import settings

    return settings.action_timeout

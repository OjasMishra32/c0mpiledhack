"""Graph validation and repair.

Ten checks. The first six repair themselves and surface quietly in the UI; the last four are
fatal and send the caller down the fallback chain. Validation messages are read by a person
standing in front of a projector, so they are written for a person:

    ✗ ValidationError: cycle detected in DAG at node a7
    ✓ Plan rejected: actions a5 and a7 each wait on the other. Recompiling from template library.

`topo_layers()` is shared: the orchestrator unlocks dependents with it and the host lays the
DAG out in columns from it, so parallelism is literally the height of a column.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable

import networkx as nx

from ..models import Action, ActionType, Predicate, PredicateType, SUPPORTED_ACTIONS

if TYPE_CHECKING:  # annotations only — importing base at runtime would be circular
    from .base import PlanContext, PlanResult


# A plan is only complete if something observable becomes true. These are those somethings.
MEASURABLE_PREDICATES = {
    PredicateType.object_in_zone.value,
    PredicateType.object_stacked_on.value,
    PredicateType.all_objects_in_zone.value,
    PredicateType.object_visible.value,
}


@dataclass
class ValidationReport:
    ok: bool = True
    errors: list[str] = field(default_factory=list)  # fatal → reject the plan, fall back
    repairs: list[str] = field(default_factory=list)  # fixed automatically → surface subtly


def build_graph(actions: Iterable[Action]) -> nx.DiGraph:
    g = nx.DiGraph()
    g.add_nodes_from(a.id for a in actions)
    known = {a.id for a in actions}
    g.add_edges_from((d, a.id) for a in actions for d in a.dependencies if d in known)
    return g


def topo_layers(actions: Iterable[Action]) -> list[list[str]]:
    """Dependency generations, in order. Each generation is one column on the host screen."""
    actions = list(actions)
    g = build_graph(actions)
    if not nx.is_directed_acyclic_graph(g):
        return [[a.id] for a in actions]
    return [sorted(layer) for layer in nx.topological_generations(g)]


def plan_stats(actions: list[Action]) -> dict:
    layers = topo_layers(actions)
    conflicts = 0
    holders: dict[str, list[str]] = defaultdict(list)
    for a in actions:
        for lock in a.lock_targets:
            holders[lock].append(a.id)
    conflicts = sum(1 for ids in holders.values() if len(ids) > 1)
    return {
        "action_count": len(actions),
        "parallel_peak": max((len(l) for l in layers), default=0),
        "opening_wave": len([a for a in actions if not a.dependencies]),
        "layer_count": len(layers),
        "resource_conflicts": conflicts,
    }


def notes_for(actions: list[Action]) -> str:
    s = plan_stats(actions)
    return (
        f"{s['action_count']} actions · {s['opening_wave']} parallel · "
        f"{s['resource_conflicts']} resource conflicts"
    )


def _synth_predicates(action: Action) -> list[Predicate]:
    """Check 4 — a plan without expected predicates cannot be verified, so derive them.

    Every predicate emitted here must be one `verifier.check_predicate` can actually satisfy
    from the world state. That constraint is load-bearing: `verifier.evaluate` refuses to
    verify an action while *any* of its predicates is unsatisfied, so a predicate nothing can
    ever report does not degrade the action — it strands it, and the run stops there.

    Two encodings are deliberately avoided:
      * `object_held_by` — the holder is unknown at plan time (the scheduler assigns later),
        and nothing in the pipeline reports `held_by` for a human's hands.
      * `object_stacked_on` — nothing sets `stacked_on` today, so a stack asserts the
        weaker-but-observable fact that the top item reached the base's area.
    """
    t, oid = action.type, action.object_id
    if t in (ActionType.place_in_zone.value, ActionType.move_to_zone.value) and oid and action.target_zone:
        return [Predicate(type=PredicateType.object_in_zone.value, subject=oid, object=action.target_zone)]
    if t in (ActionType.place_on.value, ActionType.hold.value) and oid:
        if action.target_zone:
            return [Predicate(type=PredicateType.object_in_zone.value, subject=oid, object=action.target_zone)]
        return [Predicate(type=PredicateType.object_visible.value, subject=oid)]
    if t in (ActionType.pick_up.value, ActionType.release.value) and oid:
        return [Predicate(type=PredicateType.object_visible.value, subject=oid)]
    if t == ActionType.inspect.value:
        if action.target_zone:
            return [Predicate(type=PredicateType.all_objects_in_zone.value, subject=action.target_zone,
                              object=action.target_zone)]
        # subject="objective" is the sentinel for "every other action"; naming this action's
        # own id here would make the predicate wait on itself forever.
        return [Predicate(type=PredicateType.sequence_completed.value, subject="objective")]
    if oid:
        return [Predicate(type=PredicateType.object_visible.value, subject=oid)]
    return [Predicate(type=PredicateType.sequence_completed.value, subject="objective")]


def _derive_locks(action: Action) -> list[str]:
    """Check 5 — objects are exclusive, zones are not. See the note in template_planner.py."""
    locks = []
    if action.object_id:
        locks.append(f"object:{action.object_id}")
    if action.target_object_id:
        locks.append(f"object:{action.target_object_id}")
    return locks


def validate_and_repair(plan: "PlanResult", ctx: "PlanContext") -> ValidationReport:
    """Validate in place. Templates get validated too — it catches your own typos."""
    report = ValidationReport()
    actions: list[Action] = plan.actions
    scene = ctx.scene
    workers = list(ctx.workers or [])
    supported = set(ctx.supported_actions or SUPPORTED_ACTIONS)

    known_objects = {o.id for o in scene.objects}
    known_zones = {z.id for z in scene.zones} | {"field"}
    known_workers = {w.id for w in workers}
    available_workers = {w.id for w in workers if w.available and w.connected}

    if not actions:
        report.ok = False
        report.errors.append("Plan rejected: it contains no actions. Recompiling from the template library.")
        return report

    # 3 — duplicate ids (first, so every later message names a unique action)
    seen: set[str] = set()
    for a in actions:
        if a.id in seen:
            new_id = a.id
            n = 2
            while f"{new_id}_{n}" in seen:
                n += 1
            report.repairs.append(f"Two actions were both called {a.id}; renamed one to {a.id}_{n}.")
            a.id = f"{new_id}_{n}"
        seen.add(a.id)

    # 8 — unsupported action type (fatal)
    bad_types = sorted({a.type for a in actions if a.type not in supported})
    if bad_types:
        report.ok = False
        report.errors.append(
            "Plan rejected: it asks for "
            + ", ".join(f"“{t}”" for t in bad_types)
            + ", which no worker can perform."
        )
        return report

    # 1 — unknown object / zone references: drop the action
    dropped: set[str] = set()
    for a in list(actions):
        if a.object_id and a.object_id not in known_objects:
            report.repairs.append(f"Dropped {a.id}: it referenced {a.object_id}, which the camera does not see.")
            dropped.add(a.id)
        elif a.target_object_id and a.target_object_id not in known_objects:
            report.repairs.append(
                f"Dropped {a.id}: it referenced {a.target_object_id}, which the camera does not see."
            )
            dropped.add(a.id)
        elif a.target_zone and a.target_zone not in known_zones:
            report.repairs.append(f"Dropped {a.id}: {a.target_zone} is not a defined area.")
            dropped.add(a.id)
    if dropped:
        actions[:] = [a for a in actions if a.id not in dropped]
        if not actions:
            report.ok = False
            report.errors.append(
                "Plan rejected: every action referred to something HIVE cannot see. Rescan the scene."
            )
            return report

    # 2 — dependencies on actions that do not exist
    live_ids = {a.id for a in actions}
    for a in actions:
        missing = [d for d in a.dependencies if d not in live_ids]
        if missing:
            a.dependencies = [d for d in a.dependencies if d in live_ids]
            report.repairs.append(
                f"{a.id} waited on {', '.join(missing)}, which is not part of this plan; that wait was removed."
            )
        if a.id in a.dependencies:
            a.dependencies = [d for d in a.dependencies if d != a.id]
            report.repairs.append(f"{a.id} depended on itself; removed.")

    # 4 / 5 — verifiability and concurrency safety
    for a in actions:
        if not a.expected_predicates:
            a.expected_predicates = _synth_predicates(a)
            report.repairs.append(f"Added a verification condition to {a.id}.")
        if not a.lock_targets:
            a.lock_targets = _derive_locks(a)
            if a.lock_targets:
                report.repairs.append(f"Derived resource locks for {a.id}.")

    # 6 — pre-assigned to a worker who cannot take it
    for a in actions:
        wid = a.assigned_worker_id
        if wid and (wid not in known_workers or (available_workers and wid not in available_workers)):
            a.assigned_worker_id = None
            a.assignment_reason = None
            report.repairs.append(f"{a.id} was pinned to an unavailable worker; the scheduler will assign it.")

    # 7 — cycles (fatal). NetworkX, not hand-rolled.
    g = build_graph(actions)
    if not nx.is_directed_acyclic_graph(g):
        try:
            cycle = nx.find_cycle(g)
            names = [n for n, _ in cycle]
        except nx.NetworkXNoCycle:  # pragma: no cover — is_directed_acyclic_graph said otherwise
            names = []
        if len(names) == 2:
            detail = f"actions {names[0]} and {names[1]} each wait on the other"
        else:
            detail = "circular dependency: " + " → ".join(names + names[:1])
        report.ok = False
        report.errors.append(f"Plan rejected: {detail}. Recompiling from the template library.")
        return report

    # 9 — a zone nobody can reach (fatal)
    if workers:
        for a in actions:
            z = a.target_zone
            if z and z != "field" and not any(z in w.reachable_zones for w in workers):
                label = scene.zone_label(z)
                report.ok = False
                report.errors.append(
                    f"Plan rejected: no worker can reach {label}, so {a.id} could never run."
                )
                return report

    # 10 — a single terminal action, and success conditions for the objective
    sinks = [a.id for a in actions if g.out_degree(a.id) == 0]
    if len(sinks) > 1:
        terminal = Action(
            id=_next_id(live_ids),
            type=ActionType.inspect.value,
            description="Confirm the objective is complete across every area.",
            dependencies=sorted(sinks),
            priority=60,
            expected_predicates=[Predicate(type=PredicateType.sequence_completed.value, subject="objective")],
            lock_targets=[],
        )
        actions.append(terminal)
        report.repairs.append("Appended a final verification step so the plan has one clear finish line.")
        sinks = [terminal.id]

    if not plan.success_predicates:
        synthesized: list[Predicate] = []
        for a in actions:
            for p in a.expected_predicates:
                if p.type in MEASURABLE_PREDICATES and p not in synthesized:
                    synthesized.append(p)
        if synthesized:
            plan.success_predicates = synthesized
            report.repairs.append("Derived the objective's success conditions from the plan's own steps.")
        else:
            report.ok = False
            report.errors.append(
                "Plan rejected: it has no measurable success condition, so HIVE could never call it complete."
            )
            return report

    return report


def _next_id(existing: set[str]) -> str:
    n = len(existing) + 1
    while f"a{n}" in existing:
        n += 1
    return f"a{n}"

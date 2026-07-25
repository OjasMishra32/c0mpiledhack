"""Task graph validation and repair.

OWNER: Zechariah. Working implementation — extend in place.

Six repairs, four fatal checks. Messages are user-facing: write them for a person,
not a stack trace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from ..models import SUPPORTED_ACTIONS, Action, Predicate


@dataclass
class ValidationReport:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)


def build_graph(actions: list[Action]) -> nx.DiGraph:
    g = nx.DiGraph()
    g.add_nodes_from(a.id for a in actions)
    for a in actions:
        for d in a.dependencies:
            if d in g:
                g.add_edge(d, a.id)
    return g


def topo_layers(actions: list[Action]) -> list[list[str]]:
    """Generations for the host DAG layout: column = time, column height = parallelism."""
    g = build_graph(actions)
    try:
        return [sorted(layer) for layer in nx.topological_generations(g)]
    except Exception:
        return [[a.id for a in actions]]


def validate_and_repair(actions: list[Action], state: Any) -> tuple[list[Action], ValidationReport]:
    rep = ValidationReport()
    known_objects = {o.id for o in state.scene.objects}
    known_zones = {z.id for z in state.scene.zones} | {"field"}
    known_workers = set(state.workers)

    kept: list[Action] = []
    seen_ids: set[str] = set()

    for a in actions:
        # 3. duplicate ids
        if a.id in seen_ids:
            new_id = f"{a.id}_{len(seen_ids)}"
            rep.repairs.append(f"Duplicate action id {a.id} renamed to {new_id}.")
            a.id = new_id
        seen_ids.add(a.id)

        # 8. unsupported type (fatal)
        if a.type not in SUPPORTED_ACTIONS:
            rep.errors.append(f"Action {a.id} uses unsupported type “{a.type}”.")
            continue

        # 1. unknown object / zone
        if a.object_id and a.object_id not in known_objects:
            rep.repairs.append(f"Dropped {a.id}: references an item that is not on the table.")
            continue
        if a.target_zone and a.target_zone not in known_zones:
            rep.repairs.append(f"Dropped {a.id}: references an unknown location.")
            continue

        # 6. bogus worker assignment
        if a.assigned_worker_id and a.assigned_worker_id not in known_workers:
            rep.repairs.append(f"Cleared invalid worker assignment on {a.id}.")
            a.assigned_worker_id = None

        # 4. missing predicates
        if not a.expected_predicates:
            if a.object_id and a.target_zone:
                a.expected_predicates = [
                    Predicate(type="object_in_zone", subject=a.object_id, object=a.target_zone)
                ]
                rep.repairs.append(f"Synthesized success condition for {a.id}.")
            elif a.type == "inspect" and a.dependencies:
                a.expected_predicates = [
                    Predicate(type="sequence_completed", subject="|".join(a.dependencies))
                ]

        # 5. missing locks
        if not a.lock_targets:
            locks = []
            if a.object_id:
                locks.append(f"object:{a.object_id}")
            # Deliberately no zone lock: two workers may stock the same zone at once.
            # Simultaneous manipulation of the same OBJECT is the real hazard.
            a.lock_targets = locks

        kept.append(a)

    # 2. dangling dependencies
    ids = {a.id for a in kept}
    for a in kept:
        missing = [d for d in a.dependencies if d not in ids]
        if missing:
            a.dependencies = [d for d in a.dependencies if d in ids]
            rep.repairs.append(f"Removed {len(missing)} dangling dependency on {a.id}.")

    # 7. cycles (fatal)
    g = build_graph(kept)
    if kept and not nx.is_directed_acyclic_graph(g):
        try:
            cycle = nx.find_cycle(g)
            names = " → ".join(n for n, _ in cycle)
            rep.errors.append(f"Plan rejected: actions {names} each wait on the other.")
        except Exception:
            rep.errors.append("Plan rejected: circular dependency detected.")

    # 9. unreachable target (fatal)
    for a in kept:
        if not a.target_zone or a.target_zone == "field":
            continue
        if not any(a.target_zone in w.reachable_zones for w in state.workers.values()):
            rep.errors.append(
                f"Plan rejected: no worker can reach {state.zone_label(a.target_zone)}."
            )
            break

    # 10. empty plan (fatal)
    if not kept:
        rep.errors.append("Plan rejected: no executable actions.")

    rep.ok = not rep.errors
    return kept, rep

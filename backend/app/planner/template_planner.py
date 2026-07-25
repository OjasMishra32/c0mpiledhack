"""The template planner — structural shapes, not scripts.

A template says *"for each (object, destination) pair, emit a move; then emit a verification
per destination."* It never names an object. Objects arrive from grounding, destinations from
the resolved zones, so the same `deliver_to_zones` template produces a warehouse plan, an
incident plan, or a plan about coffee cups. The difference is entirely in the bindings.

If you find yourself typing a colour literal in this file, the design has gone wrong: the
colour lives in `descriptor.color_name` and the *meaning* lives in the binding.

This planner is the product. The LLM planner is the upgrade.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from ..models import Action, ActionType, ObservedObject, Predicate, PredicateType, Scene
from . import validator
from .grounding import PRIORITY_GATING, PRIORITY_ROUTINE, GroundingResult

if TYPE_CHECKING:
    from .base import PlanContext, PlanResult

TEMPLATE_KEYWORDS: dict[str, list[str]] = {
    "deliver_to_zones": [
        "move", "deliver", "bring", "take", "put", "fulfill", "fulfil", "stabilize",
        "stabilise", "restock", "stage", "evacuate", "transfer",
    ],
    "assemble_structure": ["stack", "tower", "build", "on top of", "assemble", "pile"],
    "sort_by_attribute": ["sort", "matching", "each to its", "distribute", "separate", "categorize"],
    "relay_chain": ["pass", "relay", "through", "hand off", "hand it", "chain"],
    "sequence_arrange": ["arrange", "order", "sequence", "left to right", "line up", "in order"],
    "gather": ["gather", "collect", "bring everything", "consolidate", "muster", "assemble at"],
}

MAX_ACTIONS = 20

# Locks are per-OBJECT, not per-zone. Two people cannot carry the same item, but several can
# walk to the same station — zone contention is a soft cost in the scheduler
# (`collision_penalty`), not a veto. Locking zones here would serialise the opening wave and
# the graph would read as a queue instead of a floor.


def route(goal_text: str, bindings: GroundingResult) -> str:
    """Pick a template from the wording plus *structural* signals in the bindings."""
    t = goal_text.lower()
    scores = {name: sum(2 for kw in kws if kw in t) for name, kws in TEMPLATE_KEYWORDS.items()}
    if bindings.distinct_destinations >= 2:
        scores["deliver_to_zones"] += 3
    if bindings.mentions_relation("on top of") or bindings.stack_relations:
        scores["assemble_structure"] += 5
    if bindings.destination_count == 1 and bindings.object_count >= 3:
        scores["gather"] += 3
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "deliver_to_zones"


# ── shared construction helpers ────────────────────────────────────────────────


class _Builder:
    def __init__(self, ctx: "PlanContext") -> None:
        self.ctx = ctx
        self.scene: Scene = ctx.scene
        self.bindings: GroundingResult = ctx.bindings
        self.actions: list[Action] = []
        self.warnings: list[str] = []
        self._n = 0

    def _id(self) -> str:
        self._n += 1
        return f"a{self._n}"

    def phrase(self, obj: ObservedObject) -> str:
        label = obj.display_label()
        return label if label.lower().startswith(("the ", "a ", "an ")) else f"the {label}"

    def zone_label(self, zone_id: str) -> str:
        return self.scene.zone_label(zone_id)

    def add(self, **kwargs) -> Action:
        action = Action(id=self._id(), **kwargs)
        if not action.lock_targets:
            action.lock_targets = validator._derive_locks(action)
        if not action.expected_predicates:
            action.expected_predicates = validator._synth_predicates(action)
        self.actions.append(action)
        return action

    # ── the objects and destinations this plan operates over ──

    def objects(self) -> list[ObservedObject]:
        bound = [self.scene.by_id(i) for i in self.bindings.bound_object_ids]
        objs = [o for o in bound if o is not None]
        if objs:
            return objs
        visible = self.scene.visible_objects
        if visible:
            self.warnings.append(
                f"No phrase in the objective resolved to a specific object; planned over all "
                f"{len(visible)} discovered objects."
            )
        return visible

    def destinations(self) -> list[str]:
        dests = self.bindings.destinations
        if dests:
            return dests
        zones = [z.id for z in self.scene.zones if z.id != "field"]
        if zones and self.bindings.unbound_places:
            named = ", ".join(f"“{p}”" for p in self.bindings.unbound_places)
            self.warnings.append(
                f"{named} is not a defined area yet — draw it on the feed and recompile. "
                f"Work was staged across the detected areas meanwhile."
            )
        elif zones:
            self.warnings.append(
                "The objective named no destination; work was distributed across the detected areas."
            )
        return zones

    def deliveries(self) -> list[tuple[str, str]]:
        pairs = [
            (oid, zid)
            for oid, zid in self.bindings.deliveries
            if self.scene.by_id(oid) is not None
        ]
        if pairs:
            return pairs
        objs, dests = self.objects(), self.destinations()
        if not objs or not dests:
            return []
        return [(o.id, dests[i % len(dests)]) for i, o in enumerate(objs)]

    def priority(self, object_id: str) -> int:
        return self.bindings.priority_for(object_id)

    # ── shared graph endings ──

    def verify_zones(self, per_zone: dict[str, list[str]]) -> list[str]:
        """One terminating inspection per destination — the zone's success condition."""
        ids = []
        for zone_id, deps in per_zone.items():
            action = self.add(
                type=ActionType.inspect.value,
                target_zone=zone_id,
                description=f"Check {self.zone_label(zone_id)} and confirm everything expected there has arrived.",
                dependencies=sorted(deps),
                priority=PRIORITY_ROUTINE - 5,
                expected_predicates=[
                    Predicate(type=PredicateType.all_objects_in_zone.value, subject=zone_id, object=zone_id)
                ],
                lock_targets=[],
            )
            ids.append(action.id)
        return ids

    def finalize(self, deps: list[str]) -> Action:
        """One final verification depending on every zone inspection — the visible funnel."""
        return self.add(
            type=ActionType.inspect.value,
            description="Confirm the objective is complete across every area.",
            dependencies=sorted(deps),
            priority=PRIORITY_ROUTINE - 10,
            expected_predicates=[
                Predicate(type=PredicateType.sequence_completed.value, subject="objective")
            ],
            lock_targets=[],
        )

    def apply_gates(self, move_by_object: dict[str, str]) -> None:
        """Dependencies stated in prose: "packing can't start until the scanner is docked".

        A gate constrains the *work* in that area, not the arrival of higher-priority items:
        an expedited item may reach the pack station before the scanner does. So gates only
        block actions whose priority is below the gate's. That is what keeps the opening wave
        wide — and it is also just true of how a floor actually runs.
        """
        for gate in self.bindings.gates:
            gate_ids = [move_by_object[oid] for oid in gate.gate_object_ids if oid in move_by_object]
            if not gate_ids or not gate.gated_zone_id:
                continue
            for a in self.actions:
                if a.target_zone != gate.gated_zone_id or a.id in gate_ids:
                    continue
                if a.priority >= PRIORITY_GATING:
                    continue
                for gid in gate_ids:
                    if gid != a.id and gid not in a.dependencies:
                        a.dependencies.append(gid)

    def success_predicates(self) -> list[Predicate]:
        out: list[Predicate] = []
        for a in self.actions:
            for p in a.expected_predicates:
                if p.type in validator.MEASURABLE_PREDICATES and p not in out:
                    out.append(p)
        return out

    def trim(self) -> None:
        """Keep it readable from ten feet. 11 actions is ideal, 20 is the ceiling."""
        if len(self.actions) <= MAX_ACTIONS:
            return
        keep = self.actions[:MAX_ACTIONS]
        kept = {a.id for a in keep}
        for a in keep:
            a.dependencies = [d for d in a.dependencies if d in kept]
        dropped = len(self.actions) - len(keep)
        self.actions = keep
        self.warnings.append(f"Objective was larger than one operation; {dropped} steps deferred.")


# ── templates ──────────────────────────────────────────────────────────────────


def deliver_to_zones(b: _Builder) -> None:
    """N parallel moves + per-zone verify + final verify — the workhorse."""
    per_zone: dict[str, list[str]] = defaultdict(list)
    move_by_object: dict[str, str] = {}
    for object_id, zone_id in b.deliveries():
        obj = b.scene.by_id(object_id)
        if obj is None:
            continue
        action = b.add(
            type=ActionType.place_in_zone.value,
            object_id=object_id,
            target_zone=zone_id,
            description=f"Move {b.phrase(obj)} to {b.zone_label(zone_id)} and set it down inside the marked area.",
            priority=b.priority(object_id),
            dependencies=[],
            lock_targets=[f"object:{object_id}"],
            expected_predicates=[
                Predicate(type=PredicateType.object_in_zone.value, subject=object_id, object=zone_id)
            ],
        )
        per_zone[zone_id].append(action.id)
        move_by_object[object_id] = action.id
    b.apply_gates(move_by_object)
    b.finalize(b.verify_zones(per_zone))


def assemble_structure(b: _Builder) -> None:
    """Serial stack + hold + release. Stacking always stabilizes the base first."""
    pairs = b.bindings.stack_relations
    if not pairs:
        ids = [o.id for o in b.objects()]
        pairs = [(ids[i], ids[i - 1]) for i in range(1, len(ids))]
    previous: list[str] = []
    placed: list[str] = []
    for top_id, base_id in pairs:
        top, base = b.scene.by_id(top_id), b.scene.by_id(base_id)
        if top is None or base is None:
            continue
        hold = b.add(
            type=ActionType.hold.value,
            object_id=base_id,
            description=f"Hold {b.phrase(base)} steady with both hands and keep it still.",
            priority=PRIORITY_GATING,
            dependencies=list(previous),
            lock_targets=[f"object:{base_id}"],
        )
        place = b.add(
            type=ActionType.place_on.value,
            object_id=top_id,
            target_object_id=base_id,
            description=f"Place {b.phrase(top)} on top of {b.phrase(base)}, centred, then let go slowly.",
            priority=b.priority(top_id),
            dependencies=[hold.id],
            lock_targets=[f"object:{top_id}", f"object:{base_id}"],
            expected_predicates=[
                Predicate(type=PredicateType.object_stacked_on.value, subject=top_id, object=base_id)
            ],
        )
        release = b.add(
            type=ActionType.release.value,
            object_id=base_id,
            description=f"Let go of {b.phrase(base)} and step back.",
            priority=PRIORITY_ROUTINE,
            dependencies=[place.id],
            lock_targets=[f"object:{base_id}"],
        )
        previous = [release.id]
        placed.append(place.id)
    if not placed:
        deliver_to_zones(b)
        return

    # A goal can both stack and deliver ("put the red cup in the dock and stack the other two").
    stacked = {oid for pair in pairs for oid in pair}
    per_zone: dict[str, list[str]] = defaultdict(list)
    for object_id, zone_id in b.deliveries():
        obj = b.scene.by_id(object_id)
        if obj is None or object_id in stacked:
            continue
        move = b.add(
            type=ActionType.place_in_zone.value,
            object_id=object_id,
            target_zone=zone_id,
            description=f"Move {b.phrase(obj)} to {b.zone_label(zone_id)} and set it down inside the marked area.",
            priority=b.priority(object_id),
            dependencies=[],
            lock_targets=[f"object:{object_id}"],
            expected_predicates=[
                Predicate(type=PredicateType.object_in_zone.value, subject=object_id, object=zone_id)
            ],
        )
        per_zone[zone_id].append(move.id)

    inspect = b.add(
        type=ActionType.inspect.value,
        description="Confirm the stack is upright and stable, then step back.",
        dependencies=sorted(previous + placed),
        priority=PRIORITY_ROUTINE - 5,
        expected_predicates=[
            Predicate(type=PredicateType.object_stacked_on.value, subject=top_id, object=base_id)
        ],
        lock_targets=[],
    )
    b.finalize([inspect.id] + b.verify_zones(per_zone))


def sort_by_attribute(b: _Builder) -> None:
    """N-way parallel; each object's destination is derived from the object itself."""
    from .grounding import _match_zone  # descriptor → zone label, no literals anywhere

    dests = b.destinations()
    if not dests:
        survey(b)
        return
    per_zone: dict[str, list[str]] = defaultdict(list)
    for i, obj in enumerate(b.objects()):
        zone_id, conf, _ = _match_zone(obj.display_label(), b.scene)
        if not zone_id or conf < 0.5 or zone_id not in dests:
            zone_id = dests[i % len(dests)]
        action = b.add(
            type=ActionType.place_in_zone.value,
            object_id=obj.id,
            target_zone=zone_id,
            description=f"Move {b.phrase(obj)} to {b.zone_label(zone_id)} and set it down inside the marked area.",
            priority=b.priority(obj.id),
            dependencies=[],
            lock_targets=[f"object:{obj.id}"],
            expected_predicates=[
                Predicate(type=PredicateType.object_in_zone.value, subject=obj.id, object=zone_id)
            ],
        )
        per_zone[zone_id].append(action.id)
    b.finalize(b.verify_zones(per_zone))


def relay_chain(b: _Builder) -> None:
    """Strictly serial across every available worker. The DAG renders as a straight line."""
    objs = b.objects()
    if not objs:
        survey(b)
        return
    obj = objs[0]
    workers = [w for w in b.ctx.workers if w.connected and w.available] or list(b.ctx.workers)
    hops = max(2, len(workers) - 1)
    previous: list[str] = []
    for hop in range(hops):
        action = b.add(
            type=ActionType.pick_up.value,
            object_id=obj.id,
            description=(
                f"Take {b.phrase(obj)} from the person handing it to you and pass it to the next person."
                if hop
                else f"Pick up {b.phrase(obj)} and pass it to the next person."
            ),
            priority=b.priority(obj.id),
            dependencies=list(previous),
            lock_targets=[f"object:{obj.id}"],
            expected_predicates=[Predicate(type=PredicateType.worker_acknowledged.value, subject=obj.id)],
        )
        previous = [action.id]
    dests = b.destinations()
    zone_id = dests[0] if dests else obj.zone
    final_move = b.add(
        type=ActionType.place_in_zone.value,
        object_id=obj.id,
        target_zone=zone_id,
        description=f"Set {b.phrase(obj)} down inside {b.zone_label(zone_id)}.",
        priority=b.priority(obj.id),
        dependencies=list(previous),
        lock_targets=[f"object:{obj.id}"],
        expected_predicates=[
            Predicate(type=PredicateType.object_in_zone.value, subject=obj.id, object=zone_id)
        ],
    )
    b.finalize(b.verify_zones({zone_id: [final_move.id]}))


def sequence_arrange(b: _Builder) -> None:
    """Positional placement in the stated order — serial by construction."""
    dests = b.destinations()
    if not dests:
        survey(b)
        return
    zone_id = dests[0]
    previous: list[str] = []
    ids: list[str] = []
    for position, obj in enumerate(b.objects(), start=1):
        action = b.add(
            type=ActionType.place_in_zone.value,
            object_id=obj.id,
            target_zone=zone_id,
            description=(
                f"Place {b.phrase(obj)} in {b.zone_label(zone_id)} as item number {position}, "
                f"immediately to the right of the previous one."
            ),
            priority=b.priority(obj.id),
            dependencies=list(previous),
            lock_targets=[f"object:{obj.id}"],
            expected_predicates=[
                Predicate(type=PredicateType.object_in_zone.value, subject=obj.id, object=zone_id)
            ],
        )
        previous = [action.id]
        ids.append(action.id)
    b.finalize(b.verify_zones({zone_id: ids}))


def gather(b: _Builder) -> None:
    """N-way converge on one zone."""
    dests = b.destinations()
    if not dests:
        survey(b)
        return
    zone_id = dests[0]
    ids: list[str] = []
    for obj in b.objects():
        action = b.add(
            type=ActionType.place_in_zone.value,
            object_id=obj.id,
            target_zone=zone_id,
            description=f"Bring {b.phrase(obj)} to {b.zone_label(zone_id)} and set it down inside the marked area.",
            priority=b.priority(obj.id),
            dependencies=[],
            lock_targets=[f"object:{obj.id}"],
            expected_predicates=[
                Predicate(type=PredicateType.object_in_zone.value, subject=obj.id, object=zone_id)
            ],
        )
        ids.append(action.id)
    b.finalize(b.verify_zones({zone_id: ids}))


def survey(b: _Builder) -> None:
    """Last-resort shape: no destination anywhere, so verify what is present.

    This exists so a scene with no zones still produces a valid, honest graph instead of an
    empty plan. It is never the interesting answer, and the warning says so.
    """
    b.warnings.append("No destination could be resolved; HIVE compiled a verification sweep instead.")
    ids = []
    for obj in b.objects():
        action = b.add(
            type=ActionType.inspect.value,
            object_id=obj.id,
            description=f"Find {b.phrase(obj)}, confirm it is present and undamaged, and report.",
            priority=PRIORITY_ROUTINE,
            dependencies=[],
            expected_predicates=[Predicate(type=PredicateType.object_visible.value, subject=obj.id)],
            lock_targets=[f"object:{obj.id}"],
        )
        ids.append(action.id)
    b.finalize(ids)


TEMPLATES = {
    "deliver_to_zones": deliver_to_zones,
    "assemble_structure": assemble_structure,
    "sort_by_attribute": sort_by_attribute,
    "relay_chain": relay_chain,
    "sequence_arrange": sequence_arrange,
    "gather": gather,
}


class TemplatePlanner:
    """Never fails. That is its entire job."""

    def compile_sync(self, goal_text: str, ctx: "PlanContext") -> "PlanResult":
        from .base import PlanResult  # local import: base owns the fallback chain

        name = route(goal_text, ctx.bindings)
        b = _Builder(ctx)
        TEMPLATES[name](b)
        if not b.actions:
            survey(b)
        b.trim()
        return PlanResult(
            actions=b.actions,
            success_predicates=b.success_predicates(),
            source="template",
            normalized_intent=name,
            notes=validator.notes_for(b.actions),
            warnings=b.warnings,
            grounding=ctx.bindings,
            template=name,
        )

    async def compile(self, goal_text: str, ctx: "PlanContext") -> "PlanResult":
        return self.compile_sync(goal_text, ctx)

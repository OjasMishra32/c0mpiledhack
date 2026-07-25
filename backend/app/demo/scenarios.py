"""Scenario library.

A scenario does NOT declare what objects exist — objects are discovered by perception.
A scenario supplies starting conditions and framing: a suggested objective, zone labels
and geometry, worker roles and reachability, UI lexicon, and a parachute plan template.

See docs/SCENARIOS.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Rect, Zone

# Zone geometry is scenario-independent so a scenario switch never invalidates the
# camera setup. Labels change; ids and rectangles do not.
ZONE_GEOMETRY: list[tuple[str, Rect]] = [
    ("zone_1", Rect(x=0.02, y=0.28, w=0.24, h=0.44)),  # west
    ("zone_2", Rect(x=0.74, y=0.28, w=0.24, h=0.44)),  # east
    ("zone_3", Rect(x=0.36, y=0.02, w=0.28, h=0.24)),  # north
    ("zone_4", Rect(x=0.34, y=0.74, w=0.32, h=0.24)),  # south
]

WORKER_SEATS: dict[str, tuple[float, float]] = {
    "worker_a": (0.08, 0.50),
    "worker_b": (0.50, 0.08),
    "worker_c": (0.50, 0.50),
    "worker_d": (0.92, 0.50),
    "worker_e": (0.50, 0.92),
}


@dataclass
class Scenario:
    id: str
    title: str
    subtitle: str
    suggested_goal: str
    zone_labels: list[str]
    worker_roles: dict[str, str]
    worker_reachability: dict[str, list[str]]
    expected_roles: list[str]  # GROUNDING HINTS ONLY — never assumed present
    recommended_failure: str
    expected_recovery: str
    comms_profile: str = "voice"
    lexicon: dict[str, str] = field(default_factory=dict)

    goal_template: str = ""

    def build_zones(self) -> list[Zone]:
        return [
            Zone(id=zid, label=self.zone_labels[i], bounds=rect, status="pending", source="drawn")
            for i, (zid, rect) in enumerate(ZONE_GEOMETRY)
        ]

    def build_goal(self, scene) -> str:
        """Compose the suggested objective from what the camera ACTUALLY sees.

        The operator can type anything; this is only the prefilled starting point. But
        grounding it in observed colours means the default demo always binds to real
        objects — and it is honest, because HIVE is proposing work for the items in
        front of it rather than for items it hopes are there.
        """
        if not self.goal_template or not scene.objects:
            return self.suggested_goal
        colors, seen = [], set()
        for o in scene.objects:
            c = o.descriptor.color_name
            if c not in seen:
                seen.add(c)
                colors.append(c)
        if len(colors) < self.goal_template.count("{c"):
            return self.suggested_goal
        slots = {f"c{i+1}": c for i, c in enumerate(colors)}
        slots.update({f"z{i+1}": lbl for i, lbl in enumerate(self.zone_labels)})
        try:
            return self.goal_template.format(**slots)
        except Exception:
            return self.suggested_goal


DEFAULT_LEXICON = {
    "collective": "COLLECTIVE",
    "worker": "Worker",
    "objective": "OBJECTIVE",
    "object": "Item",
    "zone": "Zone",
    "complete": "OBJECTIVE COMPLETE",
    "deviation": "WORLD STATE DEVIATION",
}

_ALL = ["zone_1", "zone_2", "zone_3", "zone_4", "field"]

WAREHOUSE = Scenario(
    id="warehouse_fulfillment",
    title="Warehouse Fulfillment",
    subtitle="Expedited order under floor disruption",
    suggested_goal=(
        "Fulfill expedited order 4471 at the pack station and restock pick aisle B. "
        "Order 4471 needs the red item and the blue item at the pack station. "
        "Packing cannot start until the scanner and the packing materials are at the pack station."
    ),
    zone_labels=["Inbound Dock", "Pack Station", "Pick Aisle A", "Pick Aisle B"],
    worker_roles={
        "worker_a": "Picker A",
        "worker_b": "Picker B",
        "worker_c": "Runner C",
        "worker_d": "Packer D",
        "worker_e": "Restocker E",
    },
    # Overlapping coverage: most (source, destination) pairs have 2-3 candidates, so the
    # scheduler has a real choice to explain — and a real alternative when one drops out.
    # Runner C still uniquely spans everything, which makes them the natural recovery pick.
    worker_reachability={
        "worker_a": ["zone_1", "zone_3", "zone_4", "field"],
        "worker_b": ["zone_3", "zone_1", "zone_2", "field"],
        "worker_c": _ALL,
        "worker_d": ["zone_2", "zone_3", "zone_4", "field"],
        "worker_e": ["zone_4", "zone_2", "zone_1", "field"],
    },
    expected_roles=["the red item", "the blue item", "the scanner", "packing materials", "the bulk item"],
    goal_template=(
        "Fulfill expedited order 4471 at the {z2} and restock {z4}. "
        "Order 4471 needs the {c1} item and the {c2} item at the {z2}. "
        "Move the {c3} item to the {z4}. "
        "Packing cannot start until the {c4} item is at the {z2}."
    ),
    recommended_failure="Judge moves the scanner into Pick Aisle A.",
    expected_recovery="Pause only the packing chain; reassign scanner retrieval; picking and restock continue.",
    lexicon={
        **DEFAULT_LEXICON,
        "collective": "FLOOR TEAM",
        "worker": "Operator",
        "objective": "WORK ORDER",
        "object": "Item",
        "zone": "Station",
        "complete": "ORDER FULFILLED",
        "deviation": "FLOOR STATE DEVIATION",
    },
)

INCIDENT = Scenario(
    id="incident_stabilization",
    title="Incident Stabilization",
    subtitle="Disaster response under changing conditions",
    suggested_goal=(
        "Stabilize all three zones. Deliver the medical kit and the water supply to the medical "
        "station, restore communications using the radio and the battery at the comms station, "
        "and supply the shelter with the food."
    ),
    zone_labels=["Medical Station", "Emergency Shelter", "Comms Station", "Staging Area"],
    worker_roles={
        "worker_a": "Responder A",
        "worker_b": "Responder B",
        "worker_c": "Runner C",
        "worker_d": "Responder D",
        "worker_e": "Responder E",
    },
    worker_reachability={
        "worker_a": ["zone_1", "zone_3", "zone_4", "field"],
        "worker_b": ["zone_3", "zone_1", "zone_2", "field"],
        "worker_c": _ALL,
        "worker_d": ["zone_2", "zone_3", "zone_4", "field"],
        "worker_e": ["zone_4", "zone_2", "zone_1", "field"],
    },
    expected_roles=["the medical kit", "the water supply", "the radio", "the battery", "the food"],
    goal_template=(
        "Stabilize all zones. Deliver the {c1} supply and the {c2} supply to the {z1}. "
        "Move the {c3} unit to the {z3}. Supply the {z2} with the {c4} unit. "
        "Communications cannot be restored until the {c4} unit is at the {z3}."
    ),
    recommended_failure="Judge moves the battery out of the comms station.",
    expected_recovery="Freeze comms restoration only; reassign battery retrieval; medical and shelter continue.",
    lexicon={
        **DEFAULT_LEXICON,
        "collective": "RESPONDERS",
        "worker": "Responder",
        "objective": "INCIDENT OBJECTIVE",
        "object": "Resource",
        "zone": "Zone",
        "complete": "INCIDENT STABILIZED",
        "deviation": "WORLD STATE DEVIATION",
    },
)

CAMPUS = Scenario(
    id="campus_emergency",
    title="Campus Emergency",
    subtitle="Silent individual routing and accounting",
    suggested_goal=(
        "Evacuate wing A and wing B to the muster point avoiding the east corridor, "
        "hold the gymnasium in place until the route clears, and account for every group."
    ),
    zone_labels=["Wing A", "Wing B", "Gymnasium", "Muster Point"],
    worker_roles={
        "worker_a": "Teacher · Wing A",
        "worker_b": "Teacher · Wing B",
        "worker_c": "Facilities",
        "worker_d": "Front Office",
        "worker_e": "Coach · Gym",
    },
    worker_reachability={
        "worker_a": ["zone_1", "zone_4", "field"],
        "worker_b": ["zone_2", "zone_4", "field"],
        "worker_c": _ALL,
        "worker_d": ["zone_4", "zone_1", "zone_2", "field"],
        "worker_e": ["zone_3", "zone_4", "field"],
    },
    expected_roles=["group one", "group two", "group three", "group four", "support"],
    goal_template=("Evacuate the {c1} group and the {c2} group to the {z4}. "
                   "Hold the {c3} group in the {z3} until the route clears. "
                   "Account for the {c4} group."),
    recommended_failure="East corridor reported obstructed; affected routes must change.",
    expected_recovery="Reroute only the affected groups; others continue; account for the unreported group.",
    comms_profile="silent",
    lexicon={
        **DEFAULT_LEXICON,
        "collective": "STAFF",
        "worker": "Staff",
        "objective": "PROTOCOL",
        "object": "Group",
        "zone": "Wing",
        "complete": "ALL CLEAR",
        "deviation": "SITUATION CHANGE",
    },
)

SORT = Scenario(
    id="resource_sort",
    title="Distributed Sort",
    subtitle="Maximum parallelism",
    suggested_goal="Move every item into a different zone, one item per zone.",
    zone_labels=["Zone One", "Zone Two", "Zone Three", "Zone Four"],
    worker_roles={w: f"Operator {w[-1].upper()}" for w in WORKER_SEATS},
    worker_reachability={w: _ALL for w in WORKER_SEATS},
    expected_roles=[],
    goal_template=("Move the {c1} item to the {z1}, the {c2} item to the {z2}, "
                   "the {c3} item to the {z3}, and the {c4} item to the {z4}."),
    recommended_failure="Disable one worker mid-run.",
    expected_recovery="Reassign that worker's action; everything else continues.",
    lexicon=DEFAULT_LEXICON,
)

RELAY = Scenario(
    id="human_relay",
    title="Human Relay",
    subtitle="Strict serial dependency chain",
    suggested_goal="Pass the red item through every worker, then place it in zone four.",
    zone_labels=["Zone One", "Zone Two", "Zone Three", "Zone Four"],
    worker_roles={w: f"Operator {w[-1].upper()}" for w in WORKER_SEATS},
    worker_reachability={w: _ALL for w in WORKER_SEATS},
    expected_roles=["the red item"],
    recommended_failure="Disable a worker in the middle of the chain.",
    expected_recovery="Chain stalls at that link only, then reassigns and resumes.",
    lexicon=DEFAULT_LEXICON,
)

SCENARIOS: dict[str, Scenario] = {
    s.id: s for s in (WAREHOUSE, INCIDENT, CAMPUS, SORT, RELAY)
}
DEFAULT_SCENARIO = WAREHOUSE.id

"""Minimal scenario loader stub. Full scenario library described in docs/SCENARIOS.md
is Ojas's/Steven's scope; this stub exists only so state.reset() has somewhere to draw
zone/object defaults from until that lands."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models import Rect, WorldState, WorldMode, Zone


@dataclass
class Scenario:
    id: str
    title: str
    zone_labels: list[str] = field(default_factory=list)
    comms_profile: str = "voice"

    def build_world(self) -> WorldState:
        labels = self.zone_labels or ["Zone A", "Zone B", "Zone C", "Zone D"]
        zones = [
            Zone(
                id=f"zone_{i+1}",
                label=label,
                bounds=Rect(x=0.1 * i, y=0.1, w=0.2, h=0.3),
                status="pending",
                source="drawn",
            )
            for i, label in enumerate(labels)
        ]
        return WorldState(mode=WorldMode.simulation, zones=zones, objects=[])


SCENARIOS: dict[str, Scenario] = {
    "incident_stabilization": Scenario(
        id="incident_stabilization",
        title="Incident Stabilization",
        zone_labels=["Medical Station", "Emergency Shelter", "Comms Station", "Staging"],
    ),
    "warehouse_fulfillment": Scenario(
        id="warehouse_fulfillment",
        title="Warehouse Fulfillment",
        zone_labels=["Inbound Dock", "Pack Station", "Pick Aisle A", "Pick Aisle B"],
    ),
    "campus_emergency": Scenario(
        id="campus_emergency",
        title="Campus Emergency",
        zone_labels=["Wing A", "Wing B", "Gymnasium", "Muster Point"],
        comms_profile="silent",
    ),
}

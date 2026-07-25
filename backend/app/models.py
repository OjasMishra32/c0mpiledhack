"""Shared data model — the vision-relevant subset of docs/CONTRACTS.md.

This is Steven's slice of the shared spine (full HiveState/Worker/Action/Goal
models belong to Ojas's backend-core workstream and aren't implemented here).
Field names are snake_case to match frontend/src/types/hive.ts once it exists.
"""
from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field

WorldMode = Literal["live", "assisted", "simulation"]
ZoneStatus = Literal["unknown", "pending", "stabilizing", "stable", "critical"]
ZoneSource = Literal["detected", "drawn", "inferred"]
ObjectSource = Literal["vision", "simulation", "host_override"]


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


class Point(BaseModel):
    x: float
    y: float


class Rect(BaseModel):
    x: float
    y: float
    w: float
    h: float

    def contains(self, p: Point, margin: float = 0.0) -> bool:
        return (
            self.x - margin <= p.x <= self.x + self.w + margin
            and self.y - margin <= p.y <= self.y + self.h + margin
        )


class Descriptor(BaseModel):
    dominant_hsv: tuple[int, int, int]
    color_name: str
    color_hex: str
    area_norm: float
    aspect: float
    circularity: float
    shape_hint: Literal["round", "rectangular", "irregular"]


class ObservedObject(BaseModel):
    id: str
    descriptor: Descriptor
    position: Point
    zone: str = "field"
    visible: bool = True
    confidence: float = 0.0
    first_seen_at: str = Field(default_factory=now_iso)
    last_updated_at: str = Field(default_factory=now_iso)
    source: ObjectSource = "vision"

    semantic_label: str | None = None
    role: str | None = None
    role_confidence: float | None = None

    held_by: str | None = None
    stacked_on: str | None = None
    locked_by: str | None = None

    def display_label(self) -> str:
        if self.role:
            return self.role
        if self.semantic_label:
            return self.semantic_label
        return f"{self.descriptor.color_name} {self.descriptor.shape_hint} object"


class Zone(BaseModel):
    id: str
    label: str
    bounds: Rect
    occupancy: list[str] = Field(default_factory=list)
    status: ZoneStatus = "pending"
    source: ZoneSource = "drawn"


class Scene(BaseModel):
    objects: list[ObservedObject] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)
    scanned_at: str = Field(default_factory=now_iso)
    object_count: int = 0
    labeling_source: Literal["vlm", "descriptor", "none"] = "none"
    stable: bool = False


class WorldState(BaseModel):
    mode: WorldMode = "simulation"
    objects: list[ObservedObject] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)
    camera_online: bool = False
    vision_fps: float = 0.0
    last_frame_at: str | None = None


class Event(BaseModel):
    id: str
    seq: int
    timestamp: str = Field(default_factory=now_iso)
    type: str
    severity: Literal["debug", "info", "warn", "critical", "success"] = "info"
    actor: str = "vision"
    message: str
    metadata: dict = Field(default_factory=dict)

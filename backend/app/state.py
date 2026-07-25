"""Minimal single-source-of-truth state holder for the vision workstream.

The full HiveState (workers, actions, goals, websocket fan-out) is Ojas's
backend-core file (Ojas.md §4). This is a self-contained slice — just enough
state for the vision pipeline (camera -> discovery -> world model -> AR
overlay) to run and be tested standalone before backend-core exists.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from app.models import Event, Scene, WorldState


@dataclass
class HostOverride:
    object_id: str
    expires_at: float


class HiveState:
    def __init__(self) -> None:
        self.world = WorldState()
        self.scene = Scene()
        self.events: list[Event] = []
        self.host_overrides: dict[str, HostOverride] = {}
        self._seq = 0
        self._lock = asyncio.Lock()
        self._world_dirty = False

    def mark_world_dirty(self) -> None:
        self._world_dirty = True

    def consume_dirty(self) -> bool:
        was = self._world_dirty
        self._world_dirty = False
        return was

    async def emit(self, type: str, message: str, severity: str = "info",
                    actor: str = "vision", metadata: dict | None = None) -> Event:
        async with self._lock:
            self._seq += 1
            evt = Event(
                id=f"evt_{self._seq:06d}",
                seq=self._seq,
                type=type,
                severity=severity,
                actor=actor,
                message=message,
                metadata=metadata or {},
            )
            self.events.append(evt)
            self.events = self.events[-200:]
            return evt

    def emit_nowait(self, type: str, message: str, severity: str = "info",
                     actor: str = "vision", metadata: dict | None = None) -> None:
        """Fire-and-forget emit for hot paths (the vision tick) that aren't async."""
        self._seq += 1
        evt = Event(
            id=f"evt_{self._seq:06d}",
            seq=self._seq,
            type=type,
            severity=severity,
            actor=actor,
            message=message,
            metadata=metadata or {},
        )
        self.events.append(evt)
        self.events = self.events[-200:]

    def set_host_override(self, object_id: str, ttl_seconds: float = 20.0) -> None:
        self.host_overrides[object_id] = HostOverride(object_id, time.time() + ttl_seconds)

    def override_active(self, object_id: str) -> bool:
        ov = self.host_overrides.get(object_id)
        if ov is None:
            return False
        if time.time() >= ov.expires_at:
            del self.host_overrides[object_id]
            return False
        return True


state = HiveState()

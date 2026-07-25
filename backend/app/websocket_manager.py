"""WebSocket transport.

Two hard guarantees:
  1. A worker socket NEVER receives the plan, the goal, or another worker's instruction.
     If a judge opens devtools on a phone, all they may see is one instruction.
  2. A refresh reclaims the same slot via a localStorage token. Five slots exist from boot;
     workers are CLAIMED, never created.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any
from uuid import uuid4

from fastapi import WebSocket

from .models import Envelope, Worker, now_iso
from .state import state

log = logging.getLogger("hive.ws")


class SlotsFull(Exception):
    pass


def envelope(type: str, payload: Any, seq: int = 0) -> dict[str, Any]:
    return Envelope(type=type, payload=payload, ts=now_iso(), seq=seq).model_dump()


class WSManager:
    def __init__(self) -> None:
        self.host_sockets: set[WebSocket] = set()
        self.worker_sockets: dict[str, WebSocket] = {}
        self.token_map: dict[str, str] = {}
        self._world_dirty = False
        self._workers_dirty = False
        self._actions_dirty = False

    # ── dirty flags: the tick coalesces broadcasts, nothing pushes per-change ──

    def mark_world_dirty(self) -> None:
        self._world_dirty = True

    def mark_workers_dirty(self) -> None:
        self._workers_dirty = True

    def mark_actions_dirty(self) -> None:
        self._actions_dirty = True

    async def flush(self) -> None:
        if self._world_dirty:
            self._world_dirty = False
            await self.broadcast_host(
                "world_state_changed",
                {"world": state.world.model_dump(), "scene": state.scene.model_dump()},
            )
        if self._workers_dirty:
            self._workers_dirty = False
            await self.broadcast_host("workers_changed", [w.public_dict() for w in state.workers.values()])
        if self._actions_dirty:
            self._actions_dirty = False
            await self.broadcast_host(
                "actions_changed",
                {
                    "actions": [a.model_dump() for a in state.actions.values()],
                    "locks": state.locks,
                    "metrics": state.metrics.model_dump(),
                    "execution_status": state.execution_status,
                },
            )
        for ev in state.drain_pending_events():
            await self.broadcast("event", ev.model_dump())

    # ── connect ─────────────────────────────────────────────────────────────

    async def connect_host(self, sock: WebSocket) -> None:
        self.host_sockets.add(sock)
        log.info("host connected (%d total)", len(self.host_sockets))

    async def connect_worker(self, sock: WebSocket, token: str | None) -> Worker:
        # 1. Known token → same slot back. Refresh-safe.
        wid = self.token_map.get(token) if token else None

        if wid is None:
            # 2. First free slot: prefer never-claimed, then merely disconnected.
            wid = next(
                (w.id for w in state.workers.values() if not w.connected and w.session_token is None),
                None,
            ) or next((w.id for w in state.workers.values() if not w.connected), None)
            if wid is None:
                await sock.send_json(
                    envelope(
                        "error_event",
                        {"code": "hive_full", "message": "All five worker slots are occupied."},
                    )
                )
                raise SlotsFull()
            token = token or str(uuid4())
            self.token_map[token] = wid

        worker = state.workers[wid]

        # 3. Evict a stale socket on this slot (phone reconnected before the old one timed out).
        if wid in self.worker_sockets and self.worker_sockets[wid] is not sock:
            with suppress(Exception):
                await self.worker_sockets[wid].close()

        self.worker_sockets[wid] = sock
        worker.connected = True
        worker.session_token = token
        worker.last_seen_at = now_iso()
        if worker.status in ("disconnected", "joining"):
            worker.status = "ready"

        await self.send(wid, "worker_assigned", {"identity": worker.public_dict(), "token": token})
        self.mark_workers_dirty()
        await state.emit(
            "worker_joined",
            f"{worker.callsign} online — {worker.role}.",
            severity="success",
            actor=wid,
            worker_id=wid,
        )

        # 4. Re-deliver a live instruction so a refresh mid-action doesn't strand them.
        if worker.current_action_id:
            a = state.actions.get(worker.current_action_id)
            if a and a.instruction:
                await self.send(wid, "instruction_created", a.instruction.model_dump())
        return worker

    async def disconnect(self, sock: WebSocket) -> None:
        self.host_sockets.discard(sock)
        for wid, s in list(self.worker_sockets.items()):
            if s is sock:
                self.worker_sockets.pop(wid, None)
                w = state.workers.get(wid)
                if w:
                    # Do NOT mark unavailable here — Wi-Fi blips constantly. The tick decides
                    # after a grace period whether to reassign.
                    w.connected = False
                    w.last_seen_at = now_iso()
                    if w.status not in ("unavailable", "emergency"):
                        w.status = "disconnected"
                    self.mark_workers_dirty()
                    await state.emit(
                        "worker_disconnected",
                        f"{w.callsign} link lost. Holding assignment briefly before reassignment.",
                        severity="warn",
                        actor=wid,
                        worker_id=wid,
                    )
                break

    # ── send ────────────────────────────────────────────────────────────────

    async def send(self, worker_id: str, type: str, payload: Any) -> None:
        """Send to exactly ONE worker."""
        sock = self.worker_sockets.get(worker_id)
        if not sock:
            return
        try:
            await sock.send_json(envelope(type, payload))
        except Exception:
            log.debug("send to %s failed", worker_id)

    async def broadcast_host(self, type: str, payload: Any) -> None:
        dead = []
        msg = envelope(type, payload)
        for sock in list(self.host_sockets):
            try:
                await sock.send_json(msg)
            except Exception:
                dead.append(sock)
        for s in dead:
            self.host_sockets.discard(s)

    async def broadcast(self, type: str, payload: Any) -> None:
        """Host + all workers. Only for events safe for every audience."""
        await self.broadcast_host(type, payload)
        msg = envelope(type, payload)
        for wid, sock in list(self.worker_sockets.items()):
            try:
                await sock.send_json(msg)
            except Exception:
                self.worker_sockets.pop(wid, None)

    async def send_snapshot(self, sock: WebSocket, role: str, worker_id: str | None = None) -> None:
        if role == "worker":
            w = state.workers.get(worker_id or "")
            action = state.actions.get(w.current_action_id) if w and w.current_action_id else None
            payload = {
                "identity": w.public_dict() if w else None,
                "execution_status": state.execution_status,
                "comms_profile": state.scenario.comms_profile,
                "lexicon": state.scenario.lexicon,
                "instruction": action.instruction.model_dump() if action and action.instruction else None,
                "action_status": action.status if action else None,
            }
        else:
            payload = state.snapshot()
        with suppress(Exception):
            await sock.send_json(envelope("state_snapshot", payload))

    async def broadcast_snapshot(self) -> None:
        for sock in list(self.host_sockets):
            await self.send_snapshot(sock, "host")
        for wid, sock in list(self.worker_sockets.items()):
            await self.send_snapshot(sock, "worker", wid)


ws = WSManager()

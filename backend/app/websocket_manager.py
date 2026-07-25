"""One socket, two roles. See docs/CONTRACTS.md §3 and Ojas.md §5.

Workers receive ONLY their own `instruction_created` message — never the goal text,
the action list, or another worker's instruction. That is the entire "private
instructions" premise of the product; enforce it here on the server side.

`Worker.session_token` is declared `exclude=True` in models.py, so a plain
`.model_dump()` already never leaks it outward — no separate "public" serializer
needed."""

from __future__ import annotations

from contextlib import suppress
from uuid import uuid4

from fastapi import WebSocket

from app.models import WorkerStatus, utc_now


class SlotsFull(Exception):
    pass


def envelope(type: str, payload: dict, seq: int = 0) -> dict:
    return {"type": type, "payload": payload, "ts": utc_now().isoformat(), "seq": seq}


class WSManager:
    def __init__(self) -> None:
        self.host_sockets: set[WebSocket] = set()
        self.worker_sockets: dict[str, WebSocket] = {}
        self.token_map: dict[str, str] = {}

    async def connect_host(self, sock: WebSocket) -> None:
        self.host_sockets.add(sock)

    async def connect_worker(self, sock: WebSocket, token: str | None):
        from app.state import state

        if token and token in self.token_map:
            wid = self.token_map[token]
        else:
            wid = next(
                (w.id for w in state.workers if not w.connected and w.session_token is None),
                None,
            ) or next((w.id for w in state.workers if not w.connected), None)
            if wid is None:
                await sock.send_json(envelope("error_event", {
                    "code": "hive_full",
                    "message": "All five responder slots are occupied.",
                }))
                await sock.close()
                raise SlotsFull()
            token = token or str(uuid4())
            self.token_map[token] = wid

        worker = state.worker_by_id(wid)
        if wid in self.worker_sockets:
            with suppress(Exception):
                await self.worker_sockets[wid].close()
        self.worker_sockets[wid] = sock
        worker.connected = True
        worker.session_token = token
        if worker.status in (WorkerStatus.disconnected.value, WorkerStatus.joining.value):
            worker.status = WorkerStatus.ready.value

        await self.send(wid, "worker_assigned", {"identity": worker.model_dump(mode="json"), "token": token})
        await self.broadcast_host("workers_changed", [w.model_dump(mode="json") for w in state.workers])

        if worker.current_action_id:
            a = state.action_by_id(worker.current_action_id)
            if a and a.instruction:
                await self.send(wid, "instruction_created", a.instruction.model_dump(mode="json"))
        return worker

    async def disconnect(self, sock: WebSocket) -> None:
        from app.state import state

        self.host_sockets.discard(sock)
        for wid, s in list(self.worker_sockets.items()):
            if s is sock:
                del self.worker_sockets[wid]
                worker = state.worker_by_id(wid)
                if worker:
                    worker.connected = False
                    worker.last_seen_at = utc_now()

    async def send(self, worker_id: str, type: str, payload: dict) -> None:
        sock = self.worker_sockets.get(worker_id)
        if not sock:
            return
        with suppress(Exception):
            await sock.send_json(envelope(type, payload))

    async def broadcast(self, type: str, payload: dict) -> None:
        for sock in list(self.host_sockets) + list(self.worker_sockets.values()):
            with suppress(Exception):
                await sock.send_json(envelope(type, payload))

    async def broadcast_host(self, type: str, payload: dict) -> None:
        for sock in list(self.host_sockets):
            with suppress(Exception):
                await sock.send_json(envelope(type, payload))

    def _snapshot(self) -> dict:
        from app.state import state

        return {
            "mode": state.world.mode,
            "goal": state.goal.model_dump(mode="json") if state.goal else None,
            "actions": [a.model_dump(mode="json") for a in state.actions],
            "workers": [w.model_dump(mode="json") for w in state.workers],
            "world": state.world.model_dump(mode="json"),
            "scene": state.scene.model_dump(mode="json"),
            "execution_status": state.execution_status,
            "events": [e.model_dump(mode="json") for e in state.events[-200:]],
        }

    async def broadcast_snapshot(self) -> None:
        snapshot = self._snapshot()
        for sock in list(self.host_sockets):
            with suppress(Exception):
                await sock.send_json(envelope("state_snapshot", snapshot))

    async def send_snapshot(self, sock: WebSocket) -> None:
        with suppress(Exception):
            await sock.send_json(envelope("state_snapshot", self._snapshot()))


ws = WSManager()

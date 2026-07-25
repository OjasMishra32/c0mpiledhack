"""One socket, two roles. See docs/CONTRACTS.md §3 and Ojas.md §5.

Workers receive ONLY their own `instruction_created` message — never the goal text,
the action list, or another worker's instruction. That is the entire "private
instructions" premise of the product; enforce it here on the server side."""

from __future__ import annotations

from contextlib import suppress
from uuid import uuid4

from fastapi import WebSocket

from app.models import Worker, WorkerStatus


class SlotsFull(Exception):
    pass


def envelope(type: str, payload: dict, seq: int = 0) -> dict:
    from app.models import now_iso

    return {"type": type, "payload": payload, "ts": now_iso(), "seq": seq}


class WSManager:
    def __init__(self) -> None:
        self.host_sockets: set[WebSocket] = set()
        self.worker_sockets: dict[str, WebSocket] = {}
        self.token_map: dict[str, str] = {}

    async def connect_host(self, sock: WebSocket) -> None:
        self.host_sockets.add(sock)

    async def connect_worker(self, sock: WebSocket, token: str | None) -> Worker:
        from app.state import state

        if token and token in self.token_map:
            wid = self.token_map[token]
        else:
            wid = next(
                (w.id for w in state.workers.values() if not w.connected and w.session_token is None),
                None,
            ) or next((w.id for w in state.workers.values() if not w.connected), None)
            if wid is None:
                await sock.send_json(envelope("error_event", {
                    "code": "hive_full",
                    "message": "All five responder slots are occupied.",
                }))
                await sock.close()
                raise SlotsFull()
            token = token or str(uuid4())
            self.token_map[token] = wid

        worker = state.workers[wid]
        if wid in self.worker_sockets:
            with suppress(Exception):
                await self.worker_sockets[wid].close()
        self.worker_sockets[wid] = sock
        worker.connected = True
        worker.session_token = token
        if worker.status in (WorkerStatus.disconnected, WorkerStatus.joining):
            worker.status = WorkerStatus.ready

        await self.send(wid, "worker_assigned", {"identity": worker.public_dict(), "token": token})
        await self.broadcast_host("workers_changed", [w.public_dict() for w in state.workers.values()])

        if worker.current_action_id:
            a = state.actions.get(worker.current_action_id)
            if a and a.instruction:
                await self.send(wid, "instruction_created", a.instruction.model_dump())
        return worker

    async def disconnect(self, sock: WebSocket) -> None:
        from app.state import state

        self.host_sockets.discard(sock)
        for wid, s in list(self.worker_sockets.items()):
            if s is sock:
                del self.worker_sockets[wid]
                worker = state.workers.get(wid)
                if worker:
                    from app.models import now_iso

                    worker.connected = False
                    worker.last_seen_at = now_iso()

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

    async def broadcast_snapshot(self) -> None:
        from app.state import state

        snapshot = {
            "mode": state.mode,
            "goal": state.goal.model_dump() if state.goal else None,
            "actions": {k: v.model_dump() for k, v in state.actions.items()},
            "workers": [w.public_dict() for w in state.workers.values()],
            "world": state.world.model_dump(),
            "execution_status": state.execution_status,
            "events": [e.model_dump() for e in list(state.events)[-200:]],
        }
        for sock in list(self.host_sockets):
            with suppress(Exception):
                await sock.send_json(envelope("state_snapshot", snapshot))

    async def send_snapshot(self, sock: WebSocket) -> None:
        from app.state import state

        snapshot = {
            "mode": state.mode,
            "goal": state.goal.model_dump() if state.goal else None,
            "actions": {k: v.model_dump() for k, v in state.actions.items()},
            "workers": [w.public_dict() for w in state.workers.values()],
            "world": state.world.model_dump(),
            "execution_status": state.execution_status,
            "events": [e.model_dump() for e in list(state.events)[-200:]],
        }
        with suppress(Exception):
            await sock.send_json(envelope("state_snapshot", snapshot))


ws = WSManager()

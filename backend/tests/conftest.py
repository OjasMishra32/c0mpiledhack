from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import orchestrator  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import InboundMessage  # noqa: E402
from app.state import state as _state  # noqa: E402
from app.vision import world_model  # noqa: E402

# Tests must never depend on a network, a key, or a camera.
settings.nvidia_api_key = None
settings.nvidia_api_keys = None
settings.world_mode = "simulation"


@pytest_asyncio.fixture
async def state():
    await _state.reset("warehouse_fulfillment")
    for w in _state.workers.values():
        w.connected = True
        w.status = "ready"
        w.available = True
    yield _state


async def run_ticks(n: int = 1) -> None:
    for _ in range(n):
        await orchestrator.tick()


def send(worker_id: str, type: str, **payload) -> None:
    _state.inbox.append(
        InboundMessage(type=type, payload=payload, worker_id=worker_id, role="worker")
    )


def complete(action_id: str, *, physically: bool = True) -> None:
    """Simulate a worker doing the thing and tapping Completed."""
    a = _state.actions.get(action_id)
    if not a:
        return
    if physically and a.object_id and a.target_zone:
        world_model.set_object_zone(_state, a.object_id, a.target_zone)
    send(a.assigned_worker_id or "worker_a", "worker_completed", action_id=action_id)


async def drive_to_completion(max_ticks: int = 300) -> bool:
    """Tick, auto-completing anything dispatched. Capped so a bug fails instead of hanging."""
    for _ in range(max_ticks):
        for a in list(_state.actions.values()):
            if a.status in ("dispatched", "acknowledged", "executing"):
                complete(a.id)
        await run_ticks(2)
        if _state.goal and _state.goal.status == "completed":
            return True
    return False


@pytest.fixture
def helpers():
    class H:
        run_ticks = staticmethod(run_ticks)
        send = staticmethod(send)
        complete = staticmethod(complete)
        drive_to_completion = staticmethod(drive_to_completion)

    return H

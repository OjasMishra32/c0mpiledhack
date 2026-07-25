"""Voygr Callwright — voice escalation.

OWNER: Nikki. Working implementation — extend in place.

HIVE coordinates people reachable through the app. When a situation exceeds what the
collective can resolve, it escalates OUTSIDE the system by calling a human who is not in
it. Ships DISARMED: an accidental live call during setup dials a real person.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ..config import settings
from ..models import now_iso

log = logging.getLogger("hive.voygr")

COOLDOWN = 120.0
MAX_CALLS_PER_RUN = 3
_last_call_at = 0.0
_calls_this_run = 0


def reset_cooldown() -> None:
    global _last_call_at, _calls_this_run
    _last_call_at = 0.0
    _calls_this_run = 0


def build_brief(reason: str, state: Any) -> str:
    zones = ", ".join(f"{z.label}: {z.status}" for z in state.scene.zones) or "none"
    avail = sum(1 for w in state.workers.values() if w.connected and w.available)
    total = len(state.workers)
    offline = sum(1 for w in state.workers.values() if not w.connected)
    blocked = [a.description for a in state.actions.values() if a.status in ("blocked", "queued")][:4]
    missing = [o.display_label() for o in state.scene.objects if not o.visible]
    return f"""You are HIVE, an autonomous operations coordinator, placing an automated escalation
call. Be calm, concise and factual. Deliver the situation report, ask the recipient to confirm they
can respond, then confirm and end the call.

SITUATION REPORT
Site: {state.scenario.title}
Trigger: {reason}
Time: {now_iso()}
Zone status: {zones}
Workers: {avail} available of {total}; {offline} offline
Blocked operations: {"; ".join(blocked) or "none"}
Resources unaccounted for: {", ".join(missing) or "none"}
Attempted automatically: reassignment and recovery already exhausted.

ASK: confirm whether they can dispatch support within ten minutes. If yes, say support is logged and
end. If no, ask who to contact instead, then end. Do not speculate beyond this report. Do not give
instructions to anyone else."""


def should_escalate(state: Any) -> bool:
    if not state.escalation_armed or not settings.escalation_phone:
        return False
    if _calls_this_run >= MAX_CALLS_PER_RUN:
        return False
    if time.monotonic() - _last_call_at < COOLDOWN:
        return False
    return True


async def escalate(reason: str, state: Any, *, manual: bool = False) -> dict[str, Any]:
    global _last_call_at, _calls_this_run
    from ..websocket_manager import ws

    if not manual and not should_escalate(state):
        return {"ok": False, "skipped": True}

    brief = build_brief(reason, state)
    to = settings.escalation_phone
    _last_call_at = time.monotonic()
    _calls_this_run += 1

    if not to or not settings.callwright_api_key:
        record = {"call_id": f"sim_{int(time.time())}", "to": to or "unconfigured",
                  "status": "simulated", "reason": reason, "simulated": True}
        state.last_call = record
        await ws.broadcast_host("call_initiated", record)
        await state.emit(
            "call_initiated",
            f"Voice escalation prepared: {reason}",
            severity="warn",
            **record,
        )
        return {"ok": True, **record}

    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(
                f"{settings.callwright_base_url.rstrip('/')}/calls",
                headers={"X-API-Key": settings.callwright_api_key, "Content-Type": "application/json"},
                json={"to": to, "brief": brief, "metadata": {"source": "hive", "reason": reason}},
            )
        if r.status_code == 402:
            await state.emit(
                "call_failed",
                "Voice escalation unavailable: call credits exhausted.",
                severity="warn",
            )
            return {"ok": False, "error": "insufficient_credits"}
        r.raise_for_status()
        data = r.json()
        record = {
            "call_id": data.get("id") or data.get("call_id") or "unknown",
            "to": to,
            "status": data.get("status", "initiated"),
            "reason": reason,
            "simulated": False,
        }
        state.last_call = record
        await ws.broadcast_host("call_initiated", record)
        await state.emit("call_initiated", f"Voice escalation placed: {reason}", severity="critical", **record)
        return {"ok": True, **record}
    except Exception as e:
        log.warning("voygr call failed: %s", e)
        await state.emit(
            "call_failed", f"Voice escalation could not be placed ({type(e).__name__}).", severity="warn"
        )
        return {"ok": False, "error": str(e)}


async def usage() -> dict[str, Any]:
    if not settings.callwright_api_key:
        return {"configured": False}
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.get(
                f"{settings.callwright_base_url.rstrip('/')}/v1/usage",
                headers={"X-API-Key": settings.callwright_api_key},
            )
        if r.status_code >= 400:
            return {"configured": True, "error": f"HTTP {r.status_code}"}
        d = r.json()
        remaining = d.get("remaining")
        return {
            "configured": True,
            "remaining": remaining,
            "quota_limit": d.get("quota_limit"),
            "calls_left": int(remaining / 10) if isinstance(remaining, (int, float)) else None,
        }
    except Exception as e:
        return {"configured": True, "error": type(e).__name__}

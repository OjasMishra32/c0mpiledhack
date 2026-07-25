"""HIVE picks up the phone. See Nikki.md §5.

Escalation is a genuine capability gap in every coordination product — when a
situation exceeds what the collective can resolve, HIVE places a real voice call to
a human who is not in the app. It is an *enhancement*: nothing about the core loop
may block on it, and it ships disarmed so an accidental call never fires during
rehearsal."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from app.config import settings


@dataclass
class CallRecord:
    call_id: str
    status: str
    to: str | None = None
    simulated: bool = False
    transcript_excerpt: str | None = None

    @classmethod
    def failed(cls, reason: str) -> "CallRecord":
        return cls(call_id="", status=f"failed:{reason}")

    @classmethod
    def from_api(cls, data: dict) -> "CallRecord":
        return cls(call_id=data.get("id", ""), status=data.get("status", "initiated"), to=data.get("to"))


class CallwrightClient:
    def __init__(self) -> None:
        self.base = settings.callwright_base_url

    async def _simulate(self, to: str, brief: str, meta: dict) -> CallRecord:
        from app.state import state

        await state.emit(
            "call_initiated",
            f"Voice escalation (simulated): {brief.splitlines()[0] if brief else 'brief unavailable'}",
            severity="warn", metadata={"simulated": True, "to": to},
        )
        return CallRecord(
            call_id=f"sim_{datetime.now(timezone.utc).timestamp():.0f}",
            status="simulated", to=to, simulated=True,
        )

    async def place_call(self, to: str, brief: str, *, meta: dict) -> CallRecord:
        if not settings.callwright_api_key:
            return await self._simulate(to, brief, meta)

        from app.state import state

        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(
                    f"{self.base}/calls",
                    headers={"X-API-Key": settings.callwright_api_key},
                    json={"to": to, "brief": brief, "metadata": meta},
                )
                if r.status_code == 402:
                    await state.emit("call_failed", "Voice escalation unavailable: call credits exhausted.",
                                      severity="warn")
                    return CallRecord.failed("insufficient_credits")
                r.raise_for_status()
                return CallRecord.from_api(r.json())
        except httpx.TimeoutException:
            await state.emit("call_failed", "Voice escalation timed out.", severity="warn")
            return CallRecord.failed("timeout")
        except httpx.HTTPError as e:
            await state.emit("call_failed", f"Voice escalation failed: {e}", severity="warn")
            return CallRecord.failed("network_error")

    async def usage(self) -> dict:
        if not settings.callwright_api_key:
            return {"credits_remaining": None}
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{self.base}/v1/usage", headers={"X-API-Key": settings.callwright_api_key})
            return r.json()


client = CallwrightClient()


def build_brief(trigger, state) -> str:
    """Everything goes in the brief — generated from structured state so it's accurate
    and never hallucinated."""
    blocked = [a for a in state.actions if a.status == "blocked"]
    missing = [o for o in state.scene.objects if o.confidence < 0.25 and not o.held_by]
    n_available = sum(1 for w in state.workers if w.available and w.connected)
    n_total = len(state.workers)
    return f"""You are HIVE, an autonomous operations coordinator, placing an automated
escalation call. Be calm, concise, and factual. Deliver the situation report, then ask
the recipient to confirm they can respond, then confirm and end the call.

SITUATION REPORT
Site: {state.scenario_id}
Trigger: {trigger.human_readable or trigger.kind}
Zone status: {", ".join(f"{z.label}: {z.status}" for z in state.scene.zones) or "none"}
Responders: {n_available} available of {n_total}
Blocked operations: {", ".join(a.description for a in blocked) or "none"}
Resources unaccounted for: {", ".join(o.display_label() for o in missing) or "none"}
Attempted automatically: {trigger.attempted_summary or "deterministic recovery"}

ASK: confirm whether they can dispatch support within 10 minutes.
If yes, say support is logged and end. If no, ask who to contact instead, then end.
Do not speculate beyond this report. Do not give instructions to anyone else."""


@dataclass
class EscalationDecision:
    reason: str
    to: str


_MAX_CALLS_PER_RUN = 3
_COOLDOWN_SECONDS = 120
_call_history: list[datetime] = []


def _cooldown_active() -> bool:
    if not _call_history:
        return False
    return (datetime.now(timezone.utc) - _call_history[-1]).total_seconds() < _COOLDOWN_SECONDS


def reset_escalation_state() -> None:
    """Call from state.reset() so a fresh run doesn't inherit the previous run's cap."""
    _call_history.clear()


def should_escalate(state, trigger=None) -> EscalationDecision | None:
    """Hard gate. Triggers, in priority order: worker_emergency (immediate), a zone
    critical >20s with zero viable responders, an unresolved scheduler_deadlock, or a
    manual host escalation (handled by the caller, always allowed). Ships disarmed:
    `state.escalation_armed` defaults False and must be armed from Advanced Controls."""
    if not settings.escalation_phone:
        return None
    if len(_call_history) >= _MAX_CALLS_PER_RUN:
        return None
    if _cooldown_active():
        return None
    if not state.escalation_armed:
        return None

    if trigger and trigger.kind == "worker_emergency":
        return EscalationDecision(reason="worker_emergency", to=settings.escalation_phone)
    critical_zones = [z for z in state.scene.zones if z.status == "critical"]
    if critical_zones and not any(w.available for w in state.workers):
        return EscalationDecision(reason="zone_critical_no_responder", to=settings.escalation_phone)
    if trigger and trigger.kind == "scheduler_deadlock":
        return EscalationDecision(reason="scheduler_deadlock", to=settings.escalation_phone)
    return None


async def escalate(trigger, state) -> CallRecord | None:
    decision = should_escalate(state, trigger)
    if not decision:
        return None
    brief = build_brief(trigger, state)
    record = await client.place_call(decision.to, brief, meta={"reason": decision.reason})
    _call_history.append(datetime.now(timezone.utc))
    await state.emit("call_initiated", f"Voice escalation: {decision.reason}", severity="warn",
                      metadata={"call_id": record.call_id, "to": decision.to})
    return record

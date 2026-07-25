import httpx
import pytest

from app.config import settings
from app.integrations import voygr
from app.recovery import DeviationTrigger


@pytest.mark.asyncio
async def test_voygr_no_key_simulates(hive_state, monkeypatch):
    monkeypatch.setattr(settings, "callwright_api_key", None)
    record = await voygr.client.place_call("+15551234567", "brief text", meta={})
    assert record.simulated is True
    assert record.status == "simulated"


@pytest.mark.asyncio
async def test_voygr_402(hive_state, monkeypatch):
    monkeypatch.setattr(settings, "callwright_api_key", "fake-key")

    class FakeResponse:
        status_code = 402

    async def fake_post(self, url, headers=None, json=None):
        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    record = await voygr.client.place_call("+15551234567", "brief text", meta={})
    assert record.status == "failed:insufficient_credits"


@pytest.mark.asyncio
async def test_voygr_timeout_does_not_raise(hive_state, monkeypatch):
    monkeypatch.setattr(settings, "callwright_api_key", "fake-key")

    async def fake_post(self, url, headers=None, json=None):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    record = await voygr.client.place_call("+15551234567", "brief text", meta={})
    assert record.status == "failed:timeout"


def test_escalation_disarmed(hive_state, monkeypatch):
    monkeypatch.setattr(settings, "escalation_phone", "+15551234567")
    hive_state.escalation_armed = False
    trigger = DeviationTrigger(kind="worker_emergency")
    assert voygr.should_escalate(hive_state, trigger) is None


def test_escalation_armed_emergency(hive_state, monkeypatch):
    monkeypatch.setattr(settings, "escalation_phone", "+15551234567")
    hive_state.escalation_armed = True
    trigger = DeviationTrigger(kind="worker_emergency")
    decision = voygr.should_escalate(hive_state, trigger)
    assert decision is not None
    assert decision.reason == "worker_emergency"


def test_escalation_no_phone_blocks(hive_state):
    hive_state.escalation_armed = True
    trigger = DeviationTrigger(kind="worker_emergency")
    assert voygr.should_escalate(hive_state, trigger) is None


def test_escalation_cooldown(hive_state, monkeypatch):
    monkeypatch.setattr(settings, "escalation_phone", "+15551234567")
    hive_state.escalation_armed = True
    from datetime import datetime, timezone

    voygr._call_history.append(datetime.now(timezone.utc))
    trigger = DeviationTrigger(kind="worker_emergency")
    assert voygr.should_escalate(hive_state, trigger) is None

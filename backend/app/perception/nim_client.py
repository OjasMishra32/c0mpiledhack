"""NVIDIA NIM client — the single model gateway for all of HIVE.

Every model call in the project goes through here: perception scene reads, deviation
adjudication, planning, and grounding. That means one place owns key pooling, rate
pacing, 429 failover, timeouts, and health reporting.

Hard rules:
  * Never called from inside the orchestrator tick. Callers own an asyncio.Task.
  * Every call has a timeout and a caller-side fallback. A dead endpoint degrades
    the intelligence; it never stalls coordination.
  * Single in-flight request per logical stream — callers drop work rather than queue.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from typing import Any

import httpx

from ..config import settings
from ..key_pool import get_pool

log = logging.getLogger("hive.nim")

_TIER_HEALTH: dict[str, bool] = {}
_LAST_ERROR: dict[str, str] = {}


def pool():
    return get_pool(settings.nim_keys, settings.nim_per_key_rpm, settings.nim_key_strategy)


def health() -> dict[str, Any]:
    p = pool()
    return {
        "configured": bool(p),
        "pool": p.stats() if p else None,
        "tiers": dict(_TIER_HEALTH),
        "errors": dict(_LAST_ERROR),
    }


def _extract_json(text: str) -> dict[str, Any] | None:
    """Strip fences, take the outermost object, parse. Models fence JSON constantly."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    start, depth = t.find("{"), 0
    if start < 0:
        return None
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start : i + 1])
                except Exception:
                    return None
    return None


def encode_jpeg(jpeg: bytes) -> str:
    return base64.b64encode(jpeg).decode()


async def chat(
    model: str,
    messages: list[dict[str, Any]],
    *,
    tier: str = "default",
    affinity: str | None = None,
    timeout: float = 12.0,
    max_tokens: int = 1200,
    temperature: float = 0.2,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
    response_json: bool = False,
    max_attempts: int = 3,
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """One chat completion. Returns the raw response dict, or None on failure.

    Never raises. Failure is a normal, expected outcome that callers handle by
    falling back to deterministic behaviour.
    """
    p = pool()
    if p is None:
        return None

    affinity = affinity or tier
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
    if response_json and not tools:
        body["response_format"] = {"type": "json_object"}
    if extra_body:
        # e.g. {"chat_template_kwargs": {"enable_thinking": False}} — reasoning-mode
        # models are ~20x slower with thinking on; the live path always turns it off.
        body.update(extra_body)

    url = f"{settings.nim_base_url.rstrip('/')}/chat/completions"
    started = time.monotonic()

    for attempt in range(max_attempts):
        lease = await p.lease(affinity=affinity)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {lease.key}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
            if r.status_code == 429 or r.status_code >= 500:
                # Penalize THIS key and immediately re-lease — another key almost
                # certainly has room. This is what turns intermittent drops into zero.
                await p.penalize(lease.index)
                _LAST_ERROR[tier] = f"HTTP {r.status_code}"
                if attempt == max_attempts - 1:
                    break
                continue
            if r.status_code >= 400:
                _LAST_ERROR[tier] = f"HTTP {r.status_code}: {r.text[:160]}"
                _TIER_HEALTH[tier] = False
                log.warning("nim %s -> %s %s", tier, r.status_code, r.text[:200])
                return None

            await p.note_success(lease.index)
            _TIER_HEALTH[tier] = True
            _LAST_ERROR.pop(tier, None)
            data = r.json()
            data["_latency"] = round(time.monotonic() - started, 2)
            data["_key"] = lease.masked
            return data

        except (httpx.TimeoutException, asyncio.TimeoutError):
            # Do NOT penalize the key: a slow model is not an unhealthy key, and cooling
            # a good key here would silently shrink capacity for the rest of the run.
            _LAST_ERROR[tier] = "timeout"
            if attempt == max_attempts - 1:
                break
        except Exception as e:  # network, DNS, TLS…
            _LAST_ERROR[tier] = type(e).__name__
            await p.penalize(lease.index)
            if attempt == max_attempts - 1:
                break

    _TIER_HEALTH[tier] = False
    return None


def content_of(resp: dict[str, Any] | None) -> str:
    if not resp:
        return ""
    try:
        return resp["choices"][0]["message"].get("content") or ""
    except Exception:
        return ""


def tool_args_of(resp: dict[str, Any] | None, name: str | None = None) -> dict[str, Any] | None:
    if not resp:
        return None
    try:
        calls = resp["choices"][0]["message"].get("tool_calls") or []
        for c in calls:
            fn = c.get("function", {})
            if name is None or fn.get("name") == name:
                args = fn.get("arguments")
                return json.loads(args) if isinstance(args, str) else args
    except Exception:
        return None
    return None


async def json_chat(
    model: str,
    prompt: str,
    *,
    jpeg: bytes | None = None,
    system: str | None = None,
    **kw: Any,
) -> dict[str, Any] | None:
    """Convenience: prompt (+ optional image) in, parsed JSON out. None on any failure."""
    content: Any = prompt
    if jpeg is not None:
        content = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{encode_jpeg(jpeg)}"},
            },
        ]
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})

    kw.setdefault("response_json", True)
    resp = await chat(model, messages, **kw)
    parsed = _extract_json(content_of(resp))
    if parsed is not None and resp:
        parsed["_latency"] = resp.get("_latency")
    return parsed

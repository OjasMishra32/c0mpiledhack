"""Event-triggered physical reasoning.

The architecture claim, stated precisely:

    HIVE combines high-frequency machine perception with event-triggered physical
    reasoning. Computer vision continuously maintains the world state; a physical-AI
    model investigates ambiguity, verifies complex transitions, and explains deviations.

We do NOT stream the webcam into a VLM. OpenCV holds the world state at 10-20 Hz. The
reasoner is invoked only when something meaningful happens, and it receives a BURST of
4-8 recent frames — because almost every question we ask is about change, and a single
still cannot answer "did it move or was it occluded?".

The reasoner produces OBSERVATIONS. It never controls workers. A slow or malformed model
response degrades explanation quality and nothing else — the deterministic scheduler and
recovery engine own all control.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import settings
from . import nim_client

log = logging.getLogger("hive.analyzer")

# Preference order for the physical-reasoning role. The first reachable model wins.
# Cosmos is purpose-built for physical/temporal reasoning; the others are capable
# general VLMs that keep the feature alive when it is not provisioned.
REASONER_CANDIDATES = [
    "nvidia/cosmos-reason2-8b",
    "nvidia/cosmos3-nano-reasoner",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    "nvidia/nemotron-nano-12b-v2-vl",
    "meta/llama-3.2-11b-vision-instruct",
]

# Reasoning-mode models are slow with thinking on (~10s) and fast with it off (<1s).
# The live path always disables it; deep recovery analysis may re-enable it.
NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}
THINKY = {"nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"}


@dataclass
class Analysis:
    ok: bool
    kind: str = ""
    agrees: bool | None = None
    confidence: float = 0.0
    what_happened: str = ""
    recommended: str = "proceed"  # proceed | replan | ask_operator
    raw: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    latency: float = 0.0


class Analyzer:
    def __init__(self) -> None:
        self.reasoner: str | None = None
        self.available: list[str] = []
        self.probed = False
        self._inflight = False
        self._last_call = 0.0
        self.last: Analysis | None = None
        self.calls = 0

    # ── availability ────────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return settings.vlm_enabled and settings.has_model_access and self.reasoner is not None

    async def probe(self) -> None:
        """Find the best reachable reasoner once at startup.

        Free hosted endpoints vary by account: a model can be in the catalog but return
        404 'not found for account'. Probing means the stack self-configures instead of
        failing on a hardcoded id, and it upgrades automatically the moment access lands.
        """
        self.probed = True
        if not settings.has_model_access:
            log.info("no model keys configured — perception reasoning disabled")
            return

        preferred = settings.vlm_reason_model
        order = [preferred] + [m for m in REASONER_CANDIDATES if m != preferred]

        async def reachable(model: str) -> bool:
            extra = NO_THINK if model in THINKY else {}
            r = await nim_client.chat(
                model,
                [{"role": "user", "content": "Reply with: ok"}],
                tier=f"probe:{model}",
                timeout=12.0,
                max_tokens=8,
                max_attempts=1,
                extra_body=extra,
            )
            return bool(r)

        for model in order:
            try:
                if await reachable(model):
                    self.available.append(model)
                    if self.reasoner is None:
                        self.reasoner = model
                        log.info("physical reasoner: %s", model)
                    break
            except Exception:
                continue

        if self.reasoner is None:
            log.warning("no reachable vision model — running on computer vision alone")

    # ── gating: the reason this stays fast and cheap ────────────────────────

    def should_analyze(self, *, force: bool = False) -> bool:
        if not self.enabled or self._inflight:
            return False
        if force:
            return True
        return (time.monotonic() - self._last_call) >= settings.vlm_min_interval

    # ── the reasoning burst ─────────────────────────────────────────────────

    async def analyze_deviation(
        self, expected: str, observed: str, frames: list[bytes], context: str = ""
    ) -> Analysis:
        """Adjudicate a deviation candidate before we put a red banner on the projector.

        Never ask "what is happening?". Ask a closed question with a schema.
        """
        prompt = f"""These frames come from a fixed overhead camera watching a work surface, in time order.

EXPECTED STATE: {expected}
A tracker reports this instead: {observed}
{context}

Decide whether the tracker is correct. Distinguish between: the item was genuinely moved,
the item is temporarily occluded by a hand or body, or the tracker misclassified it.

Return STRICT JSON only, no prose:
{{"agrees": <true if the item genuinely moved as reported>,
  "confidence": <0-1>,
  "what_actually_happened": "<one plain sentence>",
  "recommended": "proceed" | "replan" | "ask_operator"}}"""
        return await self._run(prompt, frames, kind="deviation")

    async def ask(self, question: str, frames: list[bytes], world_summary: str = "") -> Analysis:
        """Operator Q&A over the live feed. The clearest possible proof this is real."""
        prompt = f"""These frames come from a fixed camera watching a work surface, in time order.

{f"Known state: {world_summary}" if world_summary else ""}

Question: {question}

Answer only from what is visible. If you cannot tell, say so.
Return STRICT JSON only: {{"answer": "<two sentences max>", "confidence": <0-1>}}"""
        a = await self._run(prompt, frames, kind="question")
        if a.ok:
            a.what_happened = a.raw.get("answer", a.what_happened)
        return a

    async def describe(self, frames: list[bytes]) -> Analysis:
        """Semantic labelling / general scene read. Used on scan, not on a timer."""
        prompt = """Look at this work surface. Report only what you can see.

Return STRICT JSON only:
{"objects":[{"ref":"<2-4 word description e.g. 'red plastic cup'>",
             "color":"<basic colour word>",
             "position":{"x":<0-1 left to right>,"y":<0-1 top to bottom>},
             "held":<true if a hand is touching it>,
             "on_top_of":"<ref of the object beneath it, or null>"}],
 "people":[{"side":"north|south|east|west","reaching":<bool>}],
 "activity":"<one short sentence, or 'no activity'>",
 "anomalies":["<anything unexpected: knocked over, obstructed, new item>"]}"""
        return await self._run(prompt, frames, kind="describe")

    # ── plumbing ────────────────────────────────────────────────────────────

    async def _run(self, prompt: str, frames: list[bytes], *, kind: str) -> Analysis:
        if not self.enabled or not frames:
            return Analysis(ok=False, kind=kind)
        if self._inflight:
            return Analysis(ok=False, kind=kind)  # drop, never queue

        self._inflight = True
        self._last_call = time.monotonic()
        self.calls += 1
        try:
            for model in [self.reasoner] + [m for m in self.available if m != self.reasoner]:
                if not model:
                    continue
                content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
                for f in frames:
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{nim_client.encode_jpeg(f)}"},
                        }
                    )
                resp = await nim_client.chat(
                    model,
                    [{"role": "user", "content": content}],
                    tier="reason",
                    affinity="reason",
                    timeout=settings.vlm_reason_timeout,
                    max_tokens=400,
                    temperature=0.1,
                    max_attempts=2,
                    extra_body=NO_THINK if model in THINKY else None,
                )
                data = nim_client._extract_json(nim_client.content_of(resp))
                if data is None:
                    continue
                a = Analysis(
                    ok=True,
                    kind=kind,
                    agrees=data.get("agrees"),
                    confidence=float(data.get("confidence") or 0.0),
                    what_happened=str(
                        data.get("what_actually_happened") or data.get("activity") or data.get("answer") or ""
                    ),
                    recommended=str(data.get("recommended") or "proceed"),
                    raw=data,
                    model=model,
                    latency=float(resp.get("_latency") or 0.0) if resp else 0.0,
                )
                self.last = a
                return a
            return Analysis(ok=False, kind=kind)
        except Exception:
            log.exception("analyzer")
            return Analysis(ok=False, kind=kind)
        finally:
            self._inflight = False

    def health(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "reasoner": self.reasoner,
            "available": self.available,
            "calls": self.calls,
            "inflight": self._inflight,
            "last": self.last.what_happened if self.last else None,
        }


analyzer = Analyzer()

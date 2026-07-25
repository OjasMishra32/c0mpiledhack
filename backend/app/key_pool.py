"""Key pool — interchangeable API keys for one endpoint, with per-key rate windows,
sticky affinity, and adaptive 429 backoff.

WHY THIS IS SAFE: OpenAI-style chat/completions are STATELESS. The full conversation
travels in `messages` on every request, so which key carries a given request is
irrelevant to correctness — rotating keys can never drop or corrupt context.

What key choice DOES affect:
  (a) which key's RPM budget you spend, and
  (b) server-side prompt-cache locality — many gateways cache the prompt prefix per
      key/tenant, so reusing the same key as the previous request gets a warm cache
      (cheaper, lower time-to-first-token) while a cold key pays full prefill.

That is why the default strategy is NOT pure round-robin. Round-robin maximizes raw
throughput on tiny identical payloads but makes every request a cold-cache miss on a
real growing prompt. `sticky_least_loaded` keeps one logical stream (an `affinity`
token, e.g. "vlm_fast") on ONE key while that key has headroom, and only spills to the
least-loaded other key under pressure or after a 429. Concurrent workloads — parallel
perception reads, a planner call during a scene read — naturally fan out because each
grabs the least-loaded key.

Honest note: a single sequential chain of calls is bottlenecked by its own dependencies,
not by RPM, so more keys do NOT make one chain faster. Their payoff is (1) never getting
throttled mid-run during a burst, (2) aggregate throughput under concurrency, and
(3) failover when a key dies.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("hive.keypool")

STRATEGY_STICKY = "sticky_least_loaded"
STRATEGY_ROUND_ROBIN = "round_robin"


@dataclass
class Lease:
    key: str
    index: int
    masked: str


@dataclass
class KeyPool:
    """A pool of interchangeable keys for the same endpoint."""

    keys: list[str]
    per_key_rpm: int = 38  # just under a typical free-tier 40 RPM, leaving headroom
    strategy: str = STRATEGY_STICKY

    _windows: list[list[float]] = field(default_factory=list, init=False)
    _cooldown_until: list[float] = field(default_factory=list, init=False)
    _consecutive_429: list[int] = field(default_factory=list, init=False)
    _affinity: dict[str, int] = field(default_factory=dict, init=False)
    _rr_cursor: int = field(default=0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    BASE_COOLDOWN = 1.5
    MAX_COOLDOWN = 45.0

    def __post_init__(self) -> None:
        # De-dupe and trim: a pasted list with blank lines or repeats must not skew the
        # budget — two identical keys share ONE real server-side limit.
        seen: set[str] = set()
        clean = []
        for k in self.keys:
            k = (k or "").strip()
            if k and k not in seen:
                seen.add(k)
                clean.append(k)
        self.keys = clean
        self.per_key_rpm = max(1, self.per_key_rpm)
        n = len(self.keys)
        self._windows = [[] for _ in range(n)]
        self._cooldown_until = [0.0] * n
        self._consecutive_429 = [0] * n

    # ── introspection ───────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        return len(self.keys)

    @property
    def aggregate_rpm(self) -> int:
        return self.count * self.per_key_rpm

    @staticmethod
    def mask(key: str) -> str:
        return f"…{key[-4:]}" if len(key) > 8 else "key"

    def stats(self) -> dict:
        now = time.monotonic()
        return {
            "keys": self.count,
            "per_key_rpm": self.per_key_rpm,
            "aggregate_rpm": self.aggregate_rpm,
            "strategy": self.strategy,
            "in_window": [len([t for t in w if now - t < 60]) for w in self._windows],
            "cooling": [max(0.0, round(c - now, 1)) for c in self._cooldown_until],
        }

    # ── leasing ─────────────────────────────────────────────────────────────

    async def lease(self, affinity: str | None = None, *, max_wait: float = 20.0) -> Lease:
        """Lease a key with headroom now, waiting only if EVERY key is saturated.

        Records the slot before returning, so the caller must actually send the request.
        """
        if not self.keys:
            raise RuntimeError("key pool is empty")
        deadline = time.monotonic() + max_wait
        while True:
            async with self._lock:
                now = time.monotonic()
                self._prune(now)
                idx = self._pick(now, affinity)
                if idx is not None:
                    self._windows[idx].append(now)
                    if affinity:
                        self._affinity[affinity] = idx
                    return Lease(key=self.keys[idx], index=idx, masked=self.mask(self.keys[idx]))
                wait = self._min_wait_until_free(now)
            if time.monotonic() + wait > deadline:
                # Rather than hang a live demo, hand back the least-cooling key and let
                # the caller's own timeout govern. Better a slow request than a stall.
                async with self._lock:
                    idx = min(range(self.count), key=lambda i: self._cooldown_until[i])
                    self._windows[idx].append(time.monotonic())
                    return Lease(key=self.keys[idx], index=idx, masked=self.mask(self.keys[idx]))
            await asyncio.sleep(min(max(wait, 0.05), 5.0))

    async def penalize(self, index: int) -> None:
        """Server 429'd this key: exponential cooldown (1.5s, 3s, 6s… capped) so lease
        skips it and the next attempt lands elsewhere instead of re-hammering a hot key."""
        async with self._lock:
            if not (0 <= index < self.count):
                return
            n = self._consecutive_429[index]
            self._consecutive_429[index] = min(n + 1, 8)
            backoff = min(self.BASE_COOLDOWN * (2**n), self.MAX_COOLDOWN)
            self._cooldown_until[index] = time.monotonic() + backoff
            log.warning("key %s rate limited — cooling %.1fs", self.mask(self.keys[index]), backoff)

    async def note_success(self, index: int) -> None:
        """A clean success clears the 429 streak so the key re-enters at full strength."""
        async with self._lock:
            if 0 <= index < self.count:
                self._consecutive_429[index] = 0
                self._cooldown_until[index] = 0.0

    # ── core ────────────────────────────────────────────────────────────────

    def _prune(self, now: float) -> None:
        for w in self._windows:
            w[:] = [t for t in w if now - t < 60]

    def _has_room(self, i: int, now: float) -> bool:
        return len(self._windows[i]) < self.per_key_rpm and now >= self._cooldown_until[i]

    def _pick(self, now: float, affinity: str | None) -> int | None:
        if self.strategy == STRATEGY_ROUND_ROBIN:
            for _ in range(self.count):
                i = self._rr_cursor % self.count
                self._rr_cursor += 1
                if self._has_room(i, now):
                    return i
            return None

        # Sticky: reuse the affinity key while it still has headroom → warm cache.
        if affinity is not None:
            last = self._affinity.get(affinity)
            if last is not None and self._has_room(last, now):
                return last

        # Least-loaded: fewest in-window requests wins; ties break to lowest index.
        best: int | None = None
        for i in range(self.count):
            if not self._has_room(i, now):
                continue
            if best is None or len(self._windows[i]) < len(self._windows[best]):
                best = i
        return best

    def _min_wait_until_free(self, now: float) -> float:
        """For each key, when does it next become pickable — the later of 'a window slot
        frees' and 'its cooldown clears'? Wait for the soonest across all keys."""
        best = 60.0
        for i in range(self.count):
            if len(self._windows[i]) < self.per_key_rpm:
                window_free = 0.0
            else:
                oldest = self._windows[i][0] if self._windows[i] else now
                window_free = 60 - (now - oldest) + 0.05
            cooldown_free = max(0.0, self._cooldown_until[i] - now)
            best = min(best, max(window_free, cooldown_free))
        return max(best, 0.05)


_pool: KeyPool | None = None
_signature = ""


def get_pool(keys: list[str], per_key_rpm: int = 38, strategy: str = STRATEGY_STICKY) -> KeyPool | None:
    """Process-wide shared pool, so EVERY request against these keys draws from ONE set of
    per-key windows. A fresh pool per caller would let concurrent workloads double-spend the
    same key's budget. Rebuilds only when the key set or config actually changes."""
    global _pool, _signature
    keys = [k.strip() for k in keys if k and k.strip()]
    if not keys:
        return None
    sig = "|".join(keys) + f"#{per_key_rpm}#{strategy}"
    if sig != _signature or _pool is None:
        _pool = KeyPool(keys=keys, per_key_rpm=per_key_rpm, strategy=strategy)
        _signature = sig
        log.info("key pool: %d key(s), ~%d RPM aggregate, %s", _pool.count, _pool.aggregate_rpm, strategy)
    return _pool

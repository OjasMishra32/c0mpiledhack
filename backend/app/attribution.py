"""Attribution — who actually did what, and how well.

The scheduler answers "who *can* do this?" from capability, reachability and load. That is
necessary and not sufficient: two workers can both be able, and still be very different
choices. This module answers "who *should* do this?" from what has actually happened in
this run.

Everything here is observed, not configured. Nobody is rated ahead of time; HIVE builds
the picture from the run itself and can defend every number it uses:

  * reliability — when this worker said "done", did the world agree?
  * speed       — how long from dispatch to verified, relative to everyone else
  * familiarity — have they successfully worked this zone / this item before?
  * recency     — how long since they last carried something (fairness, and it keeps
                  five people visibly involved rather than two)

The output is a small adjustment applied on top of the scheduler's score, plus a phrase
for the assignment explanation. Both are deliberately modest: capability decides who is
eligible, evidence only breaks ties. A worker who fails once should slide down the list,
not be exiled from the demo.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

# Weights are small on purpose — this nudges an already-valid choice, it never overrides
# a hard filter. Lower total is better, matching the scheduler's convention.
W_RELIABILITY = 1.6
W_SPEED = 0.7
W_FAMILIARITY = 0.6
W_RECENCY = 0.4

FAST_ENOUGH = 0.85  # ratio of own mean duration to the collective mean


@dataclass
class WorkerRecord:
    worker_id: str
    completed: int = 0
    failed: int = 0
    reassigned_away: int = 0
    claims: int = 0  # times they reported "done"
    claims_upheld: int = 0  # …and the world agreed
    durations: list[float] = field(default_factory=list)
    zones: dict[str, int] = field(default_factory=dict)
    objects: dict[str, int] = field(default_factory=dict)
    last_finished_at: float | None = None

    @property
    def reliability(self) -> float:
        """Fraction of self-reports that survived verification. Unknown → assume good."""
        if self.claims == 0:
            return 1.0
        return self.claims_upheld / self.claims

    @property
    def mean_duration(self) -> float | None:
        if not self.durations:
            return None
        recent = self.durations[-5:]
        return sum(recent) / len(recent)

    def summary(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "completed": self.completed,
            "failed": self.failed,
            "reliability": round(self.reliability, 2),
            "mean_seconds": round(self.mean_duration, 1) if self.mean_duration else None,
            "zones": dict(sorted(self.zones.items(), key=lambda kv: -kv[1])),
        }


class Attribution:
    def __init__(self) -> None:
        self.records: dict[str, WorkerRecord] = {}

    def reset(self) -> None:
        self.records.clear()

    def record(self, worker_id: str) -> WorkerRecord:
        return self.records.setdefault(worker_id, WorkerRecord(worker_id=worker_id))

    # ── observations ────────────────────────────────────────────────────────

    def note_claim(self, worker_id: str | None) -> None:
        if worker_id:
            self.record(worker_id).claims += 1

    def note_verified(self, action: Any, state: Any) -> None:
        wid = action.assigned_worker_id
        if not wid:
            return
        r = self.record(wid)
        r.completed += 1
        r.last_finished_at = time.monotonic()
        # A claim that verification upheld is the strongest signal we get about a person.
        if any(e.kind == "worker_report" for e in action.evidence):
            r.claims_upheld += 1
        if action.dispatched_at:
            secs = _elapsed(action.dispatched_at)
            if 0 < secs < 600:
                r.durations.append(secs)
        if action.target_zone:
            r.zones[action.target_zone] = r.zones.get(action.target_zone, 0) + 1
        if action.object_id:
            r.objects[action.object_id] = r.objects.get(action.object_id, 0) + 1

    def note_failure(self, worker_id: str | None, *, reassigned: bool = False) -> None:
        if not worker_id:
            return
        r = self.record(worker_id)
        r.failed += 1
        if reassigned:
            r.reassigned_away += 1

    # ── scoring ─────────────────────────────────────────────────────────────

    def adjust(self, worker_id: str, action: Any) -> tuple[float, list[str]]:
        """Return (score_delta, reasons). Negative delta = preferred."""
        r = self.records.get(worker_id)
        if r is None or (r.completed == 0 and r.failed == 0):
            return 0.0, []

        delta = 0.0
        reasons: list[str] = []

        rel = r.reliability
        if rel < 0.99 and r.claims >= 2:
            delta += W_RELIABILITY * (1.0 - rel)
            reasons.append(f"{int(rel * 100)}% of their reports verified")
        elif rel >= 0.99 and r.claims >= 2:
            delta -= W_RELIABILITY * 0.25
            reasons.append("every report so far has verified")

        if r.failed:
            delta += W_RELIABILITY * 0.5 * min(r.failed, 2)
            reasons.append(f"{r.failed} incomplete action{'s' if r.failed != 1 else ''}")

        mean = r.mean_duration
        collective = self._collective_mean()
        if mean and collective:
            ratio = mean / collective
            if ratio <= FAST_ENOUGH:
                delta -= W_SPEED
                reasons.append(f"fastest on recent actions ({mean:.0f}s avg)")
            elif ratio >= 1.35:
                delta += W_SPEED * 0.5

        if action.target_zone and r.zones.get(action.target_zone):
            n = r.zones[action.target_zone]
            delta -= W_FAMILIARITY * min(n, 3) / 3
            reasons.append(f"has worked this area {n}×")
        if action.object_id and r.objects.get(action.object_id):
            delta -= W_FAMILIARITY * 0.5
            reasons.append("has handled this item before")

        if r.last_finished_at is not None:
            idle = time.monotonic() - r.last_finished_at
            if idle < 3.0:
                delta += W_RECENCY  # just finished — spread the work
        return round(delta, 3), reasons

    def _collective_mean(self) -> float | None:
        means = [r.mean_duration for r in self.records.values() if r.mean_duration]
        return sum(means) / len(means) if means else None

    # ── reporting ───────────────────────────────────────────────────────────

    def leaderboard(self, state: Any) -> list[dict[str, Any]]:
        out = []
        for wid, r in self.records.items():
            w = state.workers.get(wid)
            out.append({**r.summary(), "callsign": w.callsign if w else wid})
        return sorted(out, key=lambda d: (-d["completed"], d["worker_id"]))

    def after_action(self, state: Any) -> str:
        """One sentence for the completion panel, built from what actually happened."""
        board = [b for b in self.leaderboard(state) if b["completed"]]
        if not board:
            return ""
        top = board[0]
        fastest = min(
            (b for b in board if b["mean_seconds"]), key=lambda b: b["mean_seconds"], default=None
        )
        parts = [f"{top['callsign']} carried {top['completed']} actions"]
        if fastest and fastest["worker_id"] != top["worker_id"]:
            parts.append(f"{fastest['callsign']} was fastest at {fastest['mean_seconds']}s average")
        unreliable = [b for b in board if b["reliability"] < 1.0]
        if unreliable:
            u = unreliable[0]
            parts.append(f"{u['callsign']}'s reports verified {int(u['reliability'] * 100)}% of the time")
        return "; ".join(parts) + "."


def _elapsed(iso: str) -> float:
    from datetime import datetime, timezone

    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except Exception:
        return 0.0


attribution = Attribution()

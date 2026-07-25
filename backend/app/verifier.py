"""Confidence-weighted verification.

OWNER: Nikki. Working implementation — extend in place.

Pure functions. Mutates nothing. The orchestrator applies the result.
Weights and the threshold live in models.EVIDENCE_WEIGHTS / settings — never redefined here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import settings
from .models import EVIDENCE_WEIGHTS, Action, Evidence, Predicate


@dataclass
class VerificationResult:
    score: float
    verified: bool
    evidence: list[Evidence] = field(default_factory=list)
    summary: str = ""
    failed: list[str] = field(default_factory=list)


def check_predicate(pred: Predicate, state: Any) -> tuple[bool, Evidence | None]:
    """Returns (holds, evidence). Evidence is None when nothing observed it."""
    scene = state.scene
    sim = state.world.mode == "simulation" or not state.world.camera_online

    if pred.type == "object_in_zone":
        obj = scene.by_id(pred.subject)
        if not obj:
            return False, None
        holds = obj.zone == pred.object
        if not holds:
            return False, None
        kind = "simulation" if sim else "vision"
        return True, Evidence(
            kind=kind,  # type: ignore[arg-type]
            confidence=obj.confidence,
            detail=f"{obj.display_label()} observed in {state.zone_label(pred.object)}",
        )

    if pred.type == "object_near_object":
        a, b = scene.by_id(pred.subject), scene.by_id(pred.object or "")
        if not a or not b:
            return False, None
        if a.position.dist(b.position) > pred.tolerance:
            return False, None
        return True, Evidence(
            kind="simulation" if sim else "vision",  # type: ignore[arg-type]
            confidence=min(a.confidence, b.confidence),
            detail=f"{a.display_label()} adjacent to {b.display_label()}",
        )

    if pred.type == "object_stacked_on":
        a = scene.by_id(pred.subject)
        if not a:
            return False, None
        if a.stacked_on == pred.object:
            # Primary source is the VLM; it reports relations CV can only approximate.
            kind = "vlm" if a.source == "vlm" else ("simulation" if sim else "inference")
            return True, Evidence(
                kind=kind,  # type: ignore[arg-type]
                confidence=a.confidence,
                detail=f"{a.display_label()} on {state.label_of(pred.object)}",
            )
        return False, None

    if pred.type == "object_held_by":
        a = scene.by_id(pred.subject)
        if a and a.held_by == pred.object:
            return True, Evidence(kind="vlm", confidence=0.9, detail=f"{a.display_label()} held")
        return False, None

    if pred.type == "object_visible":
        a = scene.by_id(pred.subject)
        if a and a.visible and a.confidence > 0.4:
            return True, Evidence(
                kind="simulation" if sim else "vision", confidence=a.confidence, detail="visible"  # type: ignore[arg-type]
            )
        return False, None

    if pred.type == "all_objects_in_zone":
        ids = [i for i in (pred.subject or "").split("|") if i]
        objs = [scene.by_id(i) for i in ids]
        if not objs or any(o is None or o.zone != pred.object for o in objs):
            return False, None
        return True, Evidence(
            kind="simulation" if sim else "vision",  # type: ignore[arg-type]
            confidence=min(o.confidence for o in objs if o),
            detail=f"{len(objs)} items in {state.zone_label(pred.object)}",
        )

    if pred.type == "sequence_completed":
        ids = [i for i in (pred.subject or "").split("|") if i]
        if ids and all(state.actions.get(i) and state.actions[i].status == "verified" for i in ids):
            return True, Evidence(kind="system", confidence=1.0, detail="all prerequisites verified")
        return False, None

    if pred.type in ("worker_ready", "worker_idle"):
        w = state.workers.get(pred.subject)
        if not w:
            return False, None
        ok = w.connected and w.available and (pred.type == "worker_ready" or not w.current_action_id)
        return (True, Evidence(kind="system", confidence=1.0, detail="worker state")) if ok else (False, None)

    if pred.type in ("worker_acknowledged", "manually_verified"):
        return False, None  # satisfied only by explicit evidence already on the action

    return False, None


def evaluate(action: Action, state: Any) -> VerificationResult:
    evidence: list[Evidence] = list(action.evidence)
    failed: list[str] = []

    for pred in action.expected_predicates:
        holds, ev = check_predicate(pred, state)
        if holds and ev:
            evidence.append(ev)
        elif not holds:
            failed.append(_describe_predicate(pred, state))

    score = min(1.0, sum(e.confidence * EVIDENCE_WEIGHTS.get(e.kind, e.weight) for e in evidence))
    # An action with unsatisfied physical predicates is never verified on a worker's word
    # alone — unless an operator explicitly overrode it.
    overridden = any(e.kind == "host_override" for e in evidence)
    verified = (score >= settings.verification_threshold) and (not failed or overridden)

    return VerificationResult(
        score=round(score, 2),
        verified=verified,
        evidence=evidence,
        summary=narrate(evidence, score),
        failed=failed,
    )


def _describe_predicate(p: Predicate, state: Any) -> str:
    if p.type == "object_in_zone":
        return f"{state.label_of(p.subject)} in {state.zone_label(p.object)}"
    if p.type == "object_stacked_on":
        return f"{state.label_of(p.subject)} on {state.label_of(p.object)}"
    return f"{p.type}({p.subject})"


def narrate(evidence: list[Evidence], score: float) -> str:
    parts: list[str] = []

    def first(kind: str) -> Evidence | None:
        return next((e for e in evidence if e.kind == kind), None)

    if v := first("vision"):
        parts.append(f"tracker {int(v.confidence * 100)}%")
    if m := first("vlm"):
        parts.append(f"scene model {int(m.confidence * 100)}%")
    if first("simulation"):
        parts.append("simulated state")
    if first("worker_report"):
        parts.append("worker confirmed")
    if first("host_override"):
        parts.append("operator confirmed")
    if first("system"):
        parts.append("prerequisites verified")
    if first("inference"):
        parts.append("inferred")
    if not parts:
        return "no evidence yet"
    return f"{' + '.join(parts)} → {int(score * 100)}% confidence"

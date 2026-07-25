"""Weighted evidence verification. Pure functions — mutates nothing. The orchestrator
tick calls evaluate() and applies the result. See docs/CONTRACTS.md §2 and Nikki.md §3.

Weights and the 0.70 threshold live in app.config / app.models.EVIDENCE_WEIGHTS.
Do not redefine them locally."""

from __future__ import annotations

from app.config import settings
from app.models import (
    Action,
    ActionStatus,
    Evidence,
    EVIDENCE_WEIGHTS,
    EvidenceKind,
    ObservedObject,
    Predicate,
    PredicateType,
    VerificationResult,
    WorkerStatus,
)


def _find_object(state, object_id: str | None) -> ObservedObject | None:
    if not object_id:
        return None
    return state.world.by_id(object_id) if hasattr(state.world, "by_id") else next(
        (o for o in state.world.objects if o.id == object_id), None
    )


def _evidence(kind: EvidenceKind, confidence: float, detail: str) -> Evidence:
    return Evidence(kind=kind, confidence=confidence, weight=EVIDENCE_WEIGHTS[kind], detail=detail)


def check_predicate(pred: Predicate, state) -> Evidence | None:
    if pred.type == PredicateType.object_in_zone:
        obj = _find_object(state, pred.subject)
        if obj and obj.zone == pred.object:
            kind = EvidenceKind.simulation if obj.source == "simulation" else EvidenceKind.vision
            return _evidence(kind, obj.confidence, f"{obj.id} centroid inside {pred.object} bounds")
        return None

    if pred.type == PredicateType.object_near_object:
        a, b = _find_object(state, pred.subject), _find_object(state, pred.object)
        if not a or not b:
            return None
        tolerance = pred.tolerance or 0.12
        dist = ((a.position.x - b.position.x) ** 2 + (a.position.y - b.position.y) ** 2) ** 0.5
        if dist < tolerance:
            return _evidence(EvidenceKind.vision, min(a.confidence, b.confidence), f"{a.id} near {b.id}")
        return None

    if pred.type == PredicateType.object_stacked_on:
        obj = _find_object(state, pred.subject)
        if obj and obj.stacked_on == pred.object:
            return _evidence(EvidenceKind.vlm, 0.85, f"{obj.id} reported on top of {pred.object}")
        return None

    if pred.type == PredicateType.object_held_by:
        obj = _find_object(state, pred.subject)
        if obj and obj.held_by == pred.object:
            return _evidence(EvidenceKind.vlm, 0.85, f"{obj.id} reported held by {pred.object}")
        return None

    if pred.type in (PredicateType.worker_ready, PredicateType.worker_idle):
        w = state.workers.get(pred.subject)
        if w and w.status == WorkerStatus.ready:
            return _evidence(EvidenceKind.inference, 1.0, f"{w.callsign} ready")
        return None

    if pred.type == PredicateType.object_visible:
        obj = _find_object(state, pred.subject)
        if obj and obj.visible and obj.confidence > 0.4:
            return _evidence(EvidenceKind.vision, obj.confidence, f"{obj.id} visible")
        return None

    if pred.type == PredicateType.all_objects_in_zone:
        ids = [i for i in (pred.subject or "").split(",") if i]
        objs = [_find_object(state, i) for i in ids]
        if objs and all(o and o.zone == pred.object for o in objs):
            return _evidence(EvidenceKind.vision, min(o.confidence for o in objs), "all objects in zone")
        return None

    if pred.type == PredicateType.sequence_completed:
        ids = [i for i in (pred.subject or "").split(",") if i]
        actions = [state.actions.get(i) for i in ids]
        if actions and all(a and a.status == ActionStatus.verified for a in actions):
            return _evidence(EvidenceKind.inference, 1.0, "dependency sequence verified")
        return None

    # worker_acknowledged and manually_verified are appended directly as Evidence by the
    # message handlers (worker_acknowledged / host_manual_verify) — nothing to check here.
    return None


def narrate(evidence: list[Evidence], score: float) -> str:
    def first(kind: str) -> Evidence | None:
        return next((e for e in evidence if e.kind.value == kind), None)

    parts: list[str] = []
    if v := first("vision"):
        parts.append(f"tracker {int(v.confidence * 100)}%")
    if m := first("vlm"):
        parts.append(f"scene model {int(m.confidence * 100)}%")
    if first("worker_report"):
        parts.append("worker confirmed")
    if first("host_override"):
        parts.append("operator confirmed")
    if first("simulation"):
        parts.append("simulated state")
    prefix = " + ".join(parts) if parts else "no evidence"
    return f"{prefix} → {int(score * 100)}% confidence"


def evaluate(action: Action, state) -> VerificationResult:
    evidence = list(action.evidence)
    for pred in action.expected_predicates:
        ev = check_predicate(pred, state)
        if ev:
            evidence.append(ev)
    score = min(1.0, sum(e.confidence * e.weight for e in evidence))
    score = round(score, 2)
    return VerificationResult(
        score=score,
        verified=score >= settings.verification_threshold,
        evidence=evidence,
        summary=narrate(evidence, score),
    )

"""Weighted evidence verification. Pure functions — mutates nothing except reading
`state.evidence` (Action has no `evidence` field in the shared model, so worker
reports / host overrides accumulate there, keyed by action id — see state.py).
The orchestrator tick calls evaluate() and applies the result. See docs/CONTRACTS.md
§2 and Nikki.md §3.

Weights and the 0.70 threshold live in app.config / app.models.EVIDENCE_WEIGHTS.
Do not redefine them locally."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

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
    WorkerStatus,
)

DEFAULT_NEAR_TOLERANCE = 0.12


@dataclass
class VerificationResult:
    score: float
    verified: bool
    evidence: list[Evidence] = field(default_factory=list)
    summary: str = ""


def _find_object(state, object_id: str | None) -> ObservedObject | None:
    if not object_id:
        return None
    return state.scene.by_id(object_id)


def _evidence(kind: str, confidence: float, detail: str) -> Evidence:
    return Evidence(kind=kind, confidence=confidence, weight=EVIDENCE_WEIGHTS[kind], detail=detail)


def check_predicate(pred: Predicate, state) -> Evidence | None:
    if pred.type == PredicateType.object_in_zone.value:
        obj = _find_object(state, pred.subject)
        if obj and obj.zone == pred.object:
            kind = EvidenceKind.simulation.value if obj.source == "simulation" else EvidenceKind.vision.value
            return _evidence(kind, obj.confidence, f"{obj.id} centroid inside {pred.object} bounds")
        return None

    if pred.type == PredicateType.object_near_object.value:
        a, b = _find_object(state, pred.subject), _find_object(state, pred.object)
        if not a or not b:
            return None
        tolerance = pred.tolerance or DEFAULT_NEAR_TOLERANCE
        dist = math.hypot(a.position.x - b.position.x, a.position.y - b.position.y)
        if dist < tolerance:
            return _evidence(EvidenceKind.vision.value, min(a.confidence, b.confidence), f"{a.id} near {b.id}")
        return None

    if pred.type == PredicateType.object_stacked_on.value:
        obj = _find_object(state, pred.subject)
        if obj and obj.stacked_on == pred.object:
            return _evidence(EvidenceKind.vlm.value, 0.85, f"{obj.id} reported on top of {pred.object}")
        return None

    if pred.type == PredicateType.object_held_by.value:
        obj = _find_object(state, pred.subject)
        if obj and obj.held_by == pred.object:
            return _evidence(EvidenceKind.vlm.value, 0.85, f"{obj.id} reported held by {pred.object}")
        return None

    if pred.type in (PredicateType.worker_ready.value, PredicateType.worker_idle.value):
        w = state.worker_by_id(pred.subject)
        if w and w.status == WorkerStatus.ready.value:
            return _evidence(EvidenceKind.inference.value, 1.0, f"{w.callsign} ready")
        return None

    if pred.type == PredicateType.object_visible.value:
        obj = _find_object(state, pred.subject)
        if obj and obj.visible and obj.confidence > 0.4:
            return _evidence(EvidenceKind.vision.value, obj.confidence, f"{obj.id} visible")
        return None

    if pred.type == PredicateType.all_objects_in_zone.value:
        ids = [i for i in (pred.subject or "").split(",") if i]
        objs = [_find_object(state, i) for i in ids]
        if objs and all(o and o.zone == pred.object for o in objs):
            return _evidence(EvidenceKind.vision.value, min(o.confidence for o in objs), "all objects in zone")
        return None

    if pred.type == PredicateType.sequence_completed.value:
        ids = [i for i in (pred.subject or "").split(",") if i]
        actions = [state.action_by_id(i) for i in ids]
        if actions and all(a and a.status == ActionStatus.verified.value for a in actions):
            return _evidence(EvidenceKind.inference.value, 1.0, "dependency sequence verified")
        return None

    # worker_acknowledged and manually_verified are appended directly to
    # state.evidence[action.id] by the message handlers — nothing to check here.
    return None


def narrate(evidence: list[Evidence], score: float) -> str:
    def first(kind: str) -> Evidence | None:
        return next((e for e in evidence if e.kind == kind), None)

    parts: list[str] = []
    if v := first(EvidenceKind.vision.value):
        parts.append(f"tracker {int(v.confidence * 100)}%")
    if m := first(EvidenceKind.vlm.value):
        parts.append(f"scene model {int(m.confidence * 100)}%")
    if first(EvidenceKind.worker_report.value):
        parts.append("worker confirmed")
    if first(EvidenceKind.host_override.value):
        parts.append("operator confirmed")
    if first(EvidenceKind.simulation.value):
        parts.append("simulated state")
    prefix = " + ".join(parts) if parts else "no evidence"
    return f"{prefix} → {int(score * 100)}% confidence"


def evaluate(action: Action, state) -> VerificationResult:
    evidence = list(state.evidence.get(action.id, []))
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

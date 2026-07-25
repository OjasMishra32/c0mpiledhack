"""Grounding — binds plain-language phrases to objects the camera actually sees.

OWNER: Zechariah. Working implementation — extend in place.

This is the component that makes "promptable" true. Nothing here knows what objects
exist; it scores phrases against the LIVE scene's measured descriptors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..demo.simulator import HUE_NAMES
from ..models import Scene

COLOR_WORDS = sorted({n for _, n in HUE_NAMES} | {"white", "black", "grey", "gray"})
SHAPE_WORDS = {"round": "round", "circular": "round", "square": "rectangular",
               "rectangular": "rectangular", "box": "rectangular", "boxy": "rectangular"}
STOP = {"the", "a", "an", "to", "into", "in", "at", "and", "then", "with", "of", "for",
        "move", "bring", "take", "put", "place", "deliver", "get", "needs", "need",
        "using", "supply", "restock", "fulfill", "stabilize"}

AMBIGUITY_MARGIN = 0.15


@dataclass
class Binding:
    phrase: str
    object_id: str | None
    confidence: float
    alternatives: list[str] = field(default_factory=list)
    basis: str = ""
    ambiguous: bool = False


@dataclass
class GroundingResult:
    bindings: list[Binding] = field(default_factory=list)
    deliveries: list[tuple[str, str]] = field(default_factory=list)  # (object_id, zone_id)
    zone_mentions: list[str] = field(default_factory=list)
    unbound_places: list[str] = field(default_factory=list)
    ambiguous: list[Binding] = field(default_factory=list)

    @property
    def object_count(self) -> int:
        return len({o for o, _ in self.deliveries})

    @property
    def destination_count(self) -> int:
        return len({z for _, z in self.deliveries})

    def ambiguous_payload(self) -> dict[str, Any]:
        b = self.ambiguous[0]
        return {
            "phrase": b.phrase,
            "candidates": [b.object_id] + b.alternatives if b.object_id else b.alternatives,
            "message": f"Multiple items match “{b.phrase}”. Select the intended one.",
        }


def _tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in STOP]


def score_object(phrase: str, obj: Any, scene: Scene) -> tuple[float, str]:
    """Score one observed object against a noun phrase. All signals are measured."""
    p = phrase.lower()
    toks = set(_tokens(phrase))
    score, basis = 0.0, []

    d = obj.descriptor
    if d.color_name and d.color_name in p:
        score += 0.40
        basis.append("color match")
    elif d.color_name in ("grey", "gray") and ("gray" in p or "grey" in p):
        score += 0.40
        basis.append("color match")

    label_toks = set(_tokens(obj.semantic_label or "")) | set(_tokens(obj.role or ""))
    overlap = toks & label_toks
    if overlap:
        score += min(0.30, 0.15 * len(overlap))
        basis.append("label match")

    for word, hint in SHAPE_WORDS.items():
        if word in p and d.shape_hint == hint:
            score += 0.15
            basis.append("shape match")
            break

    zone = scene.zone_by_id(obj.zone)
    if zone and zone.label.lower() in p:
        score += 0.10
        basis.append("location match")

    if "left" in p and obj.position.x < 0.4:
        score += 0.08
    if "right" in p and obj.position.x > 0.6:
        score += 0.08

    areas = sorted(o.descriptor.area_norm for o in scene.objects)
    if areas:
        if any(w in p for w in ("big", "large", "biggest")) and d.area_norm >= areas[-1]:
            score += 0.05
            basis.append("size match")
        if any(w in p for w in ("small", "little", "smallest")) and d.area_norm <= areas[0]:
            score += 0.05
            basis.append("size match")

    return score, " + ".join(basis) or "weak match"


def resolve(phrase: str, scene: Scene) -> Binding:
    scored = sorted(
        ((score_object(phrase, o, scene), o.id) for o in scene.objects),
        key=lambda t: -t[0][0],
    )
    if not scored or scored[0][0][0] <= 0.0:
        return Binding(phrase=phrase, object_id=None, confidence=0.0, basis="no match")
    (top_score, basis), top_id = scored[0]
    runner_up = scored[1][0][0] if len(scored) > 1 else 0.0
    alts = [oid for (s, _), oid in scored[1:] if s > 0]

    # Ambiguity is about the MARGIN between candidates, never an absolute score. A single
    # clean colour match is a weak-looking number but a completely unambiguous binding;
    # treating it as ambiguous would stop every plan to ask a pointless question.
    ambiguous = runner_up > 0 and (top_score - runner_up) < AMBIGUITY_MARGIN

    # Normalize so a decisive match reads like one to a human.
    confidence = round(min(1.0, top_score / 0.55), 2)
    if ambiguous:
        confidence = round(min(confidence, 0.45), 2)

    return Binding(
        phrase=phrase,
        object_id=top_id,
        confidence=confidence,
        alternatives=alts[:3],
        basis=basis,
        ambiguous=ambiguous,
    )


def _zone_for_clause(clause: str, scene: Scene) -> str | None:
    c = clause.lower()
    best, best_len = None, 0
    for z in scene.zones:
        lab = z.label.lower()
        if lab in c and len(lab) > best_len:
            best, best_len = z.id, len(lab)
        # tolerate partial phrasing: "pack station" ~ "the pack"
        head = lab.split()[0] if lab.split() else ""
        if not best and head and len(head) > 3 and head in c:
            best = z.id
    return best


def _object_phrases(clause: str, scene: Scene) -> list[str]:
    """Pull candidate noun phrases: colour words and any words matching known labels."""
    c = clause.lower()
    found: list[str] = []
    for color in COLOR_WORDS:
        for m in re.finditer(rf"\b{color}\b(\s+\w+)?", c):
            found.append(m.group(0).strip())
    for obj in scene.objects:
        for src in (obj.role, obj.semantic_label):
            if not src:
                continue
            key = src.lower().replace("the ", "").strip()
            if key and key in c and key not in " ".join(found):
                found.append(key)
    # de-dupe preserving order
    out: list[str] = []
    for f in found:
        if f not in out:
            out.append(f)
    return out


def resolve_all(goal_text: str, scene: Scene) -> GroundingResult:
    res = GroundingResult()
    if not scene.objects:
        return res

    clauses = [c.strip() for c in re.split(r"[.;,]| and (?=\w)", goal_text) if c.strip()]
    used: set[str] = set()
    last_zone: str | None = None

    for clause in clauses:
        zone = _zone_for_clause(clause, scene)
        if zone:
            last_zone = zone
            if zone not in res.zone_mentions:
                res.zone_mentions.append(zone)
        target = zone or last_zone
        for phrase in _object_phrases(clause, scene):
            b = resolve(phrase, scene)
            if b.object_id is None:
                continue
            res.bindings.append(b)
            if b.ambiguous:
                res.ambiguous.append(b)
            if target and b.object_id not in used:
                res.deliveries.append((b.object_id, target))
                used.add(b.object_id)

    # Place names mentioned but not matched to any zone become unbound chips in the UI.
    for m in re.finditer(r"\b(?:to|at|into)\s+the\s+([a-z ]{3,24}?)(?:\s|$|,|\.)", goal_text.lower()):
        name = m.group(1).strip()
        if not any(z.label.lower() in name or name in z.label.lower() for z in scene.zones):
            if name not in res.unbound_places and len(name.split()) <= 3:
                res.unbound_places.append(name)

    # Nothing parsed? Distribute what we can see across the zones we know. A plan the
    # operator can see and correct beats an empty screen.
    if not res.deliveries:
        zones = [z.id for z in scene.zones] or ["field"]
        for i, obj in enumerate(scene.objects):
            res.deliveries.append((obj.id, zones[i % len(zones)]))

    return res

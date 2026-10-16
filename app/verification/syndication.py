"""Detect likely syndication without discarding source provenance."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from rapidfuzz import fuzz

from app.models.schemas import Source, SourceIndependence, SourceRelationship


_WIRE_SERVICES = ("reuters", "associated press", "\bap\b", "afp", "agence france-presse")


@dataclass(frozen=True)
class SyndicationResult:
    relationship: SourceIndependence
    confidence: float
    evidence: tuple[str, ...]


class SyndicationDetector:
    def compare(self, a: Source, b: Source) -> SyndicationResult:
        if a.id == b.id:
            return SyndicationResult(SourceIndependence.UNKNOWN, 0.0, ())
        evidence: list[str] = []
        if a.original_source_url and a.original_source_url == b.original_source_url:
            evidence.append("shared_origin_url")
        if a.content_hash and a.content_hash == b.content_hash:
            evidence.append("duplicate_content_hash")
        text_similarity = self._similarity(a, b)
        if text_similarity >= 0.93:
            evidence.append(f"near_identical_text:{text_similarity:.2f}")
        wire = self._wire_service(a) or self._wire_service(b)
        if wire and text_similarity >= 0.65:
            evidence.append(f"shared_wire_attribution:{wire}")
        if evidence:
            relation = SourceIndependence.LIKELY_COPIED if "duplicate_content_hash" in evidence else SourceIndependence.LIKELY_SYNDICATED
            return SyndicationResult(relation, min(0.98, 0.65 + .1 * len(evidence)), tuple(evidence))
        if a.publisher.casefold() != b.publisher.casefold() and text_similarity < .55:
            return SyndicationResult(SourceIndependence.INDEPENDENT, .7, ("different_publisher_and_text",))
        return SyndicationResult(SourceIndependence.UNKNOWN, .35, ("insufficient_provenance",))

    def relationships(self, sources: Iterable[Source]) -> list[SourceRelationship]:
        items = list(sources); found = []
        for index, a in enumerate(items):
            for b in items[index + 1:]:
                result = self.compare(a, b)
                found.append(SourceRelationship(source_id_a=a.id, source_id_b=b.id, relationship=result.relationship, confidence=result.confidence, evidence=list(result.evidence)))
        return found

    @staticmethod
    def _similarity(a: Source, b: Source) -> float:
        # Headlines are too short and routinely converge around the same place
        # and event. Do not infer syndication from titles alone.
        left = (a.content or "").strip(); right = (b.content or "").strip()
        return fuzz.token_set_ratio(left, right) / 100 if left and right else 0.0

    @staticmethod
    def _wire_service(source: Source) -> str | None:
        value = f"{source.author or ''} {source.content or ''} {source.title}".casefold()
        for service in _WIRE_SERVICES:
            if re.search(service, value): return service.replace("\\b", "")
        return None

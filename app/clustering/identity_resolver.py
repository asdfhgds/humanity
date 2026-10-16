"""Conservative resolution of real-world event identity.

Source URLs identify reports, not occurrences. This module deliberately keeps
those identities separate: a new occurrence receives a random, persistent
canonical ID, while a later report is attached only when its structured facts
provide strong evidence that it concerns that occurrence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import atan2, cos, radians, sin, sqrt
import re
from typing import Iterable, Literal, Optional
from uuid import uuid4

from rapidfuzz import fuzz

from app.datastore import EventStore, get_event_store
from app.models.schemas import Event, EventIdentity


IdentityDecision = Literal["SAME_EVENT", "DISTINCT_EVENT", "UNCERTAIN"]


@dataclass(frozen=True)
class IdentityResolution:
    """The explainable result of comparing a candidate to stored events."""

    decision: IdentityDecision
    canonical_id: str
    confidence: float
    matched_event_id: Optional[str]
    reasons: tuple[str, ...]


class EventIdentityResolver:
    """Resolve event identity with deterministic prefilters and conservative rules."""

    def __init__(
        self,
        event_store: Optional[EventStore] = None,
        *,
        time_window_hours: int = 36,
        location_window_km: float = 35.0,
        same_event_threshold: float = 0.82,
    ) -> None:
        self.event_store = event_store or get_event_store()
        self.time_window_hours = time_window_hours
        self.location_window_km = location_window_km
        self.same_event_threshold = same_event_threshold

    @staticmethod
    def new_canonical_id() -> str:
        """Create an opaque, stable identifier for a newly observed occurrence."""
        return f"evt_{uuid4().hex}"

    def resolve(self, candidate: Event, events: Optional[Iterable[Event]] = None) -> IdentityResolution:
        """Return SAME_EVENT only when the evidence is strong enough to merge."""
        candidates = list(events) if events is not None else self.event_store.get_all()
        best: Optional[tuple[Event, float, tuple[str, ...]]] = None
        ambiguous = False

        for existing in candidates:
            if existing.id == candidate.id:
                continue
            decision, score, reasons = self.compare(candidate, existing)
            if decision == "SAME_EVENT" and (best is None or score > best[1]):
                best = (existing, score, reasons)
            elif decision == "UNCERTAIN":
                ambiguous = True

        if best:
            event, score, reasons = best
            return IdentityResolution("SAME_EVENT", event.canonical_id or event.id, score, event.id, reasons)
        if ambiguous:
            return IdentityResolution(
                "UNCERTAIN", candidate.canonical_id or self.new_canonical_id(), 0.0, None,
                ("candidate passed a coarse prefilter but did not meet merge threshold",),
            )
        return IdentityResolution(
            "DISTINCT_EVENT", candidate.canonical_id or self.new_canonical_id(), 0.0, None,
            ("no compatible existing event",),
        )

    def compare(self, candidate: Event, existing: Event) -> tuple[IdentityDecision, float, tuple[str, ...]]:
        """Compare two events without mutating either one."""
        if candidate.event_type != existing.event_type:
            return "DISTINCT_EVENT", 0.0, ("event types differ",)
        time_hours = self._time_distance_hours(candidate.start_time, existing.start_time)
        if time_hours > self.time_window_hours:
            return "DISTINCT_EVENT", 0.0, (f"events are {time_hours:.1f} hours apart",)
        distance = self._location_distance(candidate, existing)
        if distance is not None and distance > self.location_window_km:
            return "DISTINCT_EVENT", 0.0, (f"locations are {distance:.1f} km apart",)
        if distance is None and not self._same_named_location(candidate, existing):
            return "UNCERTAIN", 0.0, ("location is missing or incompatible",)

        title_score = fuzz.token_set_ratio(candidate.title, existing.title) / 100
        description_score = fuzz.token_set_ratio(
            f"{candidate.title} {candidate.summary}", f"{existing.title} {existing.summary}"
        ) / 100
        text_score = max(title_score, description_score)
        location_score = 0.75 if distance is None else max(0.0, 1 - distance / self.location_window_km)
        time_score = max(0.0, 1 - time_hours / self.time_window_hours)
        score = 0.60 * text_score + 0.25 * location_score + 0.15 * time_score
        reasons = (
            f"same event type: {candidate.event_type.value}",
            f"text similarity: {text_score:.2f}",
            f"time difference: {time_hours:.1f}h",
            "named location match" if distance is None else f"location distance: {distance:.1f}km",
        )
        if score >= self.same_event_threshold and text_score >= 0.75:
            return "SAME_EVENT", score, reasons
        if text_score >= 0.55:
            return "UNCERTAIN", score, reasons
        return "DISTINCT_EVENT", score, reasons

    def resolve_event_identity(self, event: Event, candidate_sources: Optional[list[str]] = None) -> EventIdentity:
        """Compatibility API used by callers that persist :class:`EventIdentity`."""
        result = self.resolve(event)
        return EventIdentity(
            canonical_id=result.canonical_id,
            candidate_ids=candidate_sources or list(event.source_ids),
            match_confidence=result.confidence,
            match_method="deterministic",
            matched_with=[result.matched_event_id] if result.matched_event_id else [],
            decision=result.decision,
            decision_reasons=list(result.reasons),
        )

    @staticmethod
    def _same_named_location(a: Event, b: Event) -> bool:
        def normal(value: Optional[str]) -> str:
            return re.sub(r"\W+", " ", value or "").strip().casefold()
        return bool(normal(a.city) and normal(a.city) == normal(b.city) and a.country_code == b.country_code)

    @staticmethod
    def _time_distance_hours(a: datetime, b: datetime) -> float:
        def utc(value: datetime) -> datetime:
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return abs((utc(a) - utc(b)).total_seconds()) / 3600

    @staticmethod
    def _location_distance(a: Event, b: Event) -> Optional[float]:
        if None in (a.latitude, a.longitude, b.latitude, b.longitude):
            return None
        lat1, lon1, lat2, lon2 = map(radians, (a.latitude, a.longitude, b.latitude, b.longitude))
        dlat, dlon = lat2 - lat1, lon2 - lon1
        value = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        return 6371 * 2 * atan2(sqrt(value), sqrt(1 - value))

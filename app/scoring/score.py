from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from app.config import load_scoring, get_country_by_code
from app.models.schemas import Event, EventType
from app.datastore import get_event_store, get_source_store


class EventScorer:
    def __init__(self, event_store=None, source_store=None):
        self.config = load_scoring()
        self.event_store = event_store or get_event_store()
        self.source_store = source_store or get_source_store()

    def score_event(self, event: Event, sources: List[Any]) -> Dict[str, float]:
        """Calculate all score components for an event."""
        scores = {}
        
        # Importance score
        scores["importance"] = self._score_importance(event)
        
        # Human impact score
        scores["human_impact"] = self._score_human_impact(event)
        
        # Novelty score
        scores["novelty"] = self._score_novelty(event)
        
        # Corroboration score
        scores["corroboration"] = self._score_corroboration(event, sources)
        
        # Velocity score
        scores["velocity"] = self._score_velocity(event)
        
        # Public interest score (placeholder)
        scores["public_interest"] = 0.5
        
        # Unusualness score
        scores["unusualness"] = self._score_unusualness(event)
        
        # Apply weights
        weights = self.config.weights
        final_score = sum(
            scores.get(key, 0) * weights.get(key, 0)
            for key in weights
        )
        
        # Apply penalties
        penalties = self._calculate_penalties(event, sources)
        final_score *= (1 - penalties)
        
        # Cap score
        final_score = max(self.config.score_caps["min_score"], 
                         min(self.config.score_caps["max_score"], final_score))
        
        scores["final"] = final_score
        scores["penalties"] = penalties
        
        return scores

    def _score_importance(self, event: Event) -> float:
        """Score based on event type and severity."""
        factors = self.config.importance_factors
        
        base = factors["event_type_base"].get(event.event_type.value, 50)
        severity_mult = factors["severity_multiplier"].get(event.severity, 1.0)
        
        # Geographic significance
        geo_mult = 1.0
        country = get_country_by_code(event.country_code)
        if country:
            if country.code in ["US", "CN", "RU", "GB", "FR", "DE", "JP", "IN"]:
                geo_mult = factors["geographic_significance"].get("major_city", 1.1)
            if country.conflict_zones:
                geo_mult = max(geo_mult, factors["geographic_significance"].get("conflict_zone", 1.25))
        
        return min(100, base * severity_mult * geo_mult)

    def _score_human_impact(self, event: Event) -> float:
        """Score based on human cost."""
        factors = self.config.importance_factors["human_impact_factors"]
        score = 0.0
        
        # Deaths
        deaths = event.deaths_max or event.deaths_min or 0
        if deaths > 0:
            for threshold, value in sorted(factors["deaths_per_event"].items()):
                if deaths >= threshold:
                    score = value
        
        # Injuries
        injuries = event.injured_max or event.injured_min or 0
        if injuries > 0:
            score += injuries * factors["injuries_multiplier"] / 10
        
        # Displacement
        displaced = event.displaced_max or event.displaced_min or 0
        if displaced > 0:
            score += displaced * factors["displacement_multiplier"] / 1000
        
        # Arrests
        arrested = event.arrested_max or event.arrested_min or 0
        if arrested > 0:
            score += arrested * factors["arrests_multiplier"]
        
        return min(100, score)

    def _score_novelty(self, event: Event) -> float:
        """Score based on how new/unusual this event is."""
        factors = self.config.importance_factors["novelty_factors"]
        score = 50.0
        
        # Check if first event of this type in country recently
        recent_events = self.event_store.get_by_country(event.country_code)
        recent_events = [e for e in recent_events 
                        if (datetime.utcnow() - e.start_time.replace(tzinfo=None)).days <= 30]
        
        same_type_recent = [e for e in recent_events if e.event_type == event.event_type]
        if not same_type_recent:
            score *= factors["first_event_type_in_region"]
        
        # Check for escalation
        if event.conflict_id:
            conflict_events = self.event_store.query("events", {"conflict_id": event.conflict_id})
            if conflict_events:
                severities = [e.get("severity", 0) for e in conflict_events]
                if event.severity > max(severities):
                    score *= factors["escalation_from_previous"]
        
        return min(100, score)

    def _score_corroboration(self, event: Event, sources: List[Any]) -> float:
        """Score based on source corroboration."""
        factors = self.config.importance_factors["corroboration_factors"]
        
        if not sources:
            return 0.0
        # A publisher count is not a corroboration count. Until the detector has
        # evidence, unknown sources get conservative partial weight.
        weights = {"independent": 1.0, "likely_syndicated": 0.25, "likely_copied": 0.25, "unknown": 0.5}
        independent_equivalent = sum(weights.get(getattr(getattr(s, "independence_classification", "unknown"), "value", getattr(s, "independence_classification", "unknown")), .5) for s in sources)
        base = factors["independent_sources"].get(min(max(1, round(independent_equivalent)), 5), 0.5)
        
        # Weight by source type
        type_mult = 1.0
        for source in sources:
            st = getattr(source, "source_type", "news")
            type_mult = max(type_mult, factors["source_types"].get(st.value if hasattr(st, "value") else st, 1.0))
        
        return min(100, base * type_mult * 100)

    def _score_velocity(self, event: Event) -> float:
        """Score based on media velocity."""
        factors = self.config.importance_factors["velocity_factors"]
        
        # Count articles in first 24h
        sources = self.source_store.get_by_event(event.id)
        recent_sources = [
            s for s in sources
            if s.published_at and (datetime.utcnow() - s.published_at.replace(tzinfo=None)).total_seconds() < 86400
        ]
        
        articles_per_hour = len(recent_sources) / 24.0
        
        for threshold, value in sorted(factors["articles_per_hour"].items()):
            if articles_per_hour >= threshold:
                return value * 100
        
        return 0.0

    def _score_unusualness(self, event: Event) -> float:
        """Score based on how unusual this event is."""
        factors = self.config.importance_factors["unusualness_factors"]
        score = 50.0
        
        # Check if event type is rare in this country
        country_events = self.event_store.get_by_country(event.country_code)
        type_counts = {}
        for e in country_events:
            type_counts[e.event_type.value] = type_counts.get(e.event_type.value, 0) + 1
        
        total = sum(type_counts.values())
        if total > 0:
            type_freq = type_counts.get(event.event_type.value, 0) / total
            if type_freq < 0.1:
                score *= factors["rare_event_type_in_country"]
        
        return min(100, score)

    def _calculate_penalties(self, event: Event, sources: List[Any]) -> float:
        """Calculate penalties."""
        penalties_config = self.config.penalties
        total_penalty = 0.0
        
        # Single source penalty
        if len(sources) == 1:
            total_penalty += penalties_config["single_source"]
        
        # Low confidence penalty
        if event.confidence.value in ["LOW", "MEDIUM"]:
            total_penalty += penalties_config["low_confidence"]
        
        # Old event penalty
        age_hours = (datetime.utcnow() - event.start_time.replace(tzinfo=None)).total_seconds() / 3600
        if age_hours > 72:
            total_penalty += penalties_config["old_event_hours"]
        
        # Duplicate penalty (if another event with same canonical_id exists)
        if event.canonical_id:
            existing = self.event_store.get_by_canonical_id(event.canonical_id)
            if existing and existing.id != event.id:
                total_penalty += penalties_config["duplicate"]
        
        return min(0.8, total_penalty)


def decide_publish(event: Event, scores: Dict[str, float], source_store=None) -> str:
    """Decide publication status based on scores."""
    config = load_scoring()
    thresholds = config.thresholds
    
    final_score = scores.get("final", 0)
    confidence_ok = event.confidence.value in ["HIGH", "VERIFIED"] or event.confidence.value == "MEDIUM"
    src_store = source_store or get_source_store()
    min_sources = len(src_store.get_by_event(event.id))
    
    if (final_score >= thresholds["auto_publish"] and 
        confidence_ok and 
        min_sources >= thresholds["min_sources_for_auto_publish"]):
        return "AUTO_PUBLISH"
    
    if final_score >= thresholds["auto_publish_warning"]:
        return "AUTO_PUBLISH_WARNING"
    
    return "HOLD"

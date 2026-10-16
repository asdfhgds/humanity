from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from slugify import slugify
from app.models.schemas import Event, Source, Claim, EventType, ConfidenceLevel, PublicationStatus
from app.datastore import get_event_store, get_source_store, get_claim_store
from app.scoring.score import EventScorer, decide_publish
from app.utils import format_range, time_ago, clean_text, compute_article_hash
from app.config import get_settings


class EventPublisher:
    def __init__(self, event_store=None, source_store=None, claim_store=None):
        self.event_store = event_store or get_event_store()
        self.source_store = source_store or get_source_store()
        self.claim_store = claim_store or get_claim_store()
        self.scorer = EventScorer(event_store=self.event_store, source_store=self.source_store)
        self.settings = get_settings()
        self.web_data_dir = self.settings.web_dir / "public" / "data"
        self.web_data_dir.mkdir(parents=True, exist_ok=True)

    def generate_slug(self, event: Event) -> str:
        """Generate URL slug for event."""
        date_str = event.start_time.strftime("%Y/%m/%d")
        title_slug = slugify(event.title, max_length=60)
        return f"{date_str}/{title_slug}"

    def build_event_page_data(self, event: Event) -> Dict[str, Any]:
        """Build complete data for event page."""
        sources = self.source_store.get_by_event(event.id)
        claims = self.claim_store.get_by_event(event.id)
        
        # Group claims by type
        claims_by_type = {}
        for claim in claims:
            ct = claim.claim_type.value
            if ct not in claims_by_type:
                claims_by_type[ct] = []
            claims_by_type[ct].append({
                "subject": claim.subject,
                "predicate": claim.predicate,
                "value": claim.value,
                "unit": claim.unit,
                "min_value": claim.min_value,
                "max_value": claim.max_value,
                "source_quote": claim.source_quote,
                "confidence": claim.confidence,
                "verification_status": claim.verification_status.value,
                "source_id": claim.source_id,
            })
        
        # Build source list
        source_list = []
        for source in sources:
            source_list.append({
                "id": source.id,
                "publisher": source.publisher,
                "title": source.title,
                "url": str(source.url),
                "published_at": source.published_at.isoformat() if source.published_at else None,
                "source_type": source.source_type.value,
                "country": source.country,
                "language": source.language,
            })
        
        # Determine status badge
        status_map = {
            "developing": "Developing",
            "ongoing": "Ongoing",
            "contained": "Contained",
            "resolved": "Resolved",
            "historical": "Historical",
        }
        
        confidence_map = {
            "LOW": "Low",
            "MEDIUM": "Medium",
            "HIGH": "High",
            "VERIFIED": "Verified",
        }
        
        # Build human impact summary
        human_impact = {}
        if event.deaths_min is not None or event.deaths_max is not None:
            human_impact["deaths"] = format_range(event.deaths_min, event.deaths_max)
        if event.injured_min is not None or event.injured_max is not None:
            human_impact["injuries"] = format_range(event.injured_min, event.injured_max)
        if event.arrested_min is not None or event.arrested_max is not None:
            human_impact["arrests"] = format_range(event.arrested_min, event.arrested_max)
        if event.displaced_min is not None or event.displaced_max is not None:
            human_impact["displaced"] = format_range(event.displaced_min, event.displaced_max)
        if event.estimated_participants_min is not None or event.estimated_participants_max is not None:
            human_impact["participants"] = format_range(event.estimated_participants_min, event.estimated_participants_max)
        
        # What is known / uncertain
        known = []
        uncertain = []
        
        for claim_type, claim_list in claims_by_type.items():
            for claim in claim_list:
                if claim["verification_status"] in ["corroborated", "independently_verified"]:
                    known.append(f"{claim['subject']} {claim['predicate']}: {claim['source_quote'][:100]}")
                else:
                    uncertain.append(f"{claim['subject']} {claim['predicate']}: {claim['source_quote'][:100]} (reported)")
        
        if not known:
            known.append("Event detected via automated monitoring")
        if not uncertain:
            uncertain.append("Details are still emerging")
        
        return {
            "id": event.id,
            "canonical_id": event.canonical_id,
            "slug": event.slug,
            "title": event.title,
            "event_type": event.event_type.value,
            "event_type_label": event.event_type.value.title(),
            "description": event.description,
            "summary": event.summary,
            "country_code": event.country_code,
            "country_name": event.country_name,
            "region": event.region,
            "city": event.city,
            "latitude": event.latitude,
            "longitude": event.longitude,
            "location_precision": event.location_precision.value,
            "start_time": event.start_time.isoformat(),
            "end_time": event.end_time.isoformat() if event.end_time else None,
            "severity": event.severity,
            "status": event.status.value,
            "status_label": status_map.get(event.status.value, event.status.value),
            "confidence": event.confidence.value,
            "confidence_label": confidence_map.get(event.confidence.value, event.confidence.value),
            "human_impact": human_impact,
            "sources": source_list,
            "claims": claims_by_type,
            "known": known,
            "uncertain": uncertain,
            "related_events": event.related_event_ids,
            "conflict_id": event.conflict_id,
            "topic_tags": event.topic_tags,
            "importance_score": event.importance_score,
            "first_detected_at": event.first_detected_at.isoformat(),
            "last_updated_at": event.last_updated_at.isoformat(),
            "published_at": event.published_at.isoformat() if event.published_at else None,
            "time_ago": time_ago(event.start_time),
        }

    def publish_event(self, event: Event) -> Dict[str, Any]:
        """Publish event: generate page data, update indexes."""
        if self.settings.production_mode and getattr(event, "is_mock", False):
            raise ValueError("Mock-derived events cannot be published in production mode")
        # Respect explicit HELD/REJECTED status
        if event.publication_status in (PublicationStatus.HELD, PublicationStatus.REJECTED):
            # Still update global stats to reflect the change
            self._update_global_stats()
            return {"published": False, "decision": event.publication_status.value.upper(), "scores": {}, "publication_status": event.publication_status.value}
        
        # Score the event
        sources = self.source_store.get_by_event(event.id)
        from app.models.schemas import SourceIndependence
        from app.verification.syndication import SyndicationDetector
        detector = SyndicationDetector()
        relationships = detector.relationships(sources)
        for source in sources:
            related = [r for r in relationships if source.id in (r.source_id_a, r.source_id_b)]
            classifications = [r.relationship for r in related]
            if any(c in (SourceIndependence.LIKELY_COPIED, SourceIndependence.LIKELY_SYNDICATED) for c in classifications):
                classification = SourceIndependence.LIKELY_SYNDICATED
            elif related and all(c == SourceIndependence.INDEPENDENT for c in classifications):
                classification = SourceIndependence.INDEPENDENT
            else:
                classification = source.independence_classification
            if classification != source.independence_classification:
                self.source_store.update(source.id, {"independence_classification": classification.value})
                source.independence_classification = classification
        from app.verification.evidence import EvidenceAssessor
        assessment = EvidenceAssessor().assess(event, sources, self.claim_store.get_by_event(event.id))
        assessment_data = assessment.model_dump(mode="json")
        self.event_store.update(event.id, {
            "evidence_assessment": assessment_data,
            "publication_reason": ",".join(assessment.reasons),
        })
        if not assessment.publication_ready:
            self.event_store.update(event.id, {"publication_status": PublicationStatus.HELD.value, "visibility": False})
            self._remove_public_artifacts(event)
            self._update_global_stats()
            return {"published": False, "decision": "HOLD", "scores": {}, "publication_status": "held", "publication_reason": assessment.reasons}
        scores = self.scorer.score_event(event, sources)
        decision = decide_publish(event, scores, self.source_store)
        
        # Map decision to publication status
        if decision == "HOLD":
            self.event_store.update(event.id, {"publication_status": PublicationStatus.HELD.value})
            self._update_global_stats()
            return {"published": False, "decision": "HOLD", "scores": scores, "publication_status": PublicationStatus.HELD.value}
        
        pub_status = PublicationStatus.PUBLISHED_WITH_WARNING if decision == "AUTO_PUBLISH_WARNING" else PublicationStatus.PUBLISHED
        self.event_store.update(event.id, {"publication_status": pub_status.value})
        
        # A report URL is not a real-world event identity. New events receive an
        # opaque canonical ID; identity resolution reuses an existing one only
        # when a later candidate has sufficient matching evidence.
        if not event.canonical_id:
            from app.clustering.identity_resolver import EventIdentityResolver

            identity = EventIdentityResolver(event_store=self.event_store).resolve_event_identity(event)
            event.canonical_id = identity.canonical_id
            event.event_identity = identity
            self.event_store.update(
                event.id,
                {"canonical_id": event.canonical_id, "event_identity": identity.model_dump(mode="json")},
            )
        
        # Generate slug if not exists
        if not event.slug:
            event.slug = self.generate_slug(event)
            self.event_store.update(event.id, {"slug": event.slug})
        
        # Set published_at if not set
        if not event.published_at:
            self.event_store.update(event.id, {"published_at": datetime.utcnow().isoformat()})
        
        # Build page data
        page_data = self.build_event_page_data(event)
        page_data["scores"] = scores
        page_data["publish_decision"] = decision
        page_data["publish_warning"] = decision == "AUTO_PUBLISH_WARNING"
        page_data["publication_status"] = pub_status.value
        page_data["evidence_assessment"] = assessment_data
        page_data["publication_reason"] = assessment.reasons
        
        # Save event page JSON
        event_file = self.web_data_dir / "events" / f"{event.slug.replace('/', '_')}.json"
        event_file.parent.mkdir(parents=True, exist_ok=True)
        with open(event_file, "w", encoding="utf-8") as f:
            json.dump(page_data, f, ensure_ascii=False, indent=2)
        
        # Update map data
        self._update_map_data(event, page_data)
        
        # Update country index
        self._update_country_index(event, page_data)
        
        # Update timeline data
        self._update_timeline_data(event, page_data)
        
        # Update global stats
        self._update_global_stats()
        
        return {"published": True, "decision": decision, "scores": scores, "slug": event.slug, "publication_status": pub_status.value}

    def _remove_public_artifacts(self, event: Event) -> None:
        """Remove a previously public event when policy changes to HOLD."""
        if event.slug:
            path = self.web_data_dir / "events" / f"{event.slug.replace('/', '_')}.json"
            if path.exists():
                path.unlink()

    def _update_map_data(self, event: Event, page_data: Dict[str, Any]) -> None:
        """Update map GeoJSON data."""
        if event.latitude is None or event.longitude is None:
            return
        
        map_file = self.web_data_dir / "map" / "events.geojson"
        map_file.parent.mkdir(parents=True, exist_ok=True)
        
        feature = {
            "type": "Feature",
            "id": event.id,
            "geometry": {
                "type": "Point",
                "coordinates": [event.longitude, event.latitude]
            },
            "properties": {
                "id": event.id,
                "slug": event.slug,
                "title": event.title,
                "event_type": event.event_type.value,
                "country_code": event.country_code,
                "country_name": event.country_name,
                "city": event.city,
                "date": event.start_time.strftime("%Y-%m-%d"),
                "severity": event.severity,
                "confidence": event.confidence.value,
                "deaths": event.deaths_max or event.deaths_min,
                "injuries": event.injured_max or event.injured_min,
            }
        }
        
        # Read existing
        features = []
        if map_file.exists():
            try:
                with open(map_file, "r") as f:
                    data = json.load(f)
                features = data.get("features", [])
            except Exception:
                features = []
        
        # Update or add
        existing_idx = next((i for i, f in enumerate(features) if f.get("id") == event.id), None)
        if existing_idx is not None:
            features[existing_idx] = feature
        else:
            features.append(feature)
        
        # Keep only last 10000 events
        features.sort(key=lambda f: f["properties"].get("date", ""), reverse=True)
        features = features[:10000]
        
        geojson = {
            "type": "FeatureCollection",
            "features": features
        }
        
        with open(map_file, "w") as f:
            json.dump(geojson, f, ensure_ascii=False)

    def _update_country_index(self, event: Event, page_data: Dict[str, Any]) -> None:
        """Update country index."""
        country_file = self.web_data_dir / "countries" / f"{event.country_code.lower()}.json"
        country_file.parent.mkdir(parents=True, exist_ok=True)
        
        country_data = {
            "code": event.country_code,
            "name": event.country_name,
            "last_event": page_data,
            "updated_at": datetime.utcnow().isoformat(),
        }
        
        # Load existing
        existing = {}
        if country_file.exists():
            try:
                with open(country_file, "r") as f:
                    existing = json.load(f)
            except Exception:
                pass
        
        # Update event counts - only publicly visible events
        events = self.event_store.get_publicly_visible()
        country_events = [e for e in events if e.country_code == event.country_code]
        country_data["event_count"] = len(country_events)
        country_data["recent_events"] = [e.slug for e in country_events[:20]]
        
        # Count by type
        type_counts = {}
        for e in country_events:
            type_counts[e.event_type.value] = type_counts.get(e.event_type.value, 0) + 1
        country_data["type_counts"] = type_counts
        
        # Merge
        country_data = {**existing, **country_data}
        
        with open(country_file, "w", encoding="utf-8") as f:
            json.dump(country_data, f, ensure_ascii=False, indent=2)

    def _update_timeline_data(self, event: Event, page_data: Dict[str, Any]) -> None:
        """Update timeline data."""
        timeline_file = self.web_data_dir / "timelines" / f"{event.country_code.lower()}.json"
        timeline_file.parent.mkdir(parents=True, exist_ok=True)
        
        timeline_entry = {
            "id": event.id,
            "slug": event.slug,
            "date": event.start_time.strftime("%Y-%m-%d"),
            "title": event.title,
            "event_type": event.event_type.value,
            "severity": event.severity,
            "city": event.city,
        }
        
        timeline = []
        if timeline_file.exists():
            try:
                with open(timeline_file, "r") as f:
                    timeline = json.load(f)
            except Exception:
                pass
        
        # Add if not exists
        if not any(e["id"] == event.id for e in timeline):
            timeline.append(timeline_entry)
        
        timeline.sort(key=lambda e: e["date"], reverse=True)
        
        with open(timeline_file, "w") as f:
            json.dump(timeline, f, ensure_ascii=False, indent=2)

    def _update_global_stats(self) -> None:
        """Update global statistics."""
        stats_file = self.web_data_dir / "stats" / "global.json"
        stats_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Only count publicly visible events (PUBLISHED or PUBLISHED_WITH_WARNING)
        published_events = self.event_store.get_publicly_visible()
        
        today = datetime.utcnow().date()
        today_events = [
            e for e in published_events 
            if e.start_time.date() == today
        ]
        
        stats = {
            "total_events": len(published_events),
            "events_today": len(today_events),
            "countries_affected": len(set(e.country_code for e in published_events)),
            "by_type": {},
            "by_severity": {},
            "total_deaths_reported": sum(e.deaths_max or 0 for e in published_events),
            "total_injuries_reported": sum(e.injured_max or 0 for e in published_events),
            "last_updated": datetime.utcnow().isoformat(),
        }
        
        for event in published_events:
            stats["by_type"][event.event_type.value] = stats["by_type"].get(event.event_type.value, 0) + 1
            stats["by_severity"][event.severity] = stats["by_severity"].get(event.severity, 0) + 1
        
        with open(stats_file, "w") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)

    def generate_daily_brief(self) -> Dict[str, Any]:
        """Generate daily briefing."""
        config = self.scorer.config
        brief_config = config.daily_brief
        
        recent_events = self.event_store.get_recent(hours=24)
        # Only include publicly visible events (PUBLISHED or PUBLISHED_WITH_WARNING)
        recent_events = [e for e in recent_events if e.visibility and e.publication_status in (PublicationStatus.PUBLISHED, PublicationStatus.PUBLISHED_WITH_WARNING)]
        
        # Score and sort
        scored = []
        for event in recent_events:
            sources = self.source_store.get_by_event(event.id)
            scores = self.scorer.score_event(event, sources)
            if scores["final"] >= brief_config["min_score_for_inclusion"]:
                scored.append((event, scores))
        
        scored.sort(key=lambda x: -x[1]["final"])
        
        # Limit per country/conflict
        country_counts = {}
        conflict_counts = {}
        selected = []
        
        for event, scores in scored:
            country_count = country_counts.get(event.country_code, 0)
            if country_count >= brief_config["max_items_per_country"]:
                continue
            
            conflict_count = 0
            if event.conflict_id:
                conflict_count = conflict_counts.get(event.conflict_id, 0)
                if conflict_count >= brief_config["max_items_per_conflict"]:
                    continue
            
            selected.append((event, scores))
            country_counts[event.country_code] = country_count + 1
            if event.conflict_id:
                conflict_counts[event.conflict_id] = conflict_count + 1
            
            if len(selected) >= brief_config["max_items"]:
                break
        
        brief_items = []
        for event, scores in selected:
            sources = self.source_store.get_by_event(event.id)
            brief_items.append({
                "event_id": event.id,
                "slug": event.slug,
                "title": event.title,
                "event_type": event.event_type.value,
                "country": event.country_name,
                "city": event.city,
                "date": event.start_time.strftime("%Y-%m-%d"),
                "summary": event.summary,
                "source_count": len(sources),
                "confidence": event.confidence.value,
                "importance_score": scores["final"],
                "human_impact": {
                    "deaths": format_range(event.deaths_min, event.deaths_max),
                    "injuries": format_range(event.injured_min, event.injured_max),
                }
            })
        
        brief = {
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
            "generated_at": datetime.utcnow().isoformat(),
            "items": brief_items,
            "total_events_today": len(recent_events),
        }
        
        brief_file = self.web_data_dir / "briefs" / f"{brief['date']}.json"
        brief_file.parent.mkdir(parents=True, exist_ok=True)
        with open(brief_file, "w") as f:
            json.dump(brief, f, ensure_ascii=False, indent=2)
        
        return brief


def slugify(text: str, max_length: int = 80) -> str:
    """Simple slugify."""
    import re
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text[:max_length].strip("-")

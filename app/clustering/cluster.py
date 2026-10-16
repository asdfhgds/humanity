from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from rapidfuzz import fuzz, process
from app.utils import canonicalize_url, compute_article_hash
from app.models.schemas import ConfidenceLevel, Event, EventIdentity, EventStatus, EventType, Source
from uuid import uuid4
from app.datastore import get_event_store, get_source_store
from app.clustering.identity_resolver import EventIdentityResolver


class EventClusterer:
    def __init__(self):
        self.event_store = get_event_store()
        self.source_store = get_source_store()
        self.identity_resolver = EventIdentityResolver()
        
        self.title_similarity_threshold = 80
        self.entity_overlap_threshold = 0.5
        self.location_threshold_km = 50
        self.time_window_hours = 48

    def find_similar_events(self, candidate: Dict[str, Any]) -> List[Tuple[Event, float]]:
        """Find existing events similar to candidate."""
        candidates = self.event_store.get_all()
        if not candidates:
            return []
        
        scored = []
        for event in candidates:
            score = self._compute_similarity(candidate, event)
            if score > 0.5:
                scored.append((event, score))
        
        scored.sort(key=lambda x: -x[1])
        return scored

    def _compute_similarity(self, candidate: Dict[str, Any], event: Event) -> float:
        scores = []
        
        # URL canonicalization match (exact)
        candidate_url = candidate.get("source_url", "")
        if candidate_url:
            for source_id in event.source_ids:
                source = self.source_store.get(source_id)
                if source and canonicalize_url(source.url) == canonicalize_url(candidate_url):
                    return 1.0
        
        # Title similarity
        candidate_title = candidate.get("title", "") or candidate.get("actor1", "")
        event_title = event.title
        if candidate_title and event_title:
            title_score = fuzz.ratio(candidate_title.lower(), event_title.lower()) / 100.0
            scores.append(title_score * 0.3)
        
        # Entity overlap (actors)
        candidate_actors = set()
        for key in ["actor1", "actor2"]:
            if candidate.get(key):
                candidate_actors.add(candidate[key].lower())
        
        event_actors = set()
        # Extract actors from event title/description
        for word in event.title.lower().split():
            if len(word) > 3:
                event_actors.add(word)
        
        if candidate_actors and event_actors:
            overlap = len(candidate_actors & event_actors) / max(len(candidate_actors), len(event_actors))
            scores.append(overlap * 0.25)
        
        # Location proximity
        cand_lat = candidate.get("latitude")
        cand_lon = candidate.get("longitude")
        event_lat = event.latitude
        event_lon = event.longitude
        
        if all(v is not None for v in [cand_lat, cand_lon, event_lat, event_lon]):
            distance = self._haversine(cand_lat, cand_lon, event_lat, event_lon)
            if distance <= self.location_threshold_km:
                loc_score = 1.0 - (distance / self.location_threshold_km)
                scores.append(loc_score * 0.25)
        
        # Time proximity
        cand_date = candidate.get("date")
        event_date = event.start_time
        
        if cand_date and event_date:
            if isinstance(cand_date, str):
                cand_date = datetime.fromisoformat(cand_date.replace("Z", "+00:00"))
            if isinstance(event_date, str):
                event_date = datetime.fromisoformat(event_date.replace("Z", "+00:00"))
            
            time_diff = abs((cand_date - event_date).total_seconds()) / 3600
            if time_diff <= self.time_window_hours:
                time_score = 1.0 - (time_diff / self.time_window_hours)
                scores.append(time_score * 0.2)
        
        return sum(scores) if scores else 0.0

    def _haversine(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two points in km."""
        from math import radians, sin, cos, sqrt, atan2
        R = 6371
        lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        return R * c

    def _compute_candidate_similarity(self, a: Dict[str, Any], b: Dict[str, Any]) -> float:
        """Compute similarity between two candidate dictionaries."""
        scores = []
        
        # URL canonicalization match (exact)
        url_a = canonicalize_url(a.get("source_url", ""))
        url_b = canonicalize_url(b.get("source_url", ""))
        if url_a and url_b and url_a == url_b:
            return 1.0
        
        # Title similarity
        title_a = a.get("title", "") or a.get("actor1", "")
        title_b = b.get("title", "") or b.get("actor1", "")
        if title_a and title_b:
            title_score = fuzz.ratio(title_a.lower(), title_b.lower()) / 100.0
            scores.append(title_score * 0.3)
        
        # Entity overlap (actors)
        actors_a = set()
        for key in ["actor1", "actor2"]:
            if a.get(key):
                actors_a.add(a[key].lower())
        
        actors_b = set()
        for key in ["actor1", "actor2"]:
            if b.get(key):
                actors_b.add(b[key].lower())
        
        if actors_a and actors_b:
            overlap = len(actors_a & actors_b) / max(len(actors_a), len(actors_b))
            scores.append(overlap * 0.25)
        
        # Location proximity
        lat_a, lon_a = a.get("latitude"), a.get("longitude")
        lat_b, lon_b = b.get("latitude"), b.get("longitude")
        
        if all(v is not None for v in [lat_a, lon_a, lat_b, lon_b]):
            distance = self._haversine(lat_a, lon_a, lat_b, lon_b)
            if distance <= self.location_threshold_km:
                loc_score = 1.0 - (distance / self.location_threshold_km)
                scores.append(loc_score * 0.25)
        
        # Time proximity
        date_a = a.get("date")
        date_b = b.get("date")
        
        if date_a and date_b:
            if isinstance(date_a, str):
                date_a = datetime.fromisoformat(date_a.replace("Z", "+00:00"))
            if isinstance(date_b, str):
                date_b = datetime.fromisoformat(date_b.replace("Z", "+00:00"))
            
            time_diff = abs((date_a - date_b).total_seconds()) / 3600
            if time_diff <= self.time_window_hours:
                time_score = 1.0 - (time_diff / self.time_window_hours)
                scores.append(time_score * 0.2)
        
        return sum(scores) if scores else 0.0

    def cluster_candidates(self, candidates: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Group candidates into clusters representing the same event."""
        if not candidates:
            return []
        
        # Use the more sophisticated similarity computation for clustering
        clusters = []
        used = set()
        
        for i, candidate in enumerate(candidates):
            if i in used:
                continue
            
            cluster = [candidate]
            used.add(i)
            
            for j, other in enumerate(candidates):
                if j in used or i == j:
                    continue
                
                # Use the full similarity computation
                score = self._compute_candidate_similarity(candidate, other)
                if score > 0.55:  # Threshold for clustering (lowered to account for missing actor data)
                    cluster.append(other)
                    used.add(j)
            
            clusters.append(cluster)
        
        return clusters

    def _are_same_event(self, a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        """Check if two candidates represent the same event."""
        # Exact URL match
        url_a = canonicalize_url(a.get("source_url", ""))
        url_b = canonicalize_url(b.get("source_url", ""))
        if url_a and url_b and url_a == url_b:
            return True
        
        # Title similarity
        title_a = a.get("title", "") or a.get("actor1", "")
        title_b = b.get("title", "") or b.get("actor1", "")
        if title_a and title_b:
            if fuzz.ratio(title_a.lower(), title_b.lower()) > self.title_similarity_threshold:
                # Check location and time
                if self._check_spatiotemporal(a, b):
                    return True
        
        return False

    def _check_spatiotemporal(self, a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        """Check if two candidates are close in space and time."""
        # Location
        lat_a, lon_a = a.get("latitude"), a.get("longitude")
        lat_b, lon_b = b.get("latitude"), b.get("longitude")
        
        if all(v is not None for v in [lat_a, lon_a, lat_b, lon_b]):
            distance = self._haversine(lat_a, lon_a, lat_b, lon_b)
            if distance > self.location_threshold_km:
                return False
        
        # Time
        date_a = a.get("date")
        date_b = b.get("date")
        
        if date_a and date_b:
            if isinstance(date_a, str):
                date_a = datetime.fromisoformat(date_a.replace("Z", "+00:00"))
            if isinstance(date_b, str):
                date_b = datetime.fromisoformat(date_b.replace("Z", "+00:00"))
            
            time_diff = abs((date_a - date_b).total_seconds()) / 3600
            if time_diff > self.time_window_hours:
                return False
        
        return True

    def merge_cluster(self, cluster: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge a cluster of candidates into a single event representation."""
        if not cluster:
            return {}
        
        # Use highest priority source as base
        cluster.sort(key=lambda x: x.get("priority", 0), reverse=True)
        base = cluster[0].copy()
        
        # Collect all sources
        all_sources = []
        all_urls = []
        for item in cluster:
            if item.get("source_url"):
                all_urls.append(item["source_url"])
                all_sources.append({
                    "url": item["source_url"],
                    "title": item.get("title", ""),
                    "publisher": item.get("publisher", "Unknown"),
                })
        
        base["source_urls"] = all_urls
        base["sources"] = all_sources
        base["source_count"] = len(all_sources)
        
        # Merge location (use most specific)
        locations = [(c.get("latitude"), c.get("longitude")) for c in cluster if c.get("latitude") and c.get("longitude")]
        if locations:
            base["latitude"], base["longitude"] = locations[0]
        
        # Merge actors
        actors = set()
        for item in cluster:
            for key in ["actor1", "actor2"]:
                if item.get(key):
                    actors.add(item[key])
        base["actors"] = list(actors)
        
        # Use earliest date
        dates = [c.get("date") for c in cluster if c.get("date")]
        if dates:
            base["date"] = min(dates, key=lambda d: d if isinstance(d, datetime) else datetime.max)
        
        return base
    
    def resolve_event_identity(self, event: Event) -> EventIdentity:
        """Resolve the identity of an event using the EventIdentityResolver."""
        return self.identity_resolver.resolve_event_identity(event)
    
    def find_or_create_event(self, candidate: Dict[str, Any]) -> Tuple[Event, EventIdentity]:
        """
        Find an existing event matching the candidate, or create a new one.
        
        Returns:
            Tuple of (Event, EventIdentity) where Event is the existing or new event,
            and EventIdentity contains the identity resolution details.
        """
        # First, check for exact URL match. This is source-record deduplication,
        # not a claim that matching URLs are the only way to identify an event.
        candidate_url = candidate.get("source_url", "")
        if candidate_url:
            for event in self.event_store.get_all():
                for source_id in event.source_ids:
                    source = self.source_store.get(source_id)
                    if source and canonicalize_url(source.url) == canonicalize_url(candidate.get("source_url", "")):
                        return event, self.identity_resolver.resolve_event_identity(event)
        
        # Use identity resolver to find or create event
        candidate_event = Event(
            id="candidate",
            event_type=EventType(candidate.get("event_type", "PROTEST")),
            title=candidate.get("title", ""),
            slug="",
            description=candidate.get("title", ""),
            summary=candidate.get("title", ""),
            country_code=candidate.get("country_code", "XX"),
            country_name=candidate.get("country_name", "Unknown"),
            city=candidate.get("city"),
            latitude=candidate.get("latitude"),
            longitude=candidate.get("longitude"),
            start_time=candidate.get("date", datetime.utcnow()),
            severity=candidate.get("severity", 5),
            status=EventStatus.DEVELOPING,
            confidence=ConfidenceLevel.MEDIUM,
            source_ids=[candidate.get("source_id", "")],
        )
        
        identity = self.identity_resolver.resolve_event_identity(candidate_event)
        if identity.matched_with:
            existing = self.event_store.get(identity.matched_with[0])
            if existing:
                return existing, identity
        
        # Create new event
        event = Event(
            id=str(uuid4()),
            canonical_id=identity.canonical_id,
            event_type=EventType(candidate.get("event_type", "PROTEST")),
            title=candidate.get("title", ""),
            slug="",
            description=candidate.get("title", ""),
            summary=candidate.get("title", ""),
            country_code=candidate.get("country_code", "XX"),
            country_name=candidate.get("country_name", "Unknown"),
            city=candidate.get("city"),
            latitude=candidate.get("latitude"),
            longitude=candidate.get("longitude"),
            start_time=candidate.get("date", datetime.utcnow()),
            severity=candidate.get("severity", 5),
            status=EventStatus.DEVELOPING,
            confidence=ConfidenceLevel.MEDIUM,
            source_ids=[candidate.get("source_id", "")],
        )
        
        return event, identity


def deduplicate_sources(sources: List[Source]) -> List[Source]:
    """Remove duplicate sources based on URL and content hash."""
    seen_urls = set()
    seen_hashes = set()
    unique = []
    
    for source in sources:
        canon_url = canonicalize_url(str(source.url))
        if canon_url in seen_urls:
            continue
        if source.article_hash and source.article_hash in seen_hashes:
            continue
        
        seen_urls.add(canon_url)
        if source.article_hash:
            seen_hashes.add(source.article_hash)
        unique.append(source)
    
    return unique

import pytest
import tempfile
import shutil
import os
import glob
from datetime import datetime
import uuid

from app.models.schemas import (
    Event, Source, Claim, Location, Country, Conflict, IngestionRun,
    EventType, EventStatus, ConfidenceLevel, VerificationStatus, SourceType, ClaimType, LocationPrecision
)
from app.datastore import JSONDataStore, get_data_store, EventStore, SourceStore, ClaimStore
from app.utils import canonicalize_url, compute_article_hash, slugify, format_range, format_number
from app.clustering.cluster import EventClusterer
from app.scoring.score import EventScorer, decide_publish
from app.extraction.gemini import ExtractionResult, ExtractedClaim, validate_extraction_result, claims_to_model_claims


@pytest.fixture
def temp_data_dir():
    """Create a temporary data directory for testing."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def data_store(temp_data_dir):
    """Create a JSONDataStore with temporary directory."""
    from pathlib import Path
    return JSONDataStore(Path(temp_data_dir))


@pytest.fixture
def event_store(data_store):
    return EventStore(data_store)


@pytest.fixture
def source_store(data_store):
    return SourceStore(data_store)


@pytest.fixture
def claim_store(data_store):
    return ClaimStore(data_store)


# ===== Unit Tests for Utils =====

class TestUtils:
    def test_canonicalize_url(self):
        url = "https://www.example.com/path?utm_source=test&ref=fbclid"
        canonical = canonicalize_url(url)
        assert "utm_source" not in canonical
        assert "fbclid" not in canonical
        assert canonical == "https://example.com/path"
    
    def test_canonicalize_url_httpurl(self):
        from pydantic import HttpUrl
        url = HttpUrl("https://www.example.com/path?utm_source=test")
        canonical = canonicalize_url(url)
        assert "utm_source" not in canonical
    
    def test_compute_article_hash(self):
        hash1 = compute_article_hash("Title", "https://example.com/1")
        hash2 = compute_article_hash("Title", "https://example.com/1")
        assert hash1 == hash2
        
        hash3 = compute_article_hash("Different", "https://example.com/1")
        assert hash1 != hash3
    
    def test_slugify(self):
        assert slugify("Hello World") == "hello-world"
        assert slugify("Test Event With Special Chars!") == "test-event-with-special-chars"
        assert len(slugify("a" * 100)) == 80
    
    def test_format_range(self):
        assert format_range(5, 10) == "5–10"
        assert format_range(5, 5) == "5"
        assert format_range(5, None) == "at least 5"
        assert format_range(None, 10) == "up to 10"
        assert format_range(None, None) == "—"
    
    def test_format_number(self):
        assert format_number(500) == "500"
        assert format_number(1500) == "1.5K"
        assert format_number(1500000) == "1.5M"
        assert format_number(None) == "—"


# ===== Unit Tests for Models =====

class TestModels:
    def test_event_creation(self):
        event = Event(
            id=str(uuid.uuid4()),
            event_type=EventType.PROTEST,
            title="Test Protest",
            slug="test-protest",
            description="A test protest",
            summary="Summary",
            country_code="US",
            country_name="United States",
            start_time=datetime.utcnow(),
            severity=5,
            status=EventStatus.DEVELOPING,
            confidence=ConfidenceLevel.MEDIUM,
        )
        assert event.event_type == EventType.PROTEST
        assert event.get_death_range() is None
    
    def test_event_death_range(self):
        event = Event(
            id=str(uuid.uuid4()),
            event_type=EventType.CASUALTY,
            title="Test",
            slug="test",
            description="Test",
            summary="Test",
            country_code="US",
            country_name="United States",
            start_time=datetime.utcnow(),
            deaths_min=10,
            deaths_max=20,
        )
        assert event.get_death_range() == "10–20"
        
        event.deaths_min = 15
        event.deaths_max = 15
        assert event.get_death_range() == "15"
        
        event.deaths_max = None
        assert event.get_death_range() == "at least 15"
    
    def test_source_creation(self):
        source = Source(
            id=str(uuid.uuid4()),
            publisher="Test Publisher",
            title="Test Article",
            url="https://example.com/article",
            published_at=datetime.utcnow(),
            source_type=SourceType.NEWS,
        )
        assert source.publisher == "Test Publisher"
    
    def test_claim_creation(self):
        claim = Claim(
            id=str(uuid.uuid4()),
            event_id="event-123",
            claim_type=ClaimType.CASUALTY,
            subject="civilians",
            predicate="killed",
            min_value=10,
            max_value=20,
            unit="people",
            source_id="source-123",
            source_quote="10-20 civilians killed",
            confidence=0.8,
            verification_status=VerificationStatus.REPORTED,
        )
        assert claim.claim_type == ClaimType.CASUALTY


# ===== Unit Tests for Datastore =====

class TestDatastore:
    def test_event_store_crud(self, event_store):
        event = Event(
            id=str(uuid.uuid4()),
            event_type=EventType.PROTEST,
            title="Test Protest",
            slug="test-protest",
            description="Test",
            summary="Test",
            country_code="US",
            country_name="United States",
            start_time=datetime.utcnow(),
        )
        
        # Create
        created = event_store.create(event)
        assert created.id == event.id
        
        # Get
        retrieved = event_store.get(event.id)
        assert retrieved.id == event.id
        assert retrieved.title == "Test Protest"
        
        # Update
        updated = event_store.update(event.id, {"title": "Updated Title"})
        assert updated.title == "Updated Title"
        
        # Get by slug
        by_slug = event_store.get_by_slug("test-protest")
        assert by_slug is not None
        
        # Get by country
        by_country = event_store.get_by_country("US")
        assert len(by_country) == 1
        
        # Delete
        deleted = event_store.delete(event.id)
        assert deleted is True
        assert event_store.get(event.id) is None
    
    def test_source_store_crud(self, source_store):
        source = Source(
            id=str(uuid.uuid4()),
            publisher="Test",
            title="Test",
            url="https://example.com",
            published_at=datetime.utcnow(),
        )
        
        source_store.create(source)
        retrieved = source_store.get(source.id)
        assert retrieved.publisher == "Test"
        
        by_url = source_store.get_by_url("https://example.com")
        assert by_url is not None
    
    def test_claim_store(self, claim_store):
        claim = Claim(
            id=str(uuid.uuid4()),
            event_id="event-123",
            claim_type=ClaimType.CASUALTY,
            subject="civilians",
            predicate="killed",
            min_value=10,
            max_value=20,
            unit="people",
            source_id="source-123",
            source_quote="10-20 killed",
            confidence=0.8,
        )
        
        claim_store.create(claim)
        retrieved = claim_store.get(claim.id)
        assert retrieved.subject == "civilians"
        
        by_event = claim_store.get_by_event("event-123")
        assert len(by_event) == 1


# ===== Unit Tests for Clustering =====

class TestClustering:
    def test_cluster_same_location_time(self):
        clusterer = EventClusterer()
        
        c1 = {'source_url': 'http://a.com', 'title': 'Protest in Paris', 'latitude': 48.8566, 'longitude': 2.3522, 'date': datetime(2026, 10, 8, 10, 0)}
        c2 = {'source_url': 'http://b.com', 'title': 'Paris demonstrations', 'latitude': 48.8566, 'longitude': 2.3522, 'date': datetime(2026, 10, 8, 11, 0)}
        c3 = {'source_url': 'http://c.com', 'title': 'Protest in London', 'latitude': 51.5074, 'longitude': -0.1278, 'date': datetime(2026, 10, 8, 10, 0)}
        
        clusters = clusterer.cluster_candidates([c1, c2, c3])
        assert len(clusters) == 2
        assert len(clusters[0]) == 2  # Paris cluster
        assert len(clusters[1]) == 1  # London
    
    def test_find_similar_events(self, event_store, source_store):
        # Create a clusterer that uses the test stores
        class TestClusterer(EventClusterer):
            def __init__(self, test_event_store, test_source_store):
                self.event_store = test_event_store
                self.source_store = test_source_store
                self.title_similarity_threshold = 80
                self.entity_overlap_threshold = 0.5
                self.location_threshold_km = 50
                self.time_window_hours = 48
        
        clusterer = TestClusterer(event_store, source_store)
        
        # Create two similar events
        source1 = Source(id=str(uuid.uuid4()), publisher="A", title="Protest in Paris", url="http://a.com", published_at=datetime(2026, 10, 8, 10, 0))
        source2 = Source(id=str(uuid.uuid4()), publisher="B", title="Paris protests", url="http://b.com", published_at=datetime(2026, 10, 8, 11, 0))
        source_store.create(source1)
        source_store.create(source2)
        
        event1 = Event(id=str(uuid.uuid4()), event_type=EventType.PROTEST, title="Protest in Paris", slug="p1", description="d", summary="s", country_code="FR", country_name="France", city="Paris", latitude=48.8566, longitude=2.3522, start_time=datetime(2026, 10, 8, 10, 0), source_ids=[source1.id])
        event2 = Event(id=str(uuid.uuid4()), event_type=EventType.PROTEST, title="Paris demonstrations", slug="p2", description="d", summary="s", country_code="FR", country_name="France", city="Paris", latitude=48.8566, longitude=2.3522, start_time=datetime(2026, 10, 8, 11, 0), source_ids=[source2.id])
        event_store.create(event1)
        event_store.create(event2)
        
        candidate = {'source_url': 'http://a.com', 'title': 'Protest in Paris', 'latitude': 48.8566, 'longitude': 2.3522, 'date': datetime(2026, 10, 8, 10, 0)}
        similar = clusterer.find_similar_events(candidate)
        assert len(similar) == 2
        assert similar[0][1] == 1.0  # Exact URL match
    
    def test_different_events_not_clustered(self):
        clusterer = EventClusterer()
        
        c1 = {'source_url': 'http://a.com', 'title': 'Protest in Paris', 'latitude': 48.8566, 'longitude': 2.3522, 'date': datetime(2026, 10, 8, 10, 0)}
        c2 = {'source_url': 'http://b.com', 'title': 'Earthquake in Tokyo', 'latitude': 35.6762, 'longitude': 139.6503, 'date': datetime(2026, 10, 8, 10, 0)}
        
        clusters = clusterer.cluster_candidates([c1, c2])
        assert len(clusters) == 2


# ===== Unit Tests for Scoring =====

class TestScoring:
    def test_conflict_high_score(self, event_store, source_store):
        scorer = EventScorer()
        
        # Create a conflict event with multiple sources
        sources = []
        for i in range(3):
            src = Source(id=str(uuid.uuid4()), publisher=f"Pub{i}", title=f"Title{i}", url=f"http://example.com/{i}", published_at=datetime.utcnow(), source_type=SourceType.NEWS)
            source_store.create(src)
            sources.append(src)
        
        event = Event(
            id=str(uuid.uuid4()),
            event_type=EventType.CONFLICT,
            title="Heavy fighting in Ukraine",
            slug="ukraine-conflict",
            description="Heavy fighting",
            summary="Heavy fighting in Ukraine",
            country_code="UA",
            country_name="Ukraine",
            city="Donetsk",
            latitude=48.0,
            longitude=37.8,
            start_time=datetime.utcnow(),
            severity=8,
            status=EventStatus.DEVELOPING,
            confidence=ConfidenceLevel.HIGH,
            deaths_min=50,
            deaths_max=70,
            injured_min=100,
            injured_max=150,
            source_ids=[s.id for s in sources],
        )
        event_store.create(event)
        
        scores = scorer.score_event(event, sources)
        assert scores["final"] > 50
        decision = decide_publish(event, scores)
        assert decision in ["AUTO_PUBLISH", "AUTO_PUBLISH_WARNING"]
    
    def test_protest_low_score_single_source(self, event_store, source_store):
        scorer = EventScorer()
        
        src = Source(id=str(uuid.uuid4()), publisher="Pub", title="Title", url="http://example.com", published_at=datetime.utcnow(), source_type=SourceType.NEWS)
        source_store.create(src)
        
        event = Event(
            id=str(uuid.uuid4()),
            event_type=EventType.PROTEST,
            title="Small protest",
            slug="small-protest",
            description="Small protest",
            summary="Small protest",
            country_code="US",
            country_name="United States",
            city="New York",
            start_time=datetime.utcnow(),
            severity=3,
            status=EventStatus.DEVELOPING,
            confidence=ConfidenceLevel.LOW,
            source_ids=[src.id],
        )
        event_store.create(event)
        
        scores = scorer.score_event(event, [src])
        assert scores["final"] < 50
        decision = decide_publish(event, scores)
        assert decision == "HOLD"
    
    def test_penalties_applied(self, event_store, source_store):
        scorer = EventScorer()
        
        src = Source(id=str(uuid.uuid4()), publisher="Pub", title="Title", url="http://example.com", published_at=datetime.utcnow(), source_type=SourceType.NEWS)
        source_store.create(src)
        
        # Old event with low confidence
        event = Event(
            id=str(uuid.uuid4()),
            event_type=EventType.PROTEST,
            title="Old protest",
            slug="old-protest",
            description="Old",
            summary="Old",
            country_code="US",
            country_name="United States",
            start_time=datetime.utcnow(),
            severity=3,
            status=EventStatus.DEVELOPING,
            confidence=ConfidenceLevel.LOW,
            source_ids=[src.id],
        )
        event_store.create(event)
        
        scores = scorer.score_event(event, [src])
        assert scores["penalties"] > 0


# ===== Unit Tests for Extraction Validation =====

class TestExtraction:
    def test_valid_extraction(self):
        result = ExtractionResult(
            is_event=True,
            event_type="PROTEST",
            title="Protest in Paris",
            country="France",
            city="Paris",
            date="2026-10-08",
            claims=[
                ExtractedClaim(type="participants", subject="protesters", predicate="count", min_value=10000, max_value=20000, unit="people", source_quote="10,000-20,000 protesters", confidence=0.8)
            ],
            importance_score=75,
            confidence_score=80,
            publish_recommendation="PUBLISH"
        )
        assert validate_extraction_result(result) is True
    
    def test_invalid_no_event_type(self):
        result = ExtractionResult(is_event=True, title="Test", country="France")
        assert validate_extraction_result(result) is False
    
    def test_invalid_not_event(self):
        result = ExtractionResult(is_event=False)
        assert validate_extraction_result(result) is False
    
    def test_claims_conversion(self):
        claims = [
            ExtractedClaim(type="participants", subject="protesters", predicate="count", min_value=10000, max_value=20000, unit="people", source_quote="10,000-20,000", confidence=0.8),
            ExtractedClaim(type="casualty", subject="civilians", predicate="injured", value=14, unit="people", source_quote="14 injured", confidence=0.9),
        ]
        
        model_claims = claims_to_model_claims(claims, "event-123", "source-456")
        assert len(model_claims) == 2
        assert model_claims[0]["claim_type"] == ClaimType.PARTICIPANTS
        assert model_claims[0]["min_value"] == 10000
        assert model_claims[1]["claim_type"] == ClaimType.CASUALTY
        assert model_claims[1]["value"] == 14


# ===== Integration Tests =====

class TestIntegration:
    def test_full_pipeline_mock(self, event_store, source_store, claim_store, isolated_publication_paths):
        """Test the full pipeline with mock data."""
        import os, shutil
        # Clear global data directories
        for d in ['data/events', 'data/sources', 'data/claims']:
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)
        for d in ['web/public/data/events', 'web/public/data/map', 'web/public/data/stats', 'web/public/data/briefs', 'web/public/data/timelines', 'web/public/data/countries']:
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)
        
        # 1. Create sources
        sources = []
        for i, (pub, title) in enumerate([
            ("Reuters", "Heavy fighting in eastern Ukraine"),
            ("AP", "Russian forces advance in Donetsk"),
            ("BBC", "Ukraine conflict escalates in Donbas"),
        ]):
            src = Source(
                id=str(uuid.uuid4()),
                publisher=pub,
                title=title,
                url=f"https://example.com/{i}",
                published_at=datetime(2026, 10, 8, 10, 0),
                source_type=SourceType.NEWS,
                country="UA",
            )
            source_store.create(src)
            sources.append(src)
        
        # 2. Create event
        event = Event(
            id=str(uuid.uuid4()),
            event_type=EventType.CONFLICT,
            title="Heavy fighting in eastern Ukraine",
            slug="",
            description="Heavy fighting in Donetsk region",
            summary="Russian and Ukrainian forces clash in Donetsk. Artillery exchanges reported. 50-70 killed, 120-180 injured.",
            country_code="UA",
            country_name="Ukraine",
            city="Donetsk",
            latitude=48.0078,
            longitude=37.8032,
            start_time=datetime(2026, 10, 8, 10, 0),
            severity=8,
            status=EventStatus.DEVELOPING,
            confidence=ConfidenceLevel.HIGH,
            deaths_min=50,
            deaths_max=70,
            injured_min=120,
            injured_max=180,
            source_ids=[s.id for s in sources],
        )
        event_store.create(event)
        
        # 3. Score and publish - use test stores
        from app.publishing.publisher import EventPublisher
        publisher = EventPublisher(event_store=event_store, source_store=source_store, claim_store=claim_store)
        result = publisher.publish_event(event)
        
        assert result["published"] is True
        assert result["decision"] in ["AUTO_PUBLISH", "AUTO_PUBLISH_WARNING"]
        
        # 4. Check generated files
        import json, os
        assert os.path.exists("web/public/data/events/2026_10_08_heavy-fighting-in-eastern-ukraine.json")
        
        with open("web/public/data/events/2026_10_08_heavy-fighting-in-eastern-ukraine.json", encoding="utf-8") as f:
            event_data = json.load(f)
        
        assert event_data["title"] == event.title
        assert event_data["human_impact"]["deaths"] == format_range(50, 70)
        assert len(event_data["sources"]) == 3
    
    def test_conflict_resolution_conflicting_claims(self, event_store, source_store, claim_store, isolated_publication_paths):
        """Test that conflicting casualty reports are preserved."""
        import os, shutil
        # Clear global data directories
        for d in ['data/events', 'data/sources', 'data/claims']:
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)
        for d in ['web/public/data/events', 'web/public/data/map', 'web/public/data/stats', 'web/public/data/briefs', 'web/public/data/timelines', 'web/public/data/countries']:
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)
        
        # Create sources
        sources = []
        for i, pub in enumerate(["Reuters", "AP", "BBC"]):
            src = Source(
                id=str(uuid.uuid4()),
                publisher=pub,
                title=f"Ukraine conflict update ({pub})",
                url=f"https://example.com/{i}",
                published_at=datetime(2026, 10, 8, 10, 0),
                source_type=SourceType.NEWS,
                country="UA",
            )
            source_store.create(src)
            sources.append(src)
        
        # Create event
        event = Event(
            id=str(uuid.uuid4()),
            event_type=EventType.CONFLICT,
            title="Heavy fighting in eastern Ukraine",
            slug="",
            description="Heavy fighting in Donetsk",
            summary="Russian and Ukrainian forces clash",
            country_code="UA",
            country_name="Ukraine",
            city="Donetsk",
            latitude=48.0078,
            longitude=37.8032,
            start_time=datetime(2026, 10, 8, 10, 0),
            severity=8,
            status=EventStatus.DEVELOPING,
            confidence=ConfidenceLevel.HIGH,
            deaths_min=50,
            deaths_max=70,
            injured_min=100,
            injured_max=150,
            source_ids=[s.id for s in sources],
        )
        event_store.create(event)
        
        # Add conflicting claims
        claims = [
            Claim(event_id=event.id, claim_type=ClaimType.CASUALTY, subject="civilians", predicate="killed", value=55, unit="people", source_id=sources[0].id, source_quote="55 killed", confidence=0.8, verification_status=VerificationStatus.REPORTED),
            Claim(event_id=event.id, claim_type=ClaimType.CASUALTY, subject="civilians", predicate="killed", value=50, unit="people", source_id=sources[1].id, source_quote="50 killed", confidence=0.8, verification_status=VerificationStatus.REPORTED),
            Claim(event_id=event.id, claim_type=ClaimType.CASUALTY, subject="civilians", predicate="killed", value=65, unit="people", source_id=sources[2].id, source_quote="65 killed", confidence=0.7, verification_status=VerificationStatus.REPORTED),
        ]
        claim_store.bulk_create(claims)
        
        # Publish - use test stores
        from app.publishing.publisher import EventPublisher
        publisher = EventPublisher(event_store=event_store, source_store=source_store, claim_store=claim_store)
        result = publisher.publish_event(event)
        
        assert result["published"] is True
        
        # Check claims are preserved
        import json
        slug = event.slug.replace("/", "_")
        with open(f"web/public/data/events/{slug}.json", encoding="utf-8") as f:
            event_data = json.load(f)
        
        claims_out = event_data["claims"].get("casualty", [])
        assert len(claims_out) == 3
        values = [c["value"] for c in claims_out]
        assert 50 in values and 55 in values and 65 in values
        
        # Check uncertain section mentions conflicting reports
        uncertain = event_data["uncertain"]
        assert any("civilians killed" in u for u in uncertain)
    
    def test_idempotent_ingestion(self, event_store, source_store):
        """Test that re-ingestion with same canonical_id updates rather than duplicates."""
        src = Source(
            id=str(uuid.uuid4()),
            publisher="Reuters",
            title="Test Event",
            url="https://reuters.com/test",
            published_at=datetime(2026, 10, 8, 10, 0),
            source_type=SourceType.NEWS,
            country="US",
        )
        source_store.create(src)
        
        canonical_id = "canonical-test-123"
        
        # First ingestion
        event1 = Event(
            id=str(uuid.uuid4()),
            canonical_id=canonical_id,
            event_type=EventType.PROTEST,
            title="Test Event",
            slug="",
            description="Test",
            summary="Test",
            country_code="US",
            country_name="United States",
            start_time=datetime(2026, 10, 8, 10, 0),
            severity=5,
            deaths_min=5,
            deaths_max=10,
            source_ids=[src.id],
        )
        event_store.create(event1)
        
        # Second ingestion - same canonical_id, updated numbers
        existing = event_store.get_by_canonical_id(canonical_id)
        assert existing is not None
        
        event_store.update(existing.id, {
            "deaths_min": 8,
            "deaths_max": 12,
            "summary": "Updated test",
            "last_updated_at": datetime.utcnow().isoformat(),
        })
        
        # Verify only one event with this canonical_id
        all_events = event_store.get_all()
        canonical_ids = [e.canonical_id for e in all_events if e.canonical_id]
        assert len(canonical_ids) == len(set(canonical_ids))
        
        # Verify updated
        updated = event_store.get(existing.id)
        assert updated.deaths_min == 8
        assert updated.deaths_max == 12
        assert updated.publication_status == "discovered"  # Default status


# ===== Publication Boundary Tests =====

class TestPublicationBoundary:
    """Tests to verify HELD/REJECTED events never leak into public outputs."""

    def test_held_event_excluded_from_public_outputs(self, event_store, source_store, claim_store, isolated_publication_paths):
        """Events with HOLD decision must not appear in any public artifact."""
        import os, shutil, json, glob
        # Clear global data directories
        for d in ['data/events', 'data/sources', 'data/claims']:
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)
        for d in ['web/public/data/events', 'web/public/data/map', 'web/public/data/stats', 'web/public/data/briefs', 'web/public/data/timelines', 'web/public/data/countries']:
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)

        # Create a low-score event that will be HELD
        src = Source(
            id=str(uuid.uuid4()),
            publisher="Test Publisher",
            title="Minor Local Protest",
            url="https://example.com/protest",
            published_at=datetime(2026, 10, 8, 10, 0),
            source_type=SourceType.NEWS,
            country="US",
        )
        source_store.create(src)

        event = Event(
            id=str(uuid.uuid4()),
            event_type=EventType.PROTEST,
            title="Minor Local Protest",
            slug="",
            description="Small protest",
            summary="A small protest with few participants",
            country_code="US",
            country_name="United States",
            city="Test City",
            latitude=40.0,
            longitude=-74.0,
            start_time=datetime(2026, 10, 8, 10, 0),
            severity=3,
            status=EventStatus.DEVELOPING,
            confidence=ConfidenceLevel.LOW,
            deaths_min=0,
            deaths_max=0,
            source_ids=[src.id],
        )
        event_store.create(event)

        # Publish - should be HELD
        from app.publishing.publisher import EventPublisher
        publisher = EventPublisher(event_store=event_store, source_store=source_store, claim_store=claim_store)
        result = publisher.publish_event(event)

        assert result["published"] is False
        assert result["decision"] == "HOLD"
        assert result["publication_status"] == "held"

        # Verify NO public artifacts were created for this event
        event_files = glob.glob("web/public/data/events/????_??_??_*.json")
        assert len(event_files) == 0, f"Expected 0 event files, got {len(event_files)}: {event_files}"

        # Verify map GeoJSON doesn't contain this event
        map_file = "web/public/data/map/events.geojson"
        if os.path.exists(map_file):
            with open(map_file, "r", encoding="utf-8") as f:
                geojson = json.load(f)
            feature_ids = [f["properties"]["id"] for f in geojson.get("features", [])]
            assert event.id not in feature_ids, "HELD event found in map GeoJSON"

        # Verify stats don't count this event
        stats_file = "web/public/data/stats/global.json"
        if os.path.exists(stats_file):
            with open(stats_file, "r", encoding="utf-8") as f:
                stats = json.load(f)
            assert stats["total_events"] == 0, f"Stats should be 0, got {stats['total_events']}"

    def test_auto_publish_event_included_in_public_outputs(self, event_store, source_store, claim_store, isolated_publication_paths):
        """Events with AUTO_PUBLISH decision must appear in public artifacts."""
        import os, shutil, json, glob
        # Clear global data directories
        for d in ['data/events', 'data/sources', 'data/claims']:
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)
        for d in ['web/public/data/events', 'web/public/data/map', 'web/public/data/stats', 'web/public/data/briefs', 'web/public/data/timelines', 'web/public/data/countries']:
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)

        # Create high-score event that will be AUTO_PUBLISH
        sources = []
        for i, (pub, title) in enumerate([
            ("Reuters", "Major Conflict Escalation"),
            ("AP", "Heavy Fighting Reported"),
            ("BBC", "Conflict Intensifies in Region"),
        ]):
            src = Source(
                id=str(uuid.uuid4()),
                publisher=pub,
                title=title,
                url=f"https://example.com/{i}",
                published_at=datetime(2026, 10, 8, 10, 0),
                source_type=SourceType.NEWS,
                country="UA",
            )
            source_store.create(src)
            sources.append(src)

        event = Event(
            id=str(uuid.uuid4()),
            event_type=EventType.CONFLICT,
            title="Major Conflict Escalation",
            slug="",
            description="Major conflict with high casualties",
            summary="Heavy fighting with significant casualties reported by multiple sources",
            country_code="UA",
            country_name="Ukraine",
            city="Kyiv",
            latitude=50.45,
            longitude=30.52,
            start_time=datetime(2026, 10, 8, 10, 0),
            severity=9,
            status=EventStatus.DEVELOPING,
            confidence=ConfidenceLevel.HIGH,
            deaths_min=100,
            deaths_max=200,
            injured_min=300,
            injured_max=500,
            source_ids=[s.id for s in sources],
        )
        event_store.create(event)

        # Publish - should be AUTO_PUBLISH
        from app.publishing.publisher import EventPublisher
        publisher = EventPublisher(event_store=event_store, source_store=source_store, claim_store=claim_store)
        result = publisher.publish_event(event)

        assert result["published"] is True
        assert result["decision"] in ["AUTO_PUBLISH", "AUTO_PUBLISH_WARNING"]
        assert result["publication_status"] in ["published", "published_with_warning"]

        # Verify event page was created
        event_files = glob.glob("web/public/data/events/????_??_??_*.json")
        assert len(event_files) >= 1, f"Expected at least 1 event file, got {len(event_files)}"

        # Verify event appears in map GeoJSON
        map_file = "web/public/data/map/events.geojson"
        assert os.path.exists(map_file), "Map GeoJSON not created"
        with open(map_file, "r", encoding="utf-8") as f:
            geojson = json.load(f)
        feature_ids = [f["properties"]["id"] for f in geojson.get("features", [])]
        assert event.id in feature_ids, "Published event not found in map GeoJSON"

        # Verify stats count this event
        stats_file = "web/public/data/stats/global.json"
        assert os.path.exists(stats_file), "Global stats not created"
        with open(stats_file, "r", encoding="utf-8") as f:
            stats = json.load(f)
        assert stats["total_events"] >= 1, f"Stats should count published events, got {stats['total_events']}"

        # Verify country index includes this event
        country_file = "web/public/data/countries/ua.json"
        assert os.path.exists(country_file), "Country index not created"
        with open(country_file, "r", encoding="utf-8") as f:
            country_data = json.load(f)
        assert country_data["event_count"] >= 1, f"Country index should count event, got {country_data['event_count']}"

    def test_published_with_warning_event_shows_uncertainty(self, event_store, source_store, claim_store, isolated_publication_paths):
        """Events with AUTO_PUBLISH_WARNING must show uncertainty in public output."""
        import os, shutil, json, glob
        # Clear global data directories
        for d in ['data/events', 'data/sources', 'data/claims']:
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)
        for d in ['web/public/data/events', 'web/public/data/map', 'web/public/data/stats', 'web/public/data/briefs', 'web/public/data/timelines', 'web/public/data/countries']:
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)

        # Create event that scores in warning range
        sources = []
        for i, pub in enumerate(["Reuters", "AP", "BBC"]):
            src = Source(
                id=str(uuid.uuid4()),
                publisher=pub,
                title=f"Ukraine Conflict Update ({pub})",
                url=f"https://example.com/{i}",
                published_at=datetime(2026, 10, 8, 10, 0),
                source_type=SourceType.NEWS,
                country="UA",
            )
            source_store.create(src)
            sources.append(src)

        event = Event(
            id=str(uuid.uuid4()),
            event_type=EventType.CONFLICT,
            title="Escalating Conflict in Eastern Ukraine",
            slug="",
            description="Fighting reported",
            summary="Clashes with moderate casualties",
            country_code="UA",
            country_name="Ukraine",
            city="Donetsk",
            latitude=48.0,
            longitude=37.8,
            start_time=datetime(2026, 10, 8, 10, 0),
            severity=8,
            status=EventStatus.DEVELOPING,
            confidence=ConfidenceLevel.HIGH,
            deaths_min=50,
            deaths_max=80,
            injured_min=100,
            injured_max=150,
            source_ids=[s.id for s in sources],
        )
        event_store.create(event)

        # Publish - should be AUTO_PUBLISH_WARNING
        from app.publishing.publisher import EventPublisher
        publisher = EventPublisher(event_store=event_store, source_store=source_store, claim_store=claim_store)
        result = publisher.publish_event(event)

        assert result["published"] is True
        assert result["publication_status"] == "published_with_warning"

        # Verify event page has publish_warning flag
        event_files = glob.glob("web/public/data/events/????_??_??_*.json")
        assert len(event_files) >= 1
        with open(event_files[0], "r", encoding="utf-8") as f:
            event_data = json.load(f)
        assert event_data.get("publish_warning") is True, "Published with warning event should have publish_warning=True"
        assert event_data.get("publication_status") == "published_with_warning"

    def test_held_events_do_not_affect_public_counters(self, event_store, source_store, claim_store, isolated_publication_paths):
        """HELD events must not increment public counters or summaries."""
        import os, shutil, json
        # Clear global data directories
        for d in ['data/events', 'data/sources', 'data/claims']:
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)
        for d in ['web/public/data/events', 'web/public/data/map', 'web/public/data/stats', 'web/public/data/briefs', 'web/public/data/timelines', 'web/public/data/countries']:
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)

        # Create 3 HELD events (low score)
        for i in range(3):
            src = Source(
                id=str(uuid.uuid4()),
                publisher=f"Publisher {i}",
                title=f"Minor Event {i}",
                url=f"https://example.com/{i}",
                published_at=datetime(2026, 10, 8, 10, 0),
                source_type=SourceType.NEWS,
                country="FR",
            )
            source_store.create(src)

            event = Event(
                id=str(uuid.uuid4()),
                event_type=EventType.PROTEST,
                title=f"Minor Protest {i}",
                slug="",
                description="Small protest",
                summary="Very small protest",
                country_code="FR",
                country_name="France",
                start_time=datetime(2026, 10, 8, 10, 0),
                severity=2,
                status=EventStatus.DEVELOPING,
                confidence=ConfidenceLevel.LOW,
                deaths_min=0,
                deaths_max=0,
                source_ids=[src.id],
            )
            event_store.create(event)

            from app.publishing.publisher import EventPublisher
            publisher = EventPublisher(event_store=event_store, source_store=source_store, claim_store=claim_store)
            result = publisher.publish_event(event)
            assert result["published"] is False
            assert result["decision"] == "HOLD"

        # Create 1 PUBLISHED event
        pub_sources = []
        for i, pub in enumerate(["Reuters", "AP", "BBC"]):
            src = Source(
                id=str(uuid.uuid4()),
                publisher=pub,
                title=f"Major Conflict Report {i}",
                url=f"https://example.com/major/{i}",
                published_at=datetime(2026, 10, 8, 10, 0),
                source_type=SourceType.NEWS,
                country="DE",
            )
            source_store.create(src)
            pub_sources.append(src)

        pub_event = Event(
            id=str(uuid.uuid4()),
            event_type=EventType.CONFLICT,
            title="Major Conflict in Germany",
            slug="",
            description="Major conflict",
            summary="Major conflict with high casualties reported by multiple sources",
            country_code="DE",
            country_name="Germany",
            city="Berlin",
            latitude=52.52,
            longitude=13.4,
            start_time=datetime(2026, 10, 8, 10, 0),
            severity=8,
            status=EventStatus.DEVELOPING,
            confidence=ConfidenceLevel.HIGH,
            deaths_min=50,
            deaths_max=100,
            injured_min=100,
            injured_max=200,
            source_ids=[s.id for s in pub_sources],
        )
        event_store.create(pub_event)

        from app.publishing.publisher import EventPublisher
        publisher = EventPublisher(event_store=event_store, source_store=source_store, claim_store=claim_store)
        result = publisher.publish_event(pub_event)
        assert result["published"] is True

        # Verify stats only count the 1 published event
        stats_file = "web/public/data/stats/global.json"
        with open(stats_file, "r", encoding="utf-8") as f:
            stats = json.load(f)
        assert stats["total_events"] == 1, f"Stats should only count 1 published event, got {stats['total_events']}"

        # Verify daily brief only includes the published event
        brief_file = "web/public/data/briefs/daily_brief_2026-10-08.json"
        if os.path.exists(brief_file):
            with open(brief_file, "r", encoding="utf-8") as f:
                brief = json.load(f)
            # Brief should only contain the published event
            assert len(brief.get("items", [])) <= 1


class TestIdempotencyAndRevisions:
    """Tests for idempotency and publication revision handling."""

    def test_idempotent_ingestion_updates_not_duplicates(self, event_store, source_store, claim_store, isolated_publication_paths):
        """Re-ingestion with same canonical_id updates existing event, no duplicates."""
        import os, shutil
        # Clear global data directories
        for d in ['data/events', 'data/sources', 'data/claims']:
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)
        for d in ['web/public/data/events', 'web/public/data/map', 'web/public/data/stats', 'web/public/data/briefs', 'web/public/data/timelines', 'web/public/data/countries']:
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)

        # Create sources
        sources = []
        for i, pub in enumerate(["Reuters", "AP", "BBC"]):
            src = Source(
                id=str(uuid.uuid4()),
                publisher=pub,
                title=f"Test Event {pub}",
                url=f"https://{pub.lower()}.com/test",
                published_at=datetime(2026, 10, 8, 10, 0),
                source_type=SourceType.NEWS,
                country="US",
            )
            source_store.create(src)
            sources.append(src)

        canonical_id = "canonical-test-123"

        # First ingestion (no canonical_id - let publisher generate it)
        event1 = Event(
            id=str(uuid.uuid4()),
            canonical_id=None,
            event_type=EventType.CONFLICT,
            title="Test Event",
            slug="",
            description="Test",
            summary="Test",
            country_code="US",
            country_name="United States",
            start_time=datetime(2026, 10, 8, 10, 0),
            severity=9,
            deaths_min=100,
            deaths_max=200,
            injured_min=200,
            injured_max=400,
            source_ids=[s.id for s in sources],
            confidence=ConfidenceLevel.HIGH,
        )
        event_store.create(event1)

        # Publish first version
        from app.publishing.publisher import EventPublisher
        publisher = EventPublisher(event_store=event_store, source_store=source_store, claim_store=claim_store)
        result1 = publisher.publish_event(event1)
        assert result1["published"] is True

        # Get the canonical_id that was generated
        published_event = event_store.get(event1.id)
        canonical_id = published_event.canonical_id
        assert canonical_id is not None

        # Second ingestion - same canonical_id, updated numbers
        existing = event_store.get_by_canonical_id(canonical_id)
        assert existing is not None

        event_store.update(existing.id, {
            "deaths_min": 150,
            "deaths_max": 250,
            "summary": "Updated test with higher casualties",
            "last_updated_at": datetime.utcnow().isoformat(),
        })

        # Publish updated version
        updated_event = event_store.get(existing.id)
        result2 = publisher.publish_event(updated_event)
        assert result2["published"] is True

        # Verify only one event with this canonical_id
        all_events = event_store.get_all()
        canonical_ids = [e.canonical_id for e in all_events if e.canonical_id]
        assert len(canonical_ids) == len(set(canonical_ids)), "Duplicate canonical_ids found"

        # Verify updated
        updated = event_store.get(existing.id)
        assert updated.deaths_min == 150
        assert updated.deaths_max == 250
        assert updated.publication_status in ["published", "published_with_warning"]

    @pytest.mark.parametrize("publication_status", ["held", "rejected"])
    def test_publication_status_change_updates_all_artifacts(
        self, event_store, source_store, claim_store, isolated_publication_paths, publication_status
    ):
        """When publication status changes, all public artifacts update consistently."""
        import os, shutil, json
        # Clear global data directories
        for d in ['data/events', 'data/sources', 'data/claims']:
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)
        for d in ['web/public/data/events', 'web/public/data/map', 'web/public/data/stats', 'web/public/data/briefs', 'web/public/data/timelines', 'web/public/data/countries']:
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)

        # Create source and event
        sources = []
        for i, pub in enumerate(["Reuters", "AP", "BBC"]):
            src = Source(
                id=str(uuid.uuid4()),
                publisher=pub,
                title=f"Test Event {pub}",
                url=f"https://{pub.lower()}.com/test",
                published_at=datetime(2026, 10, 8, 10, 0),
                source_type=SourceType.NEWS,
                country="US",
            )
            source_store.create(src)
            sources.append(src)

        event = Event(
            id=str(uuid.uuid4()),
            event_type=EventType.CONFLICT,
            title="Test Conflict",
            slug="",
            description="Test",
            summary="Test conflict",
            country_code="US",
            country_name="United States",
            city="Test City",
            latitude=40.0,
            longitude=-74.0,
            start_time=datetime(2026, 10, 8, 10, 0),
            severity=8,
            status=EventStatus.DEVELOPING,
            confidence=ConfidenceLevel.HIGH,
            deaths_min=50,
            deaths_max=100,
            source_ids=[s.id for s in sources],
        )
        event_store.create(event)

        # Publish - should be PUBLISHED or PUBLISHED_WITH_WARNING
        from app.publishing.publisher import EventPublisher
        publisher = EventPublisher(event_store=event_store, source_store=source_store, claim_store=claim_store)
        result = publisher.publish_event(event)
        assert result["published"] is True
        assert result["publication_status"] in ["published", "published_with_warning"]

        # Verify event page exists
        event_files = glob.glob("web/public/data/events/????_??_??_*.json")
        assert len(event_files) == 1
        with open(event_files[0], "r", encoding="utf-8") as f:
            event_data = json.load(f)
        assert event_data["publication_status"] in ["published", "published_with_warning"]
        with open("web/public/data/events/all.json", "r", encoding="utf-8") as f:
            indexed_events = json.load(f)
        indexed_event = next(item for item in indexed_events if item["id"] == event.id)
        assert indexed_event["publication_status"] == result["publication_status"]

        # Verify map GeoJSON has the event
        map_file = "web/public/data/map/events.geojson"
        with open(map_file, "r", encoding="utf-8") as f:
            geojson = json.load(f)
        feature_ids = [f["properties"]["id"] for f in geojson.get("features", [])]
        assert event.id in feature_ids

        # Now simulate status change to HELD (e.g., new evidence contradicts)
        event_store.update(event.id, {"publication_status": publication_status, "visibility": False})

        # Re-publish should not create new artifacts for HELD event
        updated_event = event_store.get(event.id)
        result = publisher.publish_event(updated_event)
        assert result["published"] is False
        assert result["decision"] == publication_status.upper()
        assert result["publication_status"] == publication_status

        # A status change must remove every generated public reference to the event.
        assert not os.path.exists(event_files[0])
        stats_file = "web/public/data/stats/global.json"
        with open(stats_file, "r", encoding="utf-8") as f:
            stats = json.load(f)
        assert stats["total_events"] == 0, f"Stats should be 0 after event held, got {stats['total_events']}"
        with open("web/public/data/events/all.json", "r", encoding="utf-8") as f:
            assert json.load(f) == []
        with open(map_file, "r", encoding="utf-8") as f:
            assert json.load(f)["features"] == []
        assert not os.path.exists("web/public/data/countries/us.json")
        assert not os.path.exists("web/public/data/timelines/us.json")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import app.datastore
from app.cli import main as cli_main
from app.datastore import JSONDataStore, SourceStore
from app.extraction.gemini import ExtractionOutcome, ExtractionResult
from app.models.schemas import Source


@pytest.mark.parametrize(
    ("extraction", "expected_event_count"),
    [
        (ExtractionResult(is_event=False), 0),
        (
            ExtractionResult(
                is_event=True,
                event_type="CONFLICT",
                title="Reported clash",
                country="US",
                date="2026-10-17",
                confidence_score=80,
                summary="Extracted summary",
            ),
            1,
        ),
    ],
)
def test_run_validates_extraction_and_preserves_discovery_result(
    monkeypatch, capsys, extraction, expected_event_count
):
    monkeypatch.setattr(cli_main.get_settings(), "enable_autopublish", True)
    source = SimpleNamespace(
        id="source-1",
        publisher="Example News",
        title="A report",
        content="Article content",
        url="https://example.test/report",
        country="US",
        published_at=None,
    )

    class SourceStore:
        def get_all(self):
            return [source]

        def get_by_event(self, event_id):
            return [source]

        def update(self, source_id, updates):
            for key, value in updates.items():
                setattr(source, key, value)
            return source

    class EventStore:
        def __init__(self):
            self.created = []

        def get_all(self):
            return self.created

        def create(self, event):
            self.created.append(event)
            return event

        def get(self, event_id):
            return next((event for event in self.created if event.id == event_id), None)

        def update(self, event_id, updates):
            return None

    event_store = EventStore()

    class Extractor:
        enabled = True

        async def extract(self, title, content, url):
            return extraction

    class Clusterer:
        def find_similar_events(self, candidate):
            return []

    class Publisher:
        def __init__(self):
            self.published = []
            self.brief_generated = False

        def publish_event(self, event):
            self.published.append(event)
            return {"published": False}

        def generate_daily_brief(self):
            self.brief_generated = True

    publisher = Publisher()

    async def discover(hours):
        return {
            "gdelt_events": 0,
            "rss_articles_fetched": 1,
            "sources_created": 1,
            "run_id": "test-run-id",
        }

    monkeypatch.setattr(cli_main, "run_discovery", discover)
    monkeypatch.setattr(cli_main, "GeminiExtractor", Extractor)
    monkeypatch.setattr(cli_main, "get_source_store", lambda: SourceStore())
    monkeypatch.setattr(cli_main, "get_event_store", lambda: event_store)
    monkeypatch.setattr(app.datastore, "get_claim_store", lambda: SimpleNamespace(bulk_create=lambda claims: None))
    monkeypatch.setattr(cli_main, "EventClusterer", Clusterer)
    monkeypatch.setattr(cli_main, "EventPublisher", lambda: publisher)

    cli_main.run(hours=1, extract_limit=50)

    output = capsys.readouterr().out
    assert "Run ID: test-run-id" in output
    assert len(event_store.created) == expected_event_count
    assert len(publisher.published) == expected_event_count
    assert publisher.brief_generated is False
    if expected_event_count:
        assert event_store.created[0].summary == "Extracted summary"
    else:
        assert "Extraction not persisted" in output


def test_extraction_identity_attaches_reports_and_preserves_claim_sources():
    class EventStore:
        def __init__(self):
            self.events = []

        def get_all(self):
            return list(self.events)

        def get(self, event_id):
            return next((event for event in self.events if event.id == event_id), None)

        def create(self, event):
            self.events.append(event)
            return event

        def update(self, event_id, updates):
            event = self.get(event_id)
            if event:
                for key, value in updates.items():
                    setattr(event, key, value)
            return event

    class ClaimStore:
        def __init__(self):
            self.claims = []

        def bulk_create(self, claims):
            self.claims.extend(claims)

    event_store = EventStore()
    claim_store = ClaimStore()
    source_records = []
    extraction_results = []
    articles = [
        (
            "source-1",
            "Protesters gather in Central City after new restrictions",
            "Protesters gather in Central City after new restrictions.",
            "Central City",
            40.0,
            -73.0,
        ),
        (
            "source-2",
            "Protesters gather in Central City after new restrictions",
            "Independent report: Protesters gather in Central City after new restrictions.",
            "Central City",
            40.0,
            -73.0,
        ),
        (
            "source-3",
            "Workers demonstrate outside the port in nearby South City",
            "Workers demonstrate outside the port in nearby South City.",
            "South City",
            40.05,
            -73.0,
        ),
    ]

    results = []
    for source_id, title, content, city, latitude, longitude in articles:
        source = Source(
            id=source_id,
            publisher=f"Publisher {source_id}",
            title=title,
            url=f"https://{source_id}.example/report",
            country="US",
            city=city,
            content=content,
        )
        result = ExtractionResult(
            is_event=True,
            event_type="PROTEST",
            title=title,
            country="US",
            city=city,
            latitude=latitude,
            longitude=longitude,
            date="2026-10-17",
            confidence_score=80,
            claims=[
                {
                    "type": "cause",
                    "subject": "protesters",
                    "predicate": "responded to",
                    "source_quote": content,
                },
                {
                    "type": "casualty",
                    "subject": "people",
                    "predicate": "injured",
                    "value": 99,
                    "source_quote": "This unsupported quotation was not in the article.",
                },
            ],
        )
        source_records.append(source)
        extraction_results.append(result)
        results.append(
            cli_main._persist_extracted_event(source, result, event_store, claim_store)
        )

    repeated, repeated_is_new, repeated_claims = cli_main._persist_extracted_event(
        source_records[0], extraction_results[0], event_store, claim_store
    )

    assert len(event_store.events) == 2
    assert results[0][1] is True
    assert results[1][1] is False
    assert results[2][1] is True
    assert repeated.id == results[0][0].id
    assert repeated_is_new is False
    assert repeated_claims == []
    assert len(claim_store.claims) == 3
    protest_event = event_store.get(results[0][0].id)
    assert protest_event.source_ids == ["source-1", "source-2"]
    assert results[2][0].canonical_id != protest_event.canonical_id
    assert {claim.source_id for claim in claim_store.claims} == {
        "source-1",
        "source-2",
        "source-3",
    }
    assert all(
        claim.source_quote != "This unsupported quotation was not in the article."
        for claim in claim_store.claims
    )
    assert all(
        claim.event_id in {event.id for event in event_store.events}
        for claim in claim_store.claims
    )


def test_source_extraction_progress_prevents_repeated_api_spend(tmp_path):
    source_store = SourceStore(JSONDataStore(tmp_path))
    source = Source(
        id="retry-source",
        publisher="Example News",
        title="A reported event",
        url="https://example.test/retry",
        content="Article text",
    )
    attached_source = Source(
        id="attached-source",
        publisher="Example News",
        title="Already processed event",
        url="https://example.test/attached",
        content="Article text",
    )
    source_store.create(source)
    source_store.create(attached_source)
    now = datetime.utcnow()
    events = [SimpleNamespace(source_ids=["attached-source"])]

    ready = cli_main._sources_ready_for_extraction(
        source_store.get_all(), events, 10, now=now
    )
    assert [item.id for item in ready] == ["retry-source"]

    cli_main._record_extraction_outcome(
        source,
        ExtractionOutcome(error_category="quota_exhausted", attempts=1),
        source_store,
    )
    retry_source = source_store.get(source.id)
    assert retry_source.extraction_status == "retryable_failure"
    assert cli_main._sources_ready_for_extraction(
        source_store.get_all(), events, 10, now=now
    ) == []

    ready_after_quota_window = cli_main._sources_ready_for_extraction(
        source_store.get_all(), events, 10, now=now + timedelta(hours=25)
    )
    assert [item.id for item in ready_after_quota_window] == ["retry-source"]

    valid_result = ExtractionResult(
        is_event=True,
        event_type="CONFLICT",
        title="A verified event title",
        country="US",
        confidence_score=80,
    )
    cli_main._record_extraction_outcome(
        retry_source,
        ExtractionOutcome(result=valid_result, attempts=1),
        source_store,
    )
    completed_source = source_store.get(source.id)
    assert completed_source.extraction_status == "completed"
    assert cli_main._sources_ready_for_extraction(
        source_store.get_all(), events, 10, now=now + timedelta(days=2)
    ) == []

from types import SimpleNamespace

import pytest

import app.datastore
from app.cli import main as cli_main
from app.extraction.gemini import ExtractionResult


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

    class EventStore:
        def __init__(self):
            self.created = []

        def get_all(self):
            return self.created

        def create(self, event):
            self.created.append(event)

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
    assert publisher.brief_generated is True
    if expected_event_count:
        assert event_store.created[0].summary == "Extracted summary"
    else:
        assert "Extraction validation failed" in output

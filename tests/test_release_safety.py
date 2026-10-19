import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import app.discovery.manager as discovery_manager
import app.sources.fetcher as source_fetcher
from app.cli import main as cli_main
from app.extraction.gemini import ExtractionResult, GeminiExtractor
from app.models.schemas import Event, Source
from app.sources.fetcher import FetchResult

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIRECTORY = REPOSITORY_ROOT / ".github" / "workflows"


def load_workflow(name: str):
    return yaml.load(
        (WORKFLOW_DIRECTORY / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def test_production_pipeline_requires_release_enable_and_has_no_force_publish():
    workflow = load_workflow("autonomous-pipeline.yml")

    assert [entry["cron"] for entry in workflow["on"]["schedule"]] == [
        "17 * * * *",
        "0 2 * * *",
    ]
    assert (
        workflow["jobs"]["discover-and-publish"]["if"]
        == "vars.PRODUCTION_RELEASE_ENABLED == 'true'"
    )
    assert "force_publish" not in workflow["on"]["workflow_dispatch"]["inputs"]
    assert (
        workflow["jobs"]["discover-and-publish"]["env"]["ENABLE_AUTOPUBLISH"]
        == "${{ vars.ENABLE_AUTOPUBLISH || 'false' }}"
    )
    assert workflow["jobs"]["discover-and-publish"]["env"]["PRODUCTION_MODE"] == "true"
    assert (
        workflow["jobs"]["discover-and-publish"]["env"]["MAX_LLM_CALLS_PER_RUN"]
        == "25"
    )
    assert (
        workflow["jobs"]["discover-and-publish"]["env"]["MAX_ARTICLES_PER_RUN"]
        == "500"
    )
    assert workflow["jobs"]["discover-and-publish"]["timeout-minutes"] == "30"
    assert workflow["jobs"]["discover-and-publish"]["permissions"] == {
        "contents": "write"
    }
    steps = workflow["jobs"]["discover-and-publish"]["steps"]
    assert any("git push" in step.get("run", "") for step in steps)
    assert not any(
        "upload-pages-artifact" in step.get("uses", "") for step in steps
    )
    assert "deploy-site" not in workflow["jobs"]
    persistence = next(
        step["run"] for step in steps if "git add -- data/" in step.get("run", "")
    )
    assert "web/public/data/" in persistence
    assert "git add -f -- data/.gemini-key-rotation.json" in persistence
    assert "pages" not in workflow["jobs"]["discover-and-publish"]["permissions"]
    assert "id-token" not in workflow["jobs"]["discover-and-publish"]["permissions"]

    ignore_rules = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/.gemini-key-rotation.json" in ignore_rules


def test_pages_deploy_requires_explicit_manual_confirmation():
    workflow = load_workflow("deploy-pages.yml")

    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["concurrency"]["group"] == "humanity-pages-deploy"
    assert (
        workflow["on"]["workflow_dispatch"]["inputs"]["confirm_deployment"]["default"]
        == "no"
    )
    assert workflow["jobs"]["build"]["if"] == "inputs.confirm_deployment == 'yes'"
    assert workflow["jobs"]["deploy"]["needs"] == "build"


def test_smoke_workflow_is_manual_read_only_and_has_no_deploy_or_persistence():
    workflow = load_workflow("live-source-smoke.yml")

    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["verify"]
    assert job["permissions"] == {"contents": "read"}
    assert job["env"]["ENABLE_AUTOPUBLISH"] == "false"
    assert "GEMINI_API_KEYS" in job["env"]
    assert "GEMINI_API_KEY" not in job["env"]
    assert job["env"]["MAX_LLM_CALLS_PER_RUN"] == "${{ inputs.extract_limit }}"
    assert (
        workflow["on"]["workflow_dispatch"]["inputs"]["extract_limit"]["default"]
        == "25"
    )
    workflow_text = (
        (WORKFLOW_DIRECTORY / "live-source-smoke.yml")
        .read_text(encoding="utf-8")
        .lower()
    )
    assert "git push" not in workflow_text
    assert "git commit" not in workflow_text
    assert "deploy-pages" not in workflow_text
    assert "upload-pages-artifact" not in workflow_text


@pytest.mark.parametrize(
    (
        "extraction_result",
        "successful_count",
        "validated_count",
        "failed_count",
        "invalid_count",
    ),
    [
        (
            ExtractionResult(
                is_event=True,
                event_type="CONFLICT",
                title="Reported event",
                country="US",
                confidence_score=85,
                claims=[
                    {
                        "type": "casualty",
                        "subject": "civilians",
                        "predicate": "injured",
                        "value": 1,
                        "unit": "person",
                        "source_quote": "Article text that must not be printed.",
                        "confidence": 0.9,
                    }
                ],
            ),
            1,
            1,
            0,
            0,
        ),
        (
            ExtractionResult(
                is_event=True, event_type="INVALID", title="Invalid", country="US"
            ),
            1,
            0,
            0,
            1,
        ),
        (None, 0, 0, 1, 0),
    ],
)
def test_smoke_test_isolated_and_never_calls_publisher(
    monkeypatch,
    tmp_path,
    capsys,
    extraction_result,
    successful_count,
    validated_count,
    failed_count,
    invalid_count,
):
    settings = cli_main.get_settings()
    original_values = (
        settings.data_dir,
        settings.web_dir,
        settings.enable_autopublish,
        settings.production_mode,
        settings.gemini_api_key,
        settings.max_llm_calls_per_run,
    )
    temporary_storage_paths = []
    source = Source(
        publisher="Example source",
        title="Reported event",
        url="https://example.test/report",
        country="US",
        content="Article text that must not be printed.",
    )

    class SourceStore:
        def get_all(self):
            return [source]

        def get_by_event(self, event_id):
            return [source]

    class EventStore:
        def __init__(self):
            self.events = []

        def create(self, event):
            self.events.append(event)
            return event

        def get(self, event_id):
            return next((event for event in self.events if event.id == event_id), None)

        def get_all(self):
            return self.events

        def update(self, event_id, updates):
            return None

        def delete(self, event_id):
            self.events = [event for event in self.events if event.id != event_id]

    class ClaimStore:
        def bulk_create(self, claims):
            pass

        def get_by_event(self, event_id):
            return []

    class Extractor:
        enabled = True

        async def extract(self, title, content, url):
            assert settings.enable_autopublish is False
            assert settings.production_mode is True
            assert settings.data_dir != original_values[0]
            assert settings.web_dir != original_values[1]
            assert content == source.content
            return extraction_result

        def rotation_diagnostics(self):
            return {
                "configured_slot_count": 1,
                "api_calls": 1,
                "call_budget": 25,
                "failover_attempts": 0,
                "failover_recoveries": 0,
                "slots": [
                    {
                        "slot_id": "slot_0123456789abcdef",
                        "attempts": 1,
                        "successful_responses": int(extraction_result is not None),
                        "failure_categories": {},
                        "cooldown_until": None,
                        "last_error_category": None,
                    }
                ],
            }

    async def discover(hours):
        assert hours == 3
        assert settings.enable_autopublish is False
        assert settings.data_dir != original_values[0]
        temporary_storage = Path(settings.data_dir)
        temporary_storage.mkdir(parents=True)
        temporary_storage_paths.append(temporary_storage)
        (temporary_storage / "smoke-only.json").write_text("[]", encoding="utf-8")
        return {
            "rss_articles_fetched": 1,
            "rss_feeds_attempted": 3,
            "rss_feeds_completed": 3,
            "rss_feeds_with_errors": 1,
            "rss_fetch_attempts": 4,
            "rss_fetch_successes": 3,
            "rss_fetch_failures": 1,
            "rss_discovery_errors": 0,
            "gdelt_events": 2,
            "gdelt_articles": 5,
            "gdelt_diagnostics": {
                "events": {
                    "status": "succeeded",
                    "errors": 0,
                    "error_categories": {},
                },
                "gkg": {
                    "status": "failed",
                    "errors": 1,
                    "error_categories": {"HTTPStatusError": 1},
                },
            },
        }

    monkeypatch.setattr(cli_main, "run_discovery", discover)
    monkeypatch.setattr(cli_main, "GeminiExtractor", Extractor)
    monkeypatch.setattr(cli_main, "get_source_store", SourceStore)
    monkeypatch.setattr(cli_main, "get_event_store", EventStore)
    monkeypatch.setattr(cli_main, "EventPublisher", lambda: (_ for _ in ()).throw(AssertionError("publisher used")))
    monkeypatch.setattr(cli_main, "get_settings", lambda: settings)
    import app.datastore
    monkeypatch.setattr(app.datastore, "get_claim_store", lambda: ClaimStore())
    settings.data_dir = tmp_path / "original-data"
    settings.web_dir = tmp_path / "original-web"
    settings.gemini_api_key = "test-key"
    settings.max_llm_calls_per_run = 25

    try:
        cli_main.smoke_test(hours=3, extract_limit=5)
        output = capsys.readouterr().out
        assert "Discovered articles: 1" in output
        assert "Extraction attempts: 1" in output
        assert f"Successful extractions: {successful_count}" in output
        assert f"Failed extractions: {failed_count}" in output
        assert f"Invalid extraction results: {invalid_count}" in output
        assert f"Validated events: {validated_count}" in output
        assert f"Invalid or non-event results: {invalid_count}" in output
        assert "GDELT events: 2 found; status=succeeded; errors=0" in output
        assert "GDELT GKG: 5 found; status=failed; errors=1;" in output
        assert "categories=HTTPStatusError:1" in output
        assert "RSS feeds: 3/3 completed; 1 with errors; discovery errors=0" in output
        assert "RSS fetch results: 3 succeeded, 1 failed of 4 attempts" in output
        assert "Gemini credential slots configured: 1" in output
        assert "Gemini calls spent: 1/25; failover attempts=0; recovered=0" in output
        assert "slot_0123456789abcdef: requests=1;" in output
        if successful_count and validated_count:
            assert "Claims created: 1" in output
            assert "Claims with source attribution: 1" in output
        assert "Publication-eligible candidates: 0" in output
        assert "Eligible candidates retained as unpublished: 0" in output
        if validated_count:
            assert "Insufficient independent sources: 1" in output
            assert "Sanitized per-event diagnostics:" in output
            expected_diagnostic = (
                "sources=1 (independent=0, syndicated=0, unknown=1); "
                "contradictions=0; gate=evidence_hold; "
                "reasons=insufficient_independent_sources"
            )
            assert expected_diagnostic in " ".join(output.split()), output
            assert "Reported event" not in output
        else:
            assert "Sanitized per-event diagnostics:\n    none" in output
        if failed_count:
            assert "Extraction error categories: no_result:1" in output
        assert "Article text that must not be printed" not in output
        assert "Auto-publication: disabled" in output
        assert list(tmp_path.iterdir()) == []
        assert temporary_storage_paths and all(
            not path.exists() for path in temporary_storage_paths
        )
        assert settings.enable_autopublish == original_values[2]
        assert settings.production_mode == original_values[3]
    finally:
        (
            settings.data_dir,
            settings.web_dir,
            settings.enable_autopublish,
            settings.production_mode,
            settings.gemini_api_key,
            settings.max_llm_calls_per_run,
        ) = original_values


def test_smoke_extraction_batch_uses_one_async_event_loop():
    sources = [
        SimpleNamespace(title=f"Story {index}", content="content", url=f"https://example.test/{index}")
        for index in range(2)
    ]

    class LoopBoundExtractor:
        loop = None

        async def extract(self, title, content, url):
            current_loop = asyncio.get_running_loop()
            if self.loop is None:
                self.loop = current_loop
            assert current_loop is self.loop
            return title

    extractor = LoopBoundExtractor()
    results = asyncio.run(cli_main._extract_sources(sources, extractor))

    assert [outcome.result for _, outcome in results] == ["Story 0", "Story 1"]
    assert all(outcome.error_category is None for _, outcome in results)


def test_discovery_returns_separate_rss_and_gdelt_diagnostics(monkeypatch):
    class RunStore:
        def create(self, run):
            return run

        def update(self, run_id, values):
            pass

    class SourceStore:
        def create(self, source):
            raise AssertionError("failed/empty article results must not create sources")

    class GDELT:
        async def close(self):
            pass

    class Fetcher:
        async def close(self):
            pass

    async def gdelt(hours, max_events, max_articles):
        return {
            "events_found": 2,
            "articles_found": 4,
            "sources_created": 6,
            "diagnostics": {
                "events": {"status": "succeeded", "errors": 0},
                "gkg": {"status": "failed", "errors": 1},
            },
        }

    async def rss(sources, max_concurrent, max_articles_per_run):
        assert max_articles_per_run == 100
        return {
            "source-a": [
                FetchResult(success=False, error="HTTP 503"),
                FetchResult(success=True, content=""),
            ],
            "source-b": [],
        }

    monkeypatch.setattr(discovery_manager, "run_gdelt_discovery", gdelt)
    monkeypatch.setattr(discovery_manager, "fetch_from_multiple_sources", rss)
    monkeypatch.setattr(discovery_manager, "SourceFetcher", Fetcher)

    manager = discovery_manager.DiscoveryManager.__new__(discovery_manager.DiscoveryManager)
    manager.settings = SimpleNamespace(max_articles_per_run=100)
    manager.max_articles_per_run = 100
    manager.source_store = SourceStore()
    manager.run_store = RunStore()
    manager.gdelt_discovery = GDELT()
    manager._get_rss_sources = lambda: ["source-a", "source-b"]

    result = asyncio.run(manager.discover_all(hours_back=6))

    assert result["gdelt_events"] == 2
    assert result["gdelt_articles"] == 4
    assert result["gdelt_diagnostics"]["gkg"] == {"status": "failed", "errors": 1}
    assert result["rss_feeds_attempted"] == 2
    assert result["rss_feeds_completed"] == 2
    assert result["rss_feeds_with_errors"] == 1
    assert result["rss_fetch_attempts"] == 2
    assert result["rss_fetch_successes"] == 1
    assert result["rss_fetch_failures"] == 1
    assert result["rss_articles_fetched"] == 0


def test_multi_source_fetch_respects_global_article_budget(monkeypatch):
    requested_limits = []

    class Fetcher:
        async def fetch(self, source, max_records=None):
            requested_limits.append(max_records)
            return []

        async def close(self):
            pass

    monkeypatch.setattr(source_fetcher, "SourceFetcher", Fetcher)
    sources = [SimpleNamespace(name=f"source-{index}") for index in range(3)]

    results = asyncio.run(
        source_fetcher.fetch_from_multiple_sources(
            sources,
            max_concurrent=2,
            max_articles_per_run=5,
        )
    )

    assert set(results) == {source.name for source in sources}
    assert sorted(requested_limits) == [1, 2, 2]
    assert sum(requested_limits) == 5


@pytest.mark.parametrize(
    ("response_text", "error", "expected_category"),
    [
        ("", None, "invalid_model_output"),
        ("not-json", None, "invalid_model_output"),
        ("{}", None, "invalid_model_output"),
        (None, RuntimeError("sensitive provider message"), "provider_error"),
        (
            None,
            type(
                "QuotaError",
                (Exception,),
                {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "quota exceeded: private detail",
                },
            )(),
            "quota_exhausted",
        ),
        (
            None,
            type(
                "ServerError",
                (Exception,),
                {
                    "code": 503,
                    "status": "UNAVAILABLE",
                    "message": "503 service unavailable: private detail",
                },
            )(),
            "provider_server_error",
        ),
    ],
)
def test_gemini_failure_categories_exclude_provider_messages(
    monkeypatch, tmp_path, response_text, error, expected_category
):
    class Models:
        async def generate_content(self, **kwargs):
            if error:
                raise error
            return SimpleNamespace(text=response_text)

    client = SimpleNamespace(aio=SimpleNamespace(models=Models()))
    settings = SimpleNamespace(
        gemini_api_keys="",
        gemini_api_key="test-key",
        gemini_model="test-model",
        max_llm_calls_per_run=3,
        data_dir=tmp_path,
    )
    monkeypatch.setattr("app.extraction.gemini.get_settings", lambda: settings)
    extractor = GeminiExtractor(client=client, sleep=lambda _: asyncio.sleep(0))

    outcome = asyncio.run(
        extractor.extract_with_diagnostics("title", "content", "https://example.test")
    )

    assert outcome.result is None
    assert outcome.error_category == expected_category
    assert expected_category in outcome.attempt_errors or not outcome.attempt_errors
    assert "private detail" not in repr(outcome)


def test_gdelt_http_error_category_includes_only_status_code():
    from app.discovery.gdelt import GDELTDiscovery

    discovery = GDELTDiscovery.__new__(GDELTDiscovery)
    discovery.diagnostics = {
        "gkg": {"status": "not_run", "errors": 0, "error_categories": {}}
    }
    http_error = type("HTTPStatusError", (Exception,), {})
    error = http_error()
    error.response = SimpleNamespace(status_code=403)

    discovery._record_error("gkg", error)

    assert discovery.diagnostics["gkg"]["error_categories"] == {"HTTPStatusError_403": 1}


def test_publisher_refuses_to_publish_when_auto_publication_is_disabled(
    monkeypatch, tmp_path, isolated_publication_paths
):
    from app.config import get_settings
    from app.datastore import ClaimStore, EventStore, JSONDataStore, SourceStore
    from app.models.schemas import EventType
    from app.publishing.publisher import EventPublisher

    settings = get_settings()
    prior_value = settings.enable_autopublish
    monkeypatch.setattr(settings, "enable_autopublish", False)
    store = JSONDataStore(tmp_path)
    events = EventStore(store)
    event = Event(
        event_type=EventType.CONFLICT,
        title="Unpublished test candidate",
        slug="",
        description="Test only",
        summary="Test only",
        country_code="US",
        country_name="United States",
        start_time=datetime.now(),
    )
    events.create(event)
    publisher = EventPublisher(
        event_store=events,
        source_store=SourceStore(store),
        claim_store=ClaimStore(store),
    )

    try:
        result = publisher.publish_event(event)
        assert result["decision"] == "AUTOPUBLISH_DISABLED"
        assert result["published"] is False
        assert events.get(event.id).publication_status.value == "discovered"
        assert not list((tmp_path / "web" / "public" / "data" / "events").glob("*.json"))
    finally:
        settings.enable_autopublish = prior_value

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from app.cli import main as cli_main
from app.models.schemas import Source
from app.extraction.gemini import ExtractionResult


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIRECTORY = REPOSITORY_ROOT / ".github" / "workflows"


def load_workflow(name: str):
    return yaml.load(
        (WORKFLOW_DIRECTORY / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def test_production_pipeline_requires_release_enable_and_has_no_force_publish():
    workflow = load_workflow("autonomous-pipeline.yml")

    assert "schedule" in workflow["on"]
    assert workflow["jobs"]["discover-and-publish"]["if"] == "vars.PRODUCTION_RELEASE_ENABLED == 'true'"
    assert "force_publish" not in workflow["on"]["workflow_dispatch"]["inputs"]
    assert workflow["jobs"]["discover-and-publish"]["env"]["ENABLE_AUTOPUBLISH"] == "${{ vars.ENABLE_AUTOPUBLISH || 'false' }}"
    assert workflow["jobs"]["discover-and-publish"]["env"]["PRODUCTION_MODE"] == "true"
    steps = workflow["jobs"]["discover-and-publish"]["steps"]
    assert any("git push" in step.get("run", "") for step in steps)
    assert any("upload-pages-artifact" in step.get("uses", "") for step in steps)
    assert workflow["jobs"]["deploy-site"]["needs"] == "discover-and-publish"


def test_pages_deploy_requires_explicit_manual_confirmation():
    workflow = load_workflow("deploy-pages.yml")

    assert "push" not in workflow["on"]
    assert workflow["on"]["workflow_dispatch"]["inputs"]["confirm_deployment"]["default"] == "no"
    assert workflow["jobs"]["build"]["if"] == "inputs.confirm_deployment == 'yes'"
    assert workflow["jobs"]["deploy"]["needs"] == "build"


def test_smoke_workflow_is_manual_read_only_and_has_no_deploy_or_persistence():
    workflow = load_workflow("live-source-smoke.yml")

    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["verify"]
    assert job["permissions"] == {"contents": "read"}
    assert job["env"]["ENABLE_AUTOPUBLISH"] == "false"
    assert "GEMINI_API_KEY" in job["env"]
    workflow_text = (WORKFLOW_DIRECTORY / "live-source-smoke.yml").read_text(encoding="utf-8").lower()
    assert "git push" not in workflow_text
    assert "git commit" not in workflow_text
    assert "deploy-pages" not in workflow_text
    assert "upload-pages-artifact" not in workflow_text


@pytest.mark.parametrize(
    ("extraction_result", "successful_count", "validated_count", "failed_count"),
    [
        (
            ExtractionResult(
                is_event=True,
                event_type="CONFLICT",
                title="Reported event",
                country="US",
                confidence_score=85,
            ),
            1,
            1,
            0,
        ),
        (None, 0, 0, 1),
    ],
)
def test_smoke_test_isolated_and_never_calls_publisher(
    monkeypatch, tmp_path, capsys, extraction_result, successful_count, validated_count, failed_count
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

    class EventStore:
        def create(self, event):
            self.event = event

        def get_by_country(self, country_code):
            return []

        def get_by_canonical_id(self, canonical_id):
            return None

    class Extractor:
        enabled = True

        async def extract(self, title, content, url):
            assert settings.enable_autopublish is False
            assert settings.production_mode is True
            assert settings.data_dir != original_values[0]
            assert settings.web_dir != original_values[1]
            assert content == source.content
            return extraction_result

    async def discover(hours):
        assert hours == 3
        assert settings.enable_autopublish is False
        assert settings.data_dir != original_values[0]
        temporary_storage = Path(settings.data_dir)
        temporary_storage.mkdir(parents=True)
        temporary_storage_paths.append(temporary_storage)
        (temporary_storage / "smoke-only.json").write_text("[]", encoding="utf-8")
        return {"rss_articles_fetched": 1, "reliefweb_articles_fetched": 0}

    monkeypatch.setattr(cli_main, "run_discovery", discover)
    monkeypatch.setattr(cli_main, "GeminiExtractor", Extractor)
    monkeypatch.setattr(cli_main, "get_source_store", SourceStore)
    monkeypatch.setattr(cli_main, "get_event_store", EventStore)
    monkeypatch.setattr(cli_main, "EventPublisher", lambda: (_ for _ in ()).throw(AssertionError("publisher used")))
    monkeypatch.setattr(cli_main, "get_settings", lambda: settings)
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
        assert f"Validated events: {validated_count}" in output
        assert "Publication-eligible candidates: 0" in output
        assert "Article text that must not be printed" not in output
        assert "Auto-publication: disabled" in output
        assert list(tmp_path.iterdir()) == []
        assert temporary_storage_paths and all(not path.exists() for path in temporary_storage_paths)
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


def test_publisher_refuses_to_publish_when_auto_publication_is_disabled(
    monkeypatch, tmp_path, isolated_publication_paths
):
    from app.config import get_settings
    from app.datastore import ClaimStore, EventStore, JSONDataStore, SourceStore
    from app.models.schemas import Event, EventType
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

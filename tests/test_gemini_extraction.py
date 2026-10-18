import asyncio
from types import SimpleNamespace

from app.extraction.gemini import ExtractionResult, GeminiExtractor


class FakeModels:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def generate_content(self, **kwargs):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(text=response)


class FakeClient:
    def __init__(self, responses):
        self.models = FakeModels(responses)
        self.aio = SimpleNamespace(models=self.models)


def configured_extractor(monkeypatch, client, max_calls=25):
    settings = SimpleNamespace(
        gemini_api_key="test-key",
        gemini_model="test-model",
        max_llm_calls_per_run=max_calls,
    )
    monkeypatch.setattr("app.extraction.gemini.get_settings", lambda: settings)
    return GeminiExtractor(client=client, sleep=lambda _: asyncio.sleep(0))


def article():
    return {"title": "Headline", "content": "Article text", "url": "https://example.test/story"}


def event_json():
    return ExtractionResult(
        is_event=True,
        event_type="PROTEST",
        title="A reported protest",
        country="US",
        confidence_score=80,
    ).model_dump_json()


def api_error(name, code, status, message, retry_after=None):
    error_type = type(
        name,
        (Exception,),
        {
            "code": code,
            "status": status,
            "message": message,
            "retry_after": retry_after,
        },
    )
    return error_type()


def test_quota_exhaustion_stops_batch_without_spending_remaining_budget(monkeypatch):
    client = FakeClient(
        [api_error("QuotaError", 429, "RESOURCE_EXHAUSTED", "daily quota exceeded")]
    )
    extractor = configured_extractor(monkeypatch, client)

    outcomes = asyncio.run(extractor.extract_many([article(), article(), article()]))

    assert client.models.calls == 1
    assert outcomes[0].error_category == "quota_exhausted"
    assert outcomes[0].attempt_errors == ("quota_exhausted",)
    assert outcomes[0].stop_batch
    assert [outcome.error_category for outcome in outcomes[1:]] == [
        "not_attempted_quota_exhausted",
        "not_attempted_quota_exhausted",
    ]
    assert extractor.api_calls == 1


def test_authentication_failure_stops_batch_without_retrying(monkeypatch):
    client = FakeClient(
        [api_error("AuthError", 401, "UNAUTHENTICATED", "invalid credential")]
    )
    extractor = configured_extractor(monkeypatch, client)

    outcomes = asyncio.run(extractor.extract_many([article(), article()]))

    assert client.models.calls == 1
    assert outcomes[0].error_category == "authentication_failure"
    assert outcomes[0].attempt_errors == ("authentication_failure",)
    assert outcomes[1].error_category == "not_attempted_after_authentication_failure"


def test_transient_rate_limit_retries_are_bounded_and_record_each_attempt(monkeypatch):
    rate_limit = api_error(
        "RateLimitError", 429, "RESOURCE_EXHAUSTED", "requests per minute limit", 0
    )
    client = FakeClient([rate_limit, rate_limit, event_json()])
    extractor = configured_extractor(monkeypatch, client)

    outcomes = asyncio.run(extractor.extract_many([article()]))

    assert client.models.calls == 3
    assert outcomes[0].result is not None
    assert outcomes[0].attempts == 3
    assert outcomes[0].attempt_errors == ("transient_rate_limit", "transient_rate_limit")
    assert extractor.api_calls == 3


def test_network_failures_are_classified_and_have_a_finite_retry_cap(monkeypatch):
    client = FakeClient([TimeoutError("private timeout")] * 3)
    extractor = configured_extractor(monkeypatch, client)

    outcomes = asyncio.run(extractor.extract_many([article(), article()]))

    assert client.models.calls == 3
    assert outcomes[0].error_category == "network_failure"
    assert outcomes[0].attempt_errors == ("network_failure",) * 3
    assert outcomes[1].error_category == "not_attempted_after_network_error"
    assert "private timeout" not in repr(outcomes)


def test_retries_count_toward_the_hard_call_budget(monkeypatch):
    rate_limit = api_error(
        "RateLimitError", 429, "RESOURCE_EXHAUSTED", "requests per minute limit", 0
    )
    client = FakeClient([rate_limit, rate_limit])
    extractor = configured_extractor(monkeypatch, client, max_calls=2)

    outcomes = asyncio.run(extractor.extract_many([article(), article()]))

    assert client.models.calls == extractor.api_calls == 2
    assert outcomes[0].error_category == "call_budget_exhausted"
    assert outcomes[0].attempts == 2
    assert outcomes[1].error_category == "not_attempted_call_budget_exhausted"
    assert outcomes[1].attempts == 0


def test_invalid_output_and_non_event_are_not_reported_as_valid_events(monkeypatch):
    non_event = ExtractionResult(is_event=False).model_dump_json()
    client = FakeClient(["not-json", non_event])
    extractor = configured_extractor(monkeypatch, client)

    outcomes = asyncio.run(extractor.extract_many([article(), article()]))

    assert outcomes[0].result is None
    assert outcomes[0].error_category == "invalid_model_output"
    assert outcomes[1].result is not None
    assert outcomes[1].error_category == "non_event"
    assert outcomes[1].attempts == 1

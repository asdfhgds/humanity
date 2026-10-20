import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from app.extraction.gemini import (
    ExtractionResult,
    GeminiExtractor,
    configured_gemini_keys,
)


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


def configured_extractor(
    monkeypatch,
    client=None,
    max_calls=25,
    *,
    keys=(),
    tmp_path=None,
    clients=None,
    sleep=None,
):
    settings = SimpleNamespace(
        gemini_api_key="",
        gemini_api_keys="\n".join(keys),
        gemini_model="test-model",
        max_llm_calls_per_run=max_calls,
        data_dir=tmp_path or Path("data"),
    )
    if not keys:
        settings.gemini_api_key = "test-key"
    monkeypatch.setattr("app.extraction.gemini.get_settings", lambda: settings)
    clients = clients or {}

    def make_client(key):
        return clients[key]

    return GeminiExtractor(
        client=client,
        client_factory=make_client,
        sleep=sleep or (lambda _: asyncio.sleep(0)),
    )


def article():
    return {
        "title": "Headline",
        "content": "Article text",
        "url": "https://example.test/story",
    }


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


def test_quota_exhaustion_on_first_key_fails_over_to_second_key(monkeypatch, tmp_path):
    client = FakeClient(
        [api_error("QuotaError", 429, "RESOURCE_EXHAUSTED", "daily quota exceeded")]
    )
    second_client = FakeClient([event_json(), event_json(), event_json()])
    extractor = configured_extractor(
        monkeypatch,
        max_calls=5,
        keys=("key-first", "key-second"),
        tmp_path=tmp_path,
        clients={"key-first": client, "key-second": second_client},
    )

    outcomes = asyncio.run(extractor.extract_many([article(), article(), article()]))

    assert client.models.calls == 1
    assert second_client.models.calls == 3
    assert outcomes[0].result is not None
    assert outcomes[0].error_category is None
    assert outcomes[0].attempt_errors == ("quota_exhausted",)
    assert outcomes[0].attempts == 2
    assert outcomes[1].result is not None
    assert outcomes[2].result is not None
    assert extractor.api_calls == 4
    diagnostics = extractor.rotation_diagnostics()
    assert diagnostics["configured_slot_count"] == 2
    assert diagnostics["api_calls"] == 4
    assert diagnostics["call_budget"] == 5
    assert diagnostics["failover_attempts"] == 1
    assert diagnostics["failover_recoveries"] == 1
    assert diagnostics["slots"][0]["failure_categories"] == {
        "quota_exhausted": 1
    }
    assert diagnostics["slots"][0]["successful_responses"] == 0
    assert diagnostics["slots"][1]["successful_responses"] == 3
    assert "key-first" not in json.dumps(diagnostics)
    assert "key-second" not in json.dumps(diagnostics)
    state = json.loads((tmp_path / ".gemini-key-rotation.json").read_text())
    assert "key-first" not in json.dumps(state)
    assert "key-second" not in json.dumps(state)
    assert state["slots"]


def test_resource_exhausted_status_rotates_without_numeric_code(monkeypatch, tmp_path):
    quota_error = type(
        "QuotaError",
        (Exception,),
        {"status": "RESOURCE_EXHAUSTED", "message": "quota exceeded"},
    )()
    first_client = FakeClient([quota_error])
    second_client = FakeClient([event_json()])
    extractor = configured_extractor(
        monkeypatch,
        keys=("key-first", "key-second"),
        tmp_path=tmp_path,
        clients={"key-first": first_client, "key-second": second_client},
    )

    outcome = asyncio.run(
        extractor.extract_with_diagnostics("title", "article", "https://example.test")
    )

    assert outcome.result is not None
    assert outcome.attempt_errors == ("quota_exhausted",)
    assert first_client.models.calls == second_client.models.calls == 1


def test_all_quotas_exhausted_stop_batch_and_defer_remaining_articles(
    monkeypatch, tmp_path
):
    client1 = FakeClient(
        [api_error("QuotaError", 429, "RESOURCE_EXHAUSTED", "daily quota exceeded")]
    )
    client2 = FakeClient(
        [api_error("QuotaError", 429, "RESOURCE_EXHAUSTED", "daily quota exceeded")]
    )
    extractor = configured_extractor(
        monkeypatch,
        max_calls=10,
        keys=("key-one", "key-two"),
        tmp_path=tmp_path,
        clients={"key-one": client1, "key-two": client2},
    )

    outcomes = asyncio.run(extractor.extract_many([article(), article(), article()]))

    assert client1.models.calls == client2.models.calls == 1
    assert extractor.api_calls == 2
    assert outcomes[0].error_category == "all_credentials_unavailable"
    assert outcomes[0].attempt_errors == ("quota_exhausted", "quota_exhausted")
    assert outcomes[0].retry_after_seconds is not None
    assert outcomes[0].stop_batch
    assert outcomes[1].error_category == "not_attempted_all_credentials_unavailable"
    assert outcomes[1].retry_after_seconds == outcomes[0].retry_after_seconds
    assert outcomes[2].attempts == 0


def test_quota_failover_does_not_discard_successful_result_or_duplicate_followup(
    monkeypatch, tmp_path
):
    client1 = FakeClient(
        [api_error("QuotaError", 429, "RESOURCE_EXHAUSTED", "daily quota exceeded")]
    )
    client2 = FakeClient([event_json(), event_json()])
    extractor = configured_extractor(
        monkeypatch,
        keys=("key-one", "key-two"),
        tmp_path=tmp_path,
        clients={"key-one": client1, "key-two": client2},
    )

    first = asyncio.run(extractor.extract_with_diagnostics("title", "text", "url"))
    second = asyncio.run(extractor.extract_with_diagnostics("title", "text", "url"))

    assert first.result is not None
    assert second.result is not None
    assert client1.models.calls == 1
    assert client2.models.calls == 2
    assert extractor.api_calls == 3


def test_authentication_and_permission_failures_rotate_to_another_key(
    monkeypatch, tmp_path
):
    for status_code, status, category in (
        (401, "UNAUTHENTICATED", "authentication_failure"),
        (403, "PERMISSION_DENIED", "permission_failure"),
    ):
        first_client = FakeClient(
            [
                api_error(
                    "CredentialError", status_code, status, "private credential detail"
                )
            ]
        )
        second_client = FakeClient([event_json(), event_json()])
        extractor = configured_extractor(
            monkeypatch,
            keys=("key-first", "key-second"),
            tmp_path=tmp_path / category,
            clients={"key-first": first_client, "key-second": second_client},
        )

        outcome = asyncio.run(
            extractor.extract_with_diagnostics(
                "title", "article body", "https://example.test"
            )
        )

        assert outcome.result is not None
        assert outcome.attempt_errors == (category,)
        assert outcome.attempts == 2
        assert first_client.models.calls == second_client.models.calls == 1
        assert "private credential detail" not in repr(outcome)


def test_rate_limited_credential_is_skipped_during_persisted_cooldown(
    monkeypatch, tmp_path
):
    first_client = FakeClient(
        [
            api_error(
                "RateLimitError",
                429,
                "RESOURCE_EXHAUSTED",
                "requests per minute limit",
                60,
            ),
            event_json(),
        ]
    )
    second_client = FakeClient([event_json(), event_json()])
    keys = ("key-first", "key-second")
    settings = SimpleNamespace(
        gemini_api_key="",
        gemini_api_keys="\n".join(keys),
        gemini_model="test-model",
        max_llm_calls_per_run=5,
        data_dir=tmp_path,
    )
    monkeypatch.setattr("app.extraction.gemini.get_settings", lambda: settings)
    clients = {"key-first": first_client, "key-second": second_client}
    extractor = GeminiExtractor(
        client_factory=clients.__getitem__,
        sleep=lambda _: asyncio.sleep(0),
    )
    outcome = asyncio.run(
        extractor.extract_with_diagnostics("title", "article", "https://example.test")
    )
    next_run = GeminiExtractor(
        client_factory=clients.__getitem__,
        sleep=lambda _: asyncio.sleep(0),
    )
    next_outcome = asyncio.run(
        next_run.extract_with_diagnostics("title", "article", "https://example.test")
    )

    assert outcome.result is not None
    assert outcome.attempt_errors == ("transient_rate_limit",)
    assert first_client.models.calls == 1
    assert next_outcome.result is not None
    assert first_client.models.calls == 1
    assert second_client.models.calls == 2


def test_cooldown_state_survives_extractor_recreation_and_corrupt_state_is_safe(
    monkeypatch, tmp_path
):
    keys = ("key-one", "key-two")
    settings = SimpleNamespace(
        gemini_api_key="",
        gemini_api_keys="\n".join(keys),
        gemini_model="test-model",
        max_llm_calls_per_run=5,
        data_dir=tmp_path,
    )
    monkeypatch.setattr("app.extraction.gemini.get_settings", lambda: settings)
    first = GeminiExtractor(client_factory=lambda _: FakeClient([]))
    first.rotation.mark_unavailable(
        first.rotation.slot_ids[0], "quota_exhausted", first.QUOTA_COOLDOWN_SECONDS
    )

    second = GeminiExtractor(client_factory=lambda _: FakeClient([]))
    assert second.rotation.available_slots() == [second.rotation.slot_ids[1]]
    state_text = (tmp_path / ".gemini-key-rotation.json").read_text()
    assert all(key not in state_text for key in keys)

    (tmp_path / ".gemini-key-rotation.json").write_text("{", encoding="utf-8")
    recovered = GeminiExtractor(client_factory=lambda _: FakeClient([]))
    assert recovered.rotation.available_slots() == list(recovered.rotation.slot_ids)


def test_round_robin_slot_selection_and_key_pool_order(monkeypatch, tmp_path):
    keys = ("first-key", "second-key", "third-key")
    clients = {key: FakeClient([event_json()]) for key in keys}
    extractor = configured_extractor(
        monkeypatch, keys=keys, tmp_path=tmp_path, clients=clients
    )
    outcomes = asyncio.run(extractor.extract_many([article(), article(), article()]))

    assert [client.models.calls for client in clients.values()] == [1, 1, 1]
    assert all(outcome.result is not None for outcome in outcomes)
    assert (
        extractor.rotation.slot_ids[0]
        == "slot_" + hashlib.sha256(b"first-key").hexdigest()[:16]
    )


def test_one_key_legacy_configuration_and_blank_duplicate_pool_entries(
    monkeypatch, tmp_path
):
    settings = SimpleNamespace(
        gemini_api_keys=" \nlegacy-key\n\n",
        gemini_api_key="legacy-key",
    )
    assert configured_gemini_keys(settings) == ("legacy-key",)

    client = FakeClient([event_json()])
    extractor = configured_extractor(
        monkeypatch,
        client=client,
        keys=("legacy-key",),
        tmp_path=tmp_path,
    )
    outcome = asyncio.run(
        extractor.extract_with_diagnostics("title", "article", "https://example.test")
    )
    assert outcome.result is not None
    assert client.models.calls == extractor.api_calls == 1


def test_call_budget_counts_requests_across_keys(monkeypatch, tmp_path):
    first_client = FakeClient(
        [api_error("QuotaError", 429, "RESOURCE_EXHAUSTED", "daily quota exceeded")]
    )
    second_client = FakeClient(
        [
            api_error(
                "RateLimitError",
                429,
                "RESOURCE_EXHAUSTED",
                "requests per minute limit",
                0,
            )
        ]
    )
    extractor = configured_extractor(
        monkeypatch,
        max_calls=2,
        keys=("key-one", "key-two"),
        tmp_path=tmp_path,
        clients={"key-one": first_client, "key-two": second_client},
    )
    outcomes = asyncio.run(extractor.extract_many([article(), article()]))

    assert (
        extractor.api_calls
        == first_client.models.calls + second_client.models.calls
        == 2
    )
    assert outcomes[0].error_category == "call_budget_exhausted"
    assert outcomes[1].error_category == "not_attempted_call_budget_exhausted"


def test_persisted_rotation_state_contains_no_keys_or_provider_messages(
    monkeypatch, tmp_path
):
    secret_key = "not-a-real-key-keep-private"
    provider_message = "sensitive provider detail " + secret_key
    client = FakeClient(
        [api_error("QuotaError", 429, "RESOURCE_EXHAUSTED", provider_message)]
    )
    extractor = configured_extractor(
        monkeypatch, keys=(secret_key,), tmp_path=tmp_path, clients={secret_key: client}
    )
    outcome = asyncio.run(extractor.extract_with_diagnostics("title", "text", "url"))
    state_text = (tmp_path / ".gemini-key-rotation.json").read_text()

    assert secret_key not in repr(outcome)
    assert provider_message not in repr(outcome)
    assert secret_key not in state_text
    assert provider_message not in state_text
    assert outcome.error_category == "quota_exhausted"


def test_no_credentials_reports_disabled_without_creating_clients(
    monkeypatch, tmp_path
):
    settings = SimpleNamespace(
        gemini_api_keys=" \n",
        gemini_api_key="",
        gemini_model="test-model",
        max_llm_calls_per_run=5,
        data_dir=tmp_path,
    )
    monkeypatch.setattr("app.extraction.gemini.get_settings", lambda: settings)
    extractor = GeminiExtractor()

    outcome = asyncio.run(extractor.extract_with_diagnostics("title", "text", "url"))

    assert not extractor.enabled
    assert outcome.error_category == "provider_disabled"
    assert extractor.api_calls == 0


def test_authentication_failure_stops_batch_without_retrying(monkeypatch, tmp_path):
    client = FakeClient(
        [api_error("AuthError", 401, "UNAUTHENTICATED", "invalid credential")]
    )
    extractor = configured_extractor(monkeypatch, client, tmp_path=tmp_path)

    outcomes = asyncio.run(extractor.extract_many([article(), article()]))

    assert client.models.calls == 1
    assert outcomes[0].error_category == "authentication_failure"
    assert outcomes[0].attempt_errors == ("authentication_failure",)
    assert outcomes[1].error_category == "not_attempted_after_authentication_failure"


def test_transient_rate_limit_retries_are_bounded_and_record_each_attempt(
    monkeypatch, tmp_path
):
    rate_limit = api_error(
        "RateLimitError", 429, "RESOURCE_EXHAUSTED", "requests per minute limit", 0
    )
    client = FakeClient([rate_limit, rate_limit, event_json()])
    extractor = configured_extractor(
        monkeypatch, client, keys=("legacy-key",), tmp_path=tmp_path
    )

    outcomes = asyncio.run(extractor.extract_many([article()]))

    assert client.models.calls == 3
    assert outcomes[0].result is not None
    assert outcomes[0].attempts == 3
    assert outcomes[0].attempt_errors == (
        "transient_rate_limit",
        "transient_rate_limit",
    )
    assert extractor.api_calls == 3


def test_network_failures_are_classified_and_have_a_finite_retry_cap(
    monkeypatch, tmp_path
):
    client = FakeClient([TimeoutError("private timeout")] * 3)
    extractor = configured_extractor(monkeypatch, client, tmp_path=tmp_path)

    outcomes = asyncio.run(extractor.extract_many([article(), article()]))

    assert client.models.calls == 3
    assert outcomes[0].error_category == "network_failure"
    assert outcomes[0].attempt_errors == ("network_failure",) * 3
    assert outcomes[1].error_category == "not_attempted_after_network_error"
    assert "private timeout" not in repr(outcomes)


def test_retries_count_toward_the_hard_call_budget(monkeypatch, tmp_path):
    rate_limit = api_error(
        "RateLimitError", 429, "RESOURCE_EXHAUSTED", "requests per minute limit", 0
    )
    client = FakeClient([rate_limit, rate_limit])
    extractor = configured_extractor(
        monkeypatch, client, max_calls=2, tmp_path=tmp_path
    )

    outcomes = asyncio.run(extractor.extract_many([article(), article()]))

    assert client.models.calls == extractor.api_calls == 2
    assert outcomes[0].error_category == "call_budget_exhausted"
    assert outcomes[0].attempts == 2
    assert outcomes[1].error_category == "not_attempted_call_budget_exhausted"
    assert outcomes[1].attempts == 0


def test_invalid_output_and_non_event_are_not_reported_as_valid_events(
    monkeypatch, tmp_path
):
    non_event = ExtractionResult(is_event=False).model_dump_json()
    client = FakeClient(["not-json", non_event])
    extractor = configured_extractor(monkeypatch, client, tmp_path=tmp_path)

    outcomes = asyncio.run(extractor.extract_many([article(), article()]))

    assert outcomes[0].result is None
    assert outcomes[0].error_category == "invalid_model_output"
    assert outcomes[1].result is not None
    assert outcomes[1].error_category == "non_event"
    assert outcomes[1].attempts == 1


def test_invalid_request_is_classified_without_rotating_or_repeating(
    monkeypatch, tmp_path
):
    error = api_error(
        "ClientError", 400, "INVALID_ARGUMENT", "private invalid payload detail"
    )
    first = FakeClient([error])
    second = FakeClient([event_json()])
    extractor = configured_extractor(
        monkeypatch,
        keys=("first-key", "second-key"),
        tmp_path=tmp_path,
        clients={"first-key": first, "second-key": second},
    )

    outcome = asyncio.run(
        extractor.extract_with_diagnostics("title", "article", "https://example.test")
    )

    assert outcome.error_category == "invalid_request"
    assert outcome.stop_batch
    assert first.models.calls == 1
    assert second.models.calls == 0
    assert "private invalid payload detail" not in repr(outcome)


def test_unsupported_model_and_provider_configuration_are_distinct(
    monkeypatch, tmp_path
):
    unsupported = api_error(
        "NotFoundError", 404, "NOT_FOUND", "Model gemini-private is not found"
    )
    extractor = configured_extractor(
        monkeypatch, FakeClient([unsupported]), tmp_path=tmp_path
    )
    unsupported_outcome = asyncio.run(
        extractor.extract_with_diagnostics("title", "article", "https://example.test")
    )
    assert unsupported_outcome.error_category == "unsupported_model"
    assert unsupported_outcome.stop_batch
    assert "gemini-private" not in repr(unsupported_outcome)

    endpoint_error = api_error(
        "NotFoundError", 404, "NOT_FOUND", "private endpoint configuration"
    )
    extractor = configured_extractor(
        monkeypatch, FakeClient([endpoint_error]), tmp_path=tmp_path / "endpoint"
    )
    endpoint_outcome = asyncio.run(
        extractor.extract_with_diagnostics("title", "article", "https://example.test")
    )
    assert endpoint_outcome.error_category == "provider_configuration_error"
    assert endpoint_outcome.stop_batch
    assert "private endpoint configuration" not in repr(endpoint_outcome)


def test_provider_server_error_remains_distinct_and_retryable(monkeypatch, tmp_path):
    error = api_error(
        "ServerError", 503, "UNAVAILABLE", "private provider response"
    )
    first = FakeClient([error])
    second = FakeClient([event_json()])
    extractor = configured_extractor(
        monkeypatch,
        keys=("first-key", "second-key"),
        tmp_path=tmp_path,
        clients={"first-key": first, "second-key": second},
    )

    outcome = asyncio.run(
        extractor.extract_with_diagnostics("title", "article", "https://example.test")
    )

    assert outcome.result is not None
    assert outcome.attempt_errors == ("provider_server_error",)
    assert first.models.calls == second.models.calls == 1
    assert "private provider response" not in repr(outcome)

import asyncio
import socket
import ssl
from types import SimpleNamespace

import httpx
import pytest

from app.diagnostics import classify_request_error, request_error_label
from app.discovery import gdelt as gdelt_module
from app.discovery.manager import DiscoveryManager
from app.sources.fetcher import SourceFetcher


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ReadTimeout("private URL"), "timeout"),
        (
            httpx.ConnectError(
                "sensitive hostname",
                request=httpx.Request("GET", "https://example.test"),
            ),
            "connection_failure",
        ),
        (
            httpx.ConnectError(
                "dns",
                request=httpx.Request("GET", "https://example.test"),
            ),
            "dns_failure",
        ),
    ],
)
def test_request_error_categories_are_safe(error, expected):
    if expected == "dns_failure":
        error.__cause__ = socket.gaierror("private DNS detail")
    assert classify_request_error(error) == expected
    assert "private" not in request_error_label(error)


def test_tls_and_http_error_categories():
    tls_error = httpx.ConnectError(
        "certificate detail",
        request=httpx.Request("GET", "https://example.test"),
    )
    tls_error.__cause__ = ssl.SSLError("private certificate detail")
    assert classify_request_error(tls_error) == "tls_failure"

    response = httpx.Response(
        429,
        request=httpx.Request("GET", "https://example.test"),
    )
    error = httpx.HTTPStatusError("secret response body", request=response.request, response=response)
    assert request_error_label(error) == "rate_limited_429"


def _source_config():
    from app.config import SourceConfig

    return SourceConfig(
        name="Test feed",
        url="https://example.test/feed.xml",
        type="rss",
        country="global",
        language="en",
        priority=1,
        poll_interval_hours=1,
        robots_policy="allow",
        enabled=True,
        reason="fixture",
    )


def _rss_fetcher(monkeypatch, body):
    fetcher = SourceFetcher.__new__(SourceFetcher)
    fetcher.client = SimpleNamespace(
        get=lambda *args, **kwargs: _response(body),
    )
    fetcher.robots_cache = SimpleNamespace(can_fetch=_allow)
    fetcher.request_cache = SimpleNamespace(get=_cache_miss, set=_cache_set)
    fetcher.rate_limiter = SimpleNamespace(acquire=_no_wait)
    fetcher.fetch_article = _article_failure
    return fetcher


async def _allow(*args, **kwargs):
    return True


async def _cache_miss(*args, **kwargs):
    return None


async def _cache_set(*args, **kwargs):
    return None


async def _no_wait(*args, **kwargs):
    return None


async def _article_failure(url):
    return SimpleNamespace(success=False, diagnostic={"status": "timeout"})


async def _response(body):
    return httpx.Response(
        200,
        text=body,
        request=httpx.Request("GET", "https://example.test/feed.xml"),
    )


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        ("<rss><channel><title>empty</title></channel></rss>", "valid_zero_results"),
        ("not an XML feed", "malformed_response"),
        (
            "<rss><channel><title>feed</title><item><title>story</title>"
            "<link>https://publisher.test/story</link></item></channel></rss>",
            "valid_results",
        ),
    ],
)
def test_rss_feed_diagnostics_distinguish_empty_malformed_and_records(
    monkeypatch, body, expected_status
):
    fetcher = _rss_fetcher(monkeypatch, body)
    monkeypatch.setattr(
        fetcher.client,
        "get",
        lambda *args, **kwargs: _response(body),
    )
    results = asyncio.run(fetcher._fetch_rss(_source_config()))
    feed_result = results[0]

    assert feed_result.diagnostic["status"] == expected_status
    assert feed_result.diagnostic["entries_seen"] in (0, 1)
    assert "Traceback" not in repr(feed_result.diagnostic)
    if expected_status == "malformed_response":
        assert feed_result.success is False
        assert feed_result.diagnostic["error_category"] == "malformed_response"
    elif expected_status == "valid_zero_results":
        assert feed_result.success is True


def test_rss_transport_failure_is_structured_and_redacted(monkeypatch):
    fetcher = _rss_fetcher(monkeypatch, "")

    async def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("private URL token")

    fetcher.client.get = timeout

    result = asyncio.run(fetcher._fetch_rss(_source_config()))

    assert result[0].diagnostic == {
        "stage": "feed",
        "status": "timeout",
        "error_category": "timeout",
    }
    assert "private" not in repr(result[0])


def test_gdelt_invalid_dates_are_rejected_not_replaced_with_current_time():
    discovery = gdelt_module.GDELTDiscovery.__new__(gdelt_module.GDELTDiscovery)
    discovery.diagnostics = gdelt_module.GDELTDiscovery._new_diagnostics()
    row = [""] * 60
    row[0] = "id"
    row[1] = "not-a-date"
    row[28] = "18"
    row[58] = "https://example.test/story"

    parsed, status = discovery._parse_gdelt_row_with_status(row)

    assert parsed is None
    assert status == "malformed"
    assert discovery.diagnostics["events"]["records_accepted"] == 0


def test_gdelt_master_index_distinguishes_valid_empty_from_malformed():
    timestamp = gdelt_module.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    valid_index = (
        f"123 abc https://data.gdeltproject.org/gdeltv2/{timestamp}.export.CSV.zip\n"
    )

    assert len(
        gdelt_module._parse_master_index(valid_index, ".export.CSV.zip")
    ) == 1
    assert gdelt_module._parse_master_index(
        "not a valid master index\n", ".export.CSV.zip"
    ) == []
    assert gdelt_module._parse_master_index(
        valid_index, ".gkg.csv.zip"
    ) == []


def test_gdelt_retry_policy_distinguishes_permanent_http_and_honors_retry_after():
    request = httpx.Request("GET", "https://example.test")
    transient_response = httpx.Response(
        503, request=request, headers={"Retry-After": "4"}
    )
    transient = httpx.HTTPStatusError(
        "private response",
        request=request,
        response=transient_response,
    )
    permanent = httpx.HTTPStatusError(
        "private response",
        request=request,
        response=httpx.Response(403, request=request),
    )

    assert gdelt_module._retryable_gdelt_error(transient)
    assert gdelt_module._retry_after_seconds(transient) == 4
    long_retry_response = httpx.Response(
        503, request=request, headers={"Retry-After": "3600"}
    )
    long_retry = httpx.HTTPStatusError(
        "private response",
        request=request,
        response=long_retry_response,
    )
    retry_state = SimpleNamespace(
        outcome=SimpleNamespace(exception=lambda: long_retry),
        attempt_number=1,
    )
    assert gdelt_module._gdelt_retry_wait(retry_state) == 300
    assert not gdelt_module._retryable_gdelt_error(permanent)


def test_rss_sources_pending_rights_are_skipped(monkeypatch):
    import app.discovery.manager as discovery_manager

    source = _source_config()
    manager = DiscoveryManager.__new__(DiscoveryManager)
    manager._rss_feeds_skipped_rights = 0
    monkeypatch.setattr(
        discovery_manager, "load_sources", lambda: {"rss_feeds": [source]}
    )
    monkeypatch.setattr(discovery_manager, "load_source_rights", lambda: {})

    assert manager._get_rss_sources() == []
    assert manager._rss_feeds_skipped_rights == 1

    monkeypatch.setattr(
        discovery_manager,
        "load_source_rights",
        lambda: {
            source.name: {
                "rights_review": "reviewed",
                "current_event_processing": "allowed_with_attribution",
                "commercial_use": "allowed_with_attribution",
                "display_rights": "allowed_with_attribution",
                "storage_retention_constraints": "allowed",
            }
        },
    )
    assert manager._get_rss_sources() == [source]
    assert manager._rss_feeds_skipped_rights == 0

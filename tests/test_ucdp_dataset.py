import csv
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.datasets import ucdp
from app.datasets.ucdp import UCDPGEDImporter, UCDPImportError, write_research_feed
from app.datastore import (
    DatasetImportStateStore,
    EventStore,
    JSONDataStore,
)


def _row(**overrides):
    row = {column: "" for column in ucdp._REQUIRED_COLUMNS}
    row.update(
        {
            "id": "101",
            "relid": "TST-2020-1-1-1",
            "year": "2020",
            "type_of_violence": "1",
            "conflict_name": "Example conflict",
            "source_article": "PRIVATE ARTICLE TEXT MUST NOT BE STORED",
            "source_headline": "PRIVATE HEADLINE MUST NOT BE STORED",
            "where_coordinates": "Example town",
            "where_description": "Example town",
            "adm_1": "Example province",
            "adm_2": "",
            "latitude": "12.5",
            "longitude": "23.5",
            "country": "Exampleland",
            "region": "Africa",
            "event_clarity": "1",
            "date_prec": "1",
            "date_start": "2020-01-02 00:00:00.000",
            "date_end": "2020-01-02 00:00:00.000",
            "deaths_a": "2",
            "deaths_b": "1",
            "deaths_civilians": "3",
            "deaths_unknown": "0",
            "best": "6",
            "high": "7",
            "low": "5",
            "number_of_sources": "2",
        }
    )
    row.update(overrides)
    return row


def _archive(*rows, columns=None):
    output = io.StringIO(newline="")
    fieldnames = columns or sorted(ucdp._REQUIRED_COLUMNS)
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(
        [{key: value for key, value in row.items() if key in fieldnames} for row in rows]
    )
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("GEDEvent_v26_1.csv", output.getvalue())
    return archive.getvalue()


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _importer(tmp_path: Path, max_records=100):
    return UCDPGEDImporter(
        JSONDataStore(tmp_path),
        max_records=max_records,
        max_archive_bytes=100_000,
        max_csv_bytes=500_000,
    )


def test_import_selects_source_native_fields_and_never_creates_news_events(tmp_path):
    body = _archive(_row())
    importer = _importer(tmp_path)
    with _client(lambda request: httpx.Response(200, content=body)) as client:
        state = importer.import_latest(client=client, now=datetime(2026, 10, 20, tzinfo=UTC))

    record = importer.record_store.get_all()[0]
    serialized_record = json.dumps(record.model_dump(mode="json"))
    assert state.last_attempt_status == "succeeded"
    assert state.last_success_dataset_version == "26.1"
    assert state.coverage_start.isoformat() == "2020-01-02"
    assert state.records_received == state.records_added == 1
    assert record.deaths_best == 6
    assert record.date_precision == 1
    assert record.event_clarity == 1
    assert "source_article" not in serialized_record
    assert "source_headline" not in serialized_record
    assert "PRIVATE" not in serialized_record
    assert EventStore(JSONDataStore(tmp_path)).get_all() == []


def test_repeat_import_is_idempotent_and_source_changes_update_same_record(tmp_path):
    importer = _importer(tmp_path)
    first = _archive(_row())
    with _client(lambda request: httpx.Response(200, content=first)) as client:
        first_state = importer.import_latest(client=client)
    assert first_state.records_added == 1
    assert first_state.records_updated == 0
    partition = importer.record_store.partition_dir / "2020.json"
    initial_partition = partition.read_bytes()
    initial_mtime = partition.stat().st_mtime_ns

    with _client(lambda request: httpx.Response(200, content=first)) as client:
        repeated_state = importer.import_latest(client=client)
    assert repeated_state.records_added == 0
    assert repeated_state.records_updated == 0
    assert partition.read_bytes() == initial_partition
    assert partition.stat().st_mtime_ns == initial_mtime

    changed = _archive(_row(best="9"))
    with _client(lambda request: httpx.Response(200, content=changed)) as client:
        changed_state = importer.import_latest(client=client)
    assert changed_state.records_added == 0
    assert changed_state.records_updated == 1
    records = importer.record_store.get_all()
    assert len(records) == 1
    assert records[0].deaths_best == 9
    assert EventStore(JSONDataStore(tmp_path)).get_all() == []


def test_full_snapshot_removes_records_missing_from_the_replacement_release(tmp_path):
    importer = _importer(tmp_path)
    first = _archive(
        _row(),
        _row(
            id="102",
            relid="TST-2019-1-1-2",
            year="2019",
            date_start="2019-02-01 00:00:00.000",
            date_end="2019-02-01 00:00:00.000",
        ),
    )
    with _client(lambda request: httpx.Response(200, content=first)) as client:
        initial = importer.import_latest(client=client)
    assert initial.records_added == 2

    replacement = _archive(_row())
    with _client(lambda request: httpx.Response(200, content=replacement)) as client:
        next_state = importer.import_latest(client=client)
    assert next_state.records_added == 0
    assert next_state.records_removed == 1
    assert [record.source_record_id for record in importer.record_store.get_all()] == [
        "TST-2020-1-1-1"
    ]
    assert not (importer.record_store.partition_dir / "2019.json").exists()


def test_dataset_release_version_updates_the_same_source_record(tmp_path, monkeypatch):
    importer = _importer(tmp_path)
    with _client(lambda request: httpx.Response(200, content=_archive(_row()))) as client:
        first_state = importer.import_latest(client=client)
    first_record = importer.record_store.get_all()[0]

    monkeypatch.setattr(ucdp, "UCDP_GED_VERSION", "26.2")
    with _client(lambda request: httpx.Response(200, content=_archive(_row()))) as client:
        state = importer.import_latest(client=client)

    records = importer.record_store.get_all()
    assert state.last_success_dataset_version == "26.2"
    assert state.records_added == 0
    assert state.records_updated == 1
    assert state.records_removed == 0
    assert len(records) == 1
    assert {record.dataset_version for record in records} == {"26.2"}
    assert records[0].id == first_record.id
    assert records[0].id == "ucdp-ged-TST-2020-1-1-1"
    assert first_state.records_added == 1


def test_failure_history_persists_after_a_later_success(tmp_path):
    importer = _importer(tmp_path)
    with (
        _client(lambda request: httpx.Response(200, content=b"broken")) as client,
        pytest.raises(UCDPImportError),
    ):
        importer.import_latest(client=client, now=datetime(2026, 10, 20, tzinfo=UTC))

    failed_state = importer.state_store.get_ucdp()
    assert failed_state.failure_count == 1
    assert failed_state.last_failure_category == "invalid_archive"
    assert failed_state.last_failure_at == datetime(2026, 10, 20, tzinfo=UTC)

    with _client(lambda request: httpx.Response(200, content=_archive(_row()))) as client:
        succeeded_state = importer.import_latest(
            client=client, now=datetime(2026, 10, 21, tzinfo=UTC)
        )

    assert succeeded_state.last_attempt_status == "succeeded"
    assert succeeded_state.last_error_category is None
    assert succeeded_state.failure_count == 1
    assert succeeded_state.last_failure_category == "invalid_archive"
    assert succeeded_state.last_failure_at == datetime(2026, 10, 20, tzinfo=UTC)


def test_missing_and_invalid_dates_coordinates_remain_null_and_are_counted(tmp_path):
    importer = _importer(tmp_path)
    body = _archive(
        _row(
            date_start="not-a-date",
            date_end="",
            latitude="91",
            longitude="",
        )
    )
    with _client(lambda request: httpx.Response(200, content=body)) as client:
        state = importer.import_latest(client=client)

    record = importer.record_store.get_all()[0]
    assert record.date_start is None
    assert record.date_end is None
    assert record.latitude is None
    assert record.longitude is None
    assert state.observations_missing_dates == 1
    assert state.observations_invalid_dates == 1
    assert state.observations_missing_coordinates == 1
    assert state.observations_invalid_coordinates == 1
    assert state.coverage_start is None
    assert state.coverage_end is None


@pytest.mark.parametrize(
    ("body", "expected_category"),
    [
        (b"not a zip file", "invalid_archive"),
        (_archive(_row(), columns=["id", "relid"]), "unsupported_csv_schema"),
        (
            _archive(_row(relid="")),
            "invalid_dataset_record",
        ),
    ],
)
def test_malformed_or_partial_dataset_fails_without_persisting_records(
    tmp_path, body, expected_category
):
    importer = _importer(tmp_path)
    with (
        _client(lambda request: httpx.Response(200, content=body)) as client,
        pytest.raises(UCDPImportError) as error,
    ):
        importer.import_latest(client=client)
    assert error.value.category == expected_category
    assert importer.record_store.get_all() == []
    state = importer.state_store.get_ucdp()
    assert state.last_attempt_status == "failed"
    assert state.last_error_category == expected_category
    assert state.last_success_at is None


def test_failed_attempt_keeps_previous_success_metadata_and_records(tmp_path):
    importer = _importer(tmp_path)
    with _client(lambda request: httpx.Response(200, content=_archive(_row()))) as client:
        first = importer.import_latest(client=client)
    success_time = first.last_success_at

    with (
        _client(lambda request: httpx.Response(200, content=b"broken")) as client,
        pytest.raises(UCDPImportError),
    ):
        importer.import_latest(client=client)

    state = importer.state_store.get_ucdp()
    assert state.last_attempt_status == "failed"
    assert state.last_success_at == success_time
    assert state.last_success_dataset_version == "26.1"
    assert state.records_received == 1
    assert len(importer.record_store.get_all()) == 1


def test_transient_server_error_retries_once_and_respects_retry_after(tmp_path):
    importer = _importer(tmp_path)
    attempts = []
    delays = []
    body = _archive(_row())

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(503, headers={"Retry-After": "3"})
        return httpx.Response(200, content=body)

    with _client(handler) as client:
        state = importer.import_latest(client=client, sleep=delays.append)
    assert state.records_received == 1
    assert len(attempts) == 2
    assert delays == [3.0]


def test_retry_after_beyond_bounded_wait_stops_without_early_retry(tmp_path):
    importer = _importer(tmp_path)
    requests = []
    delays = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "30"})

    with (
        _client(handler) as client,
        pytest.raises(UCDPImportError, match="download_retry_exhausted"),
    ):
        importer.import_latest(client=client, sleep=delays.append)
    assert len(requests) == 1
    assert delays == []


def test_retry_budget_exhaustion_is_sanitized_and_does_not_overrequest(tmp_path):
    importer = _importer(tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(503, headers={"Retry-After": "0"})

    with _client(handler) as client, pytest.raises(UCDPImportError) as error:
        importer.import_latest(client=client, sleep=lambda delay: None)
    assert error.value.category == "download_retry_exhausted"
    assert len(requests) == ucdp.MAX_ATTEMPTS


def test_download_and_record_limits_are_enforced(tmp_path):
    too_large_importer = UCDPGEDImporter(
        JSONDataStore(tmp_path / "large"),
        max_archive_bytes=4,
        max_csv_bytes=500_000,
        max_records=100,
    )
    with (
        _client(lambda request: httpx.Response(200, content=b"12345")) as client,
        pytest.raises(UCDPImportError, match="archive_too_large"),
    ):
        too_large_importer.import_latest(client=client)

    limited_importer = _importer(tmp_path / "records", max_records=1)
    body = _archive(_row(), _row(id="102", relid="TST-2020-1-1-2"))
    with (
        _client(lambda request: httpx.Response(200, content=body)) as client,
        pytest.raises(UCDPImportError, match="record_limit_exceeded"),
    ):
        limited_importer.import_latest(client=client)
    assert limited_importer.record_store.get_all() == []


def test_public_feed_is_attributed_stable_and_excludes_article_text(tmp_path):
    importer = _importer(tmp_path)
    with _client(lambda request: httpx.Response(200, content=_archive(_row()))) as client:
        importer.import_latest(client=client)
    feed = tmp_path / "public" / "ucdp.json"
    write_research_feed(feed, importer.record_store, importer.state_store)
    document = json.loads(feed.read_text(encoding="utf-8"))
    year_file = tmp_path / "public" / "ucdp-ged" / "years" / "2020.json"
    year_document = json.loads(year_file.read_text(encoding="utf-8"))
    record = year_document["records"][0]
    assert document["status"] == "available"
    assert document["license"] == "CC BY 4.0"
    assert document["record_count"] == 1
    assert document["years"] == [{"year": 2020, "record_count": 1}]
    assert "records" not in document
    assert year_document["dataset_total_count"] == 1
    assert "source_article" not in record
    assert "PRIVATE" not in year_file.read_text(encoding="utf-8")
    assert "historical source-native" in document["notice"].lower()
    assert document["dataset_url"] == ucdp.UCDP_GED_URL


def test_rebuilding_unchanged_public_partitions_does_not_rewrite_year_file(tmp_path):
    importer = _importer(tmp_path)
    with _client(lambda request: httpx.Response(200, content=_archive(_row()))) as client:
        importer.import_latest(client=client, now=datetime(2026, 10, 20, tzinfo=UTC))
    feed = tmp_path / "public" / "ucdp.json"
    write_research_feed(feed, importer.record_store, importer.state_store)
    year_file = tmp_path / "public" / "ucdp-ged" / "years" / "2020.json"
    first_content = year_file.read_bytes()
    first_mtime = year_file.stat().st_mtime_ns

    with _client(lambda request: httpx.Response(200, content=_archive(_row()))) as client:
        importer.import_latest(client=client, now=datetime(2026, 10, 21, tzinfo=UTC))
    write_research_feed(feed, importer.record_store, importer.state_store)

    assert year_file.read_bytes() == first_content
    assert year_file.stat().st_mtime_ns == first_mtime
    index = json.loads(feed.read_text(encoding="utf-8"))
    assert index["retrieved_at"].startswith("2026-10-21")


def test_year_partition_orders_dated_records_before_undated_records(tmp_path):
    importer = _importer(tmp_path)
    body = _archive(
        _row(id="101", relid="TST-2020-1-1-1", date_start=""),
        _row(
            id="102",
            relid="TST-2020-1-1-2",
            date_start="2020-02-01 00:00:00.000",
        ),
    )
    with _client(lambda request: httpx.Response(200, content=body)) as client:
        importer.import_latest(client=client)
    feed = tmp_path / "public" / "ucdp.json"
    write_research_feed(feed, importer.record_store, importer.state_store)
    year_document = json.loads(
        (tmp_path / "public" / "ucdp-ged" / "years" / "2020.json").read_text(encoding="utf-8")
    )
    assert [record["source_record_id"] for record in year_document["records"]] == [
        "TST-2020-1-1-2",
        "TST-2020-1-1-1",
    ]


def test_errors_and_persisted_state_contain_no_response_or_secret_content(tmp_path):
    importer = _importer(tmp_path)
    secret_marker = "NEVER-PERSIST-THIS-CONTENT"

    def handler(request):
        return httpx.Response(200, content=secret_marker.encode())

    with _client(handler) as client, pytest.raises(UCDPImportError) as error:
        importer.import_latest(client=client)

    state_json = json.dumps(
        DatasetImportStateStore(JSONDataStore(tmp_path)).get_ucdp().model_dump(mode="json")
    )
    assert secret_marker not in str(error.value)
    assert secret_marker not in state_json
    assert error.value.category == "invalid_archive"


def test_feed_marks_failed_refresh_stale_but_retains_last_success(tmp_path):
    importer = _importer(tmp_path)
    with _client(lambda request: httpx.Response(200, content=_archive(_row()))) as client:
        importer.import_latest(client=client)
    with (
        _client(lambda request: httpx.Response(200, content=b"bad")) as client,
        pytest.raises(UCDPImportError),
    ):
        importer.import_latest(client=client)

    feed = tmp_path / "public" / "ucdp.json"
    write_research_feed(feed, importer.record_store, importer.state_store)
    document = json.loads(feed.read_text(encoding="utf-8"))
    assert document["status"] == "stale"
    assert document["record_count"] == 1
    assert document["last_error_category"] == "invalid_archive"
    assert document["last_failure_category"] == "invalid_archive"
    assert document["failure_count"] == 1
    year_document = json.loads(
        (tmp_path / "public" / "ucdp-ged" / "years" / "2020.json").read_text(encoding="utf-8")
    )
    assert year_document["records"][0]["source_record_id"] == "TST-2020-1-1-1"


def test_duplicate_source_record_ids_abort_before_persistence(tmp_path):
    importer = _importer(tmp_path)
    body = _archive(
        _row(id="101", relid="TST-2020-1-1-1"),
        _row(id="102", relid="TST-2020-1-1-1"),
    )
    with (
        _client(lambda request: httpx.Response(200, content=body)) as client,
        pytest.raises(UCDPImportError, match="duplicate_source_record_id"),
    ):
        importer.import_latest(client=client)
    assert importer.record_store.get_all() == []


def test_negative_source_count_is_retained_as_unavailable_not_a_negative_count(tmp_path):
    importer = _importer(tmp_path)
    body = _archive(_row(number_of_sources="-1"))
    with _client(lambda request: httpx.Response(200, content=body)) as client:
        state = importer.import_latest(client=client)
    assert importer.record_store.get_all()[0].number_of_sources is None
    assert state.observations_missing_source_counts == 1

from __future__ import annotations

import csv
import io
import json
import math
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from app.datastore import (
    DatasetImportStateStore,
    DatasetRecordStore,
    JSONDataStore,
)
from app.models.schemas import DatasetImportState, DatasetRecord

UCDP_GED_VERSION = "26.1"
UCDP_GED_URL = "https://ucdp.uu.se/downloads/ged/ged261-csv.zip"
UCDP_CODEBOOK_URL = "https://ucdp.uu.se/downloads/ged/ged261.pdf"
UCDP_DOWNLOADS_URL = "https://ucdp.uu.se/downloads/"
CC_BY_4_URL = "https://creativecommons.org/licenses/by/4.0/"
MAX_ARCHIVE_BYTES = 50_000_000
MAX_CSV_BYTES = 350_000_000
MAX_RECORDS = 500_000
MAX_ATTEMPTS = 2
MAX_RETRY_DELAY_SECONDS = 5

_REQUIRED_COLUMNS = {
    "id",
    "relid",
    "year",
    "type_of_violence",
    "conflict_name",
    "source_article",
    "source_headline",
    "where_coordinates",
    "where_description",
    "adm_1",
    "adm_2",
    "latitude",
    "longitude",
    "country",
    "region",
    "event_clarity",
    "date_prec",
    "date_start",
    "date_end",
    "deaths_a",
    "deaths_b",
    "deaths_civilians",
    "deaths_unknown",
    "best",
    "high",
    "low",
    "number_of_sources",
}


class UCDPImportError(Exception):
    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


class UCDPGEDImporter:
    def __init__(
        self,
        data_store: JSONDataStore,
        *,
        record_store: DatasetRecordStore | None = None,
        archive_url: str = UCDP_GED_URL,
        max_archive_bytes: int = MAX_ARCHIVE_BYTES,
        max_csv_bytes: int = MAX_CSV_BYTES,
        max_records: int = MAX_RECORDS,
    ):
        self.data_store = data_store
        self.record_store = record_store or DatasetRecordStore(data_store)
        self.state_store = DatasetImportStateStore(data_store)
        self.archive_url = archive_url
        self.max_archive_bytes = max_archive_bytes
        self.max_csv_bytes = max_csv_bytes
        self.max_records = max_records

    def import_latest(
        self,
        *,
        client: httpx.Client | None = None,
        now: datetime | None = None,
        sleep=time.sleep,
    ) -> DatasetImportState:
        attempted_at = now or datetime.now(UTC)
        previous = self.state_store.get_ucdp()
        owns_client = client is None
        active_client = client or httpx.Client(
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=True,
            headers={"User-Agent": "HUMANITY-research-dataset-import/1.0"},
        )
        try:
            with tempfile.TemporaryDirectory(prefix="humanity-ucdp-") as folder:
                archive_path = self._download_archive(
                    active_client, Path(folder) / "ged.zip", sleep=sleep
                )
                records, quality = self._read_archive(archive_path)
            added, updated, removed = self.record_store.replace_snapshot(records)
            starts = [record.date_start for record in records if record.date_start]
            ends = [record.date_end for record in records if record.date_end]
            state = previous.model_copy(
                update={
                    "last_attempt_at": attempted_at,
                    "last_attempt_status": "succeeded",
                    "last_error_category": None,
                    "last_success_at": attempted_at,
                    "last_success_dataset_version": UCDP_GED_VERSION,
                    "coverage_start": min(starts) if starts else None,
                    "coverage_end": max(ends) if ends else None,
                    "records_received": len(records),
                    "records_added": added,
                    "records_updated": updated,
                    "records_removed": removed,
                    **quality,
                }
            )
            return self.state_store.save_ucdp(state)
        except UCDPImportError as error:
            failed_state = previous.model_copy(
                update={
                    "last_attempt_at": attempted_at,
                    "last_attempt_status": "failed",
                    "last_error_category": error.category,
                    "failure_count": previous.failure_count + 1,
                    "last_failure_at": attempted_at,
                    "last_failure_category": error.category,
                }
            )
            self.state_store.save_ucdp(failed_state)
            raise
        finally:
            if owns_client:
                active_client.close()

    def _download_archive(self, client: httpx.Client, archive_path: Path, *, sleep) -> Path:
        last_category = "download_network_error"

        for attempt in range(MAX_ATTEMPTS):
            size = 0
            try:
                with client.stream("GET", self.archive_url) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt + 1 < MAX_ATTEMPTS:
                            delay = _retry_delay(response.headers.get("retry-after"))
                            if delay is not None:
                                sleep(delay)
                                continue
                        last_category = "download_retry_exhausted"
                        break
                    if response.status_code < 200 or response.status_code >= 300:
                        raise UCDPImportError("download_http_error")
                    declared_length = response.headers.get("content-length")
                    if declared_length and int(declared_length) > self.max_archive_bytes:
                        raise UCDPImportError("archive_too_large")
                    with archive_path.open("wb") as output:
                        for chunk in response.iter_bytes(1024 * 1024):
                            size += len(chunk)
                            if size > self.max_archive_bytes:
                                raise UCDPImportError("archive_too_large")
                            output.write(chunk)
                if size == 0:
                    raise UCDPImportError("empty_archive")
                return archive_path
            except UCDPImportError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt + 1 < MAX_ATTEMPTS:
                    sleep(1)
                else:
                    last_category = "download_network_error"
            except (ValueError, OSError):
                raise UCDPImportError("download_error") from None

        raise UCDPImportError(last_category)

    def _read_archive(self, archive_path: Path) -> tuple[list[DatasetRecord], dict[str, int]]:
        records: list[DatasetRecord] = []
        seen_record_ids: set[str] = set()
        seen_record_numbers: set[str] = set()
        missing_dates = 0
        invalid_dates = 0
        missing_coordinates = 0
        invalid_coordinates = 0
        missing_source_counts = 0
        try:
            with zipfile.ZipFile(archive_path) as archive:
                csv_members = [
                    member
                    for member in archive.infolist()
                    if member.filename.lower().endswith(".csv")
                ]
                if len(csv_members) != 1:
                    raise UCDPImportError("invalid_archive_contents")
                member = csv_members[0]
                if member.file_size > self.max_csv_bytes:
                    raise UCDPImportError("csv_too_large")
                if member.compress_size and member.file_size / member.compress_size > 30:
                    raise UCDPImportError("suspicious_archive_ratio")
                with archive.open(member) as raw:
                    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
                    if not reader.fieldnames or not _REQUIRED_COLUMNS.issubset(reader.fieldnames):
                        raise UCDPImportError("unsupported_csv_schema")
                    for row in reader:
                        if len(records) >= self.max_records:
                            raise UCDPImportError("record_limit_exceeded")
                        record, quality = _parse_record(row)
                        if (
                            record.source_record_id in seen_record_ids
                            or record.record_number in seen_record_numbers
                        ):
                            raise UCDPImportError("duplicate_source_record_id")
                        seen_record_ids.add(record.source_record_id)
                        seen_record_numbers.add(record.record_number)
                        records.append(record)
                        missing_dates += quality["missing_dates"]
                        invalid_dates += quality["invalid_dates"]
                        missing_coordinates += quality["missing_coordinates"]
                        invalid_coordinates += quality["invalid_coordinates"]
                        missing_source_counts += quality["missing_source_counts"]
        except UCDPImportError:
            raise
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError):
            raise UCDPImportError("invalid_archive") from None
        except (csv.Error, UnicodeError):
            raise UCDPImportError("malformed_csv") from None
        if not records:
            raise UCDPImportError("empty_dataset")
        return records, {
            "observations_missing_dates": missing_dates,
            "observations_invalid_dates": invalid_dates,
            "observations_missing_coordinates": missing_coordinates,
            "observations_invalid_coordinates": invalid_coordinates,
            "observations_missing_source_counts": missing_source_counts,
        }


def write_research_feed(
    output_path: Path,
    record_store: DatasetRecordStore,
    state_store: DatasetImportStateStore,
) -> None:
    state = state_store.get_ucdp()
    version = state.last_success_dataset_version
    records = record_store.get_all(version) if version else []
    records.sort(
        key=lambda item: (
            item.date_start is not None,
            item.date_start or date.min,
            item.source_record_id,
        ),
        reverse=True,
    )
    records_by_year: dict[int, list[DatasetRecord]] = defaultdict(list)
    for record in records:
        records_by_year[record.event_year].append(record)
    status = (
        "not_imported"
        if state.last_success_at is None
        else "stale"
        if state.last_attempt_status == "failed"
        else "available"
    )
    metadata = {
        "schema_version": 1,
        "dataset": "Uppsala Conflict Data Program Georeferenced Event Dataset",
        "dataset_version": version,
        "status": status,
        "last_attempt_at": state.last_attempt_at.isoformat() if state.last_attempt_at else None,
        "last_attempt_status": state.last_attempt_status,
        "last_error_category": state.last_error_category,
        "failure_count": state.failure_count,
        "last_failure_at": (state.last_failure_at.isoformat() if state.last_failure_at else None),
        "last_failure_category": state.last_failure_category,
        "retrieved_at": state.last_success_at.isoformat() if state.last_success_at else None,
        "coverage_start": state.coverage_start.isoformat() if state.coverage_start else None,
        "coverage_end": state.coverage_end.isoformat() if state.coverage_end else None,
        "record_count": len(records),
        "years": [
            {"year": year, "record_count": count}
            for year, count in sorted(
                Counter(record.event_year for record in records).items(), reverse=True
            )
        ],
        "records_added": state.records_added,
        "records_updated": state.records_updated,
        "records_removed": state.records_removed,
        "observations_missing_dates": state.observations_missing_dates,
        "observations_invalid_dates": state.observations_invalid_dates,
        "observations_missing_coordinates": state.observations_missing_coordinates,
        "observations_invalid_coordinates": state.observations_invalid_coordinates,
        "observations_missing_source_counts": state.observations_missing_source_counts,
        "source_url": UCDP_DOWNLOADS_URL,
        "dataset_url": UCDP_GED_URL,
        "codebook_url": UCDP_CODEBOOK_URL,
        "license": "CC BY 4.0",
        "license_url": CC_BY_4_URL,
        "attribution": (
            "Uppsala Conflict Data Program (UCDP), Georeferenced Event Dataset "
            f"(GED), version {version or UCDP_GED_VERSION}; cite the relevant UCDP "
            "publication. Changes: selected event-level fields; source-article "
            "text and publisher metadata omitted."
        ),
        "notice": (
            "Historical source-native dataset observations, not live alerts, "
            "independently corroborated HUMANITY news events, or publication candidates."
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_if_changed(output_path, metadata)

    years_path = output_path.parent / "ucdp-ged" / "years"
    years_path.mkdir(parents=True, exist_ok=True)
    expected_files: set[Path] = set()
    for year, year_records in records_by_year.items():
        year_path = years_path / f"{year}.json"
        expected_files.add(year_path)
        year_document = {
            "schema_version": metadata["schema_version"],
            "dataset": metadata["dataset"],
            "dataset_version": version,
            "status": "available",
            "year": year,
            "dataset_total_count": len(records),
            "record_count": len(year_records),
            "coverage_start": metadata["coverage_start"],
            "coverage_end": metadata["coverage_end"],
            "source_url": metadata["source_url"],
            "dataset_url": metadata["dataset_url"],
            "codebook_url": metadata["codebook_url"],
            "license": metadata["license"],
            "license_url": metadata["license_url"],
            "attribution": metadata["attribution"],
            "notice": metadata["notice"],
            "records": [record.model_dump(mode="json") for record in year_records],
        }
        _write_json_if_changed(year_path, year_document)
    for existing_path in years_path.glob("*.json"):
        if existing_path not in expected_files:
            existing_path.unlink()


def _write_json_if_changed(path: Path, document: dict) -> None:
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    if path.exists() and path.read_text(encoding="utf-8") == payload:
        return
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(payload, encoding="utf-8")
    temporary_path.replace(path)


def _parse_record(row: dict[str, str | None]) -> tuple[DatasetRecord, dict[str, int]]:
    record_number = _required_text(row, "id")
    source_record_id = _required_text(row, "relid")
    year = _required_integer(row, "year")
    violence_type = _required_integer(row, "type_of_violence")
    date_start, start_invalid = _optional_date(row.get("date_start"))
    date_end, end_invalid = _optional_date(row.get("date_end"))
    latitude, latitude_invalid = _optional_float(row.get("latitude"), -90, 90)
    longitude, longitude_invalid = _optional_float(row.get("longitude"), -180, 180)
    coordinates_missing = (
        not (row.get("latitude") or "").strip() or not (row.get("longitude") or "").strip()
    )
    coordinates_invalid = latitude_invalid or longitude_invalid
    if coordinates_missing or coordinates_invalid:
        latitude = None
        longitude = None

    identifier = f"ucdp-ged-{source_record_id}"
    record = DatasetRecord(
        id=identifier,
        source_dataset="UCDP GED",
        dataset_version=UCDP_GED_VERSION,
        source_record_id=source_record_id,
        record_number=record_number,
        event_year=year,
        date_start=date_start,
        date_end=date_end,
        country=_optional_text(row.get("country")),
        region=_optional_text(row.get("region")),
        location=_optional_text(row.get("where_description")),
        admin1=_optional_text(row.get("adm_1")),
        admin2=_optional_text(row.get("adm_2")),
        latitude=latitude,
        longitude=longitude,
        violence_type=violence_type,
        conflict_name=_optional_text(row.get("conflict_name")),
        deaths_a=_optional_integer(row.get("deaths_a")),
        deaths_b=_optional_integer(row.get("deaths_b")),
        deaths_civilians=_optional_integer(row.get("deaths_civilians")),
        deaths_unknown=_optional_integer(row.get("deaths_unknown")),
        deaths_best=_optional_integer(row.get("best")),
        deaths_high=_optional_integer(row.get("high")),
        deaths_low=_optional_integer(row.get("low")),
        date_precision=_optional_integer(row.get("date_prec")),
        location_precision=_optional_integer(row.get("where_prec")),
        event_clarity=_optional_integer(row.get("event_clarity")),
        number_of_sources=_optional_count(row.get("number_of_sources")),
    )
    return record, {
        "missing_dates": int(date_start is None or date_end is None),
        "invalid_dates": int(start_invalid) + int(end_invalid),
        "missing_coordinates": int(coordinates_missing),
        "invalid_coordinates": int(coordinates_invalid),
        "missing_source_counts": int(
            row.get("number_of_sources") is None
            or not row["number_of_sources"].strip()
            or row["number_of_sources"].strip().startswith("-")
        ),
    }


def _required_text(row: dict[str, str | None], key: str) -> str:
    value = _optional_text(row.get(key))
    if not value:
        raise UCDPImportError("invalid_dataset_record")
    return value


def _optional_text(value: str | None) -> str | None:
    return value.strip() or None if value is not None else None


def _required_integer(row: dict[str, str | None], key: str) -> int:
    value = _optional_integer(row.get(key))
    if value is None:
        raise UCDPImportError("invalid_dataset_record")
    return value


def _optional_integer(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError:
        raise UCDPImportError("invalid_dataset_record") from None
    if parsed < 0:
        raise UCDPImportError("invalid_dataset_record")
    return parsed


def _optional_count(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError:
        raise UCDPImportError("invalid_dataset_record") from None
    return parsed if parsed >= 0 else None


def _optional_date(value: str | None) -> tuple[date | None, bool]:
    if value is None or not value.strip():
        return None, False
    try:
        return date.fromisoformat(value.strip()[:10]), False
    except ValueError:
        return None, True


def _optional_float(value: str | None, minimum: float, maximum: float) -> tuple[float | None, bool]:
    if value is None or not value.strip():
        return None, False
    try:
        parsed = float(value)
    except ValueError:
        return None, True
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        return None, True
    return parsed, False


def _retry_delay(value: str | None) -> float | None:
    if not value:
        return 1.0
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            seconds = (retry_at - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 1.0
    if seconds > MAX_RETRY_DELAY_SECONDS:
        return None
    return max(0.0, seconds)

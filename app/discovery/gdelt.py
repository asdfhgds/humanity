from __future__ import annotations

import asyncio
import csv
import gzip
import io
import zipfile
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
from app.diagnostics import classify_request_error
from app.config import get_settings, get_enabled_sources
from app.utils import canonicalize_url, compute_article_hash, parse_date_flexible
from app.models.schemas import Source, SourceType, IngestionRun
from app.datastore import get_source_store, get_ingestion_run_store


GDELT_EVENT_URL = "http://data.gdeltproject.org/events/"
GDELT_GKG_URL = "http://data.gdeltproject.org/gkg/"
GDELT_MASTER_URL = "http://data.gdeltproject.org/gdeltv2/masterfilelist.txt"

try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:
    csv.field_size_limit(2**31 - 1)


CAMEO_CODES = {
    "01": "MAKE_PUBLIC_STATEMENT",
    "02": "APPEAL",
    "03": "EXPRESS_INTENT_COOPERATE",
    "04": "CONSULT",
    "05": "ENGAGE_DIPLOMACY",
    "06": "ENGAGE_MATERIAL_COOPERATION",
    "07": "PROVIDE_AID",
    "08": "YIELD",
    "09": "INVESTIGATE",
    "10": "DEMAND",
    "11": "DISAPPROVE",
    "12": "REJECT",
    "13": "THREATEN",
    "14": "PROTEST",
    "15": "EXHIBIT_FORCE",
    "16": "REDUCE_RELATIONS",
    "17": "COERCE",
    "18": "ASSAULT",
    "19": "FIGHT",
    "20": "USE_UNCONVENTIONAL_VIOLENCE",
}

EVENT_TYPE_MAP = {
    "14": "PROTEST",
    "15": "CONFLICT",
    "18": "ATTACK",
    "19": "CONFLICT",
    "20": "ATTACK",
}


def _retry_after_seconds(error: BaseException) -> Optional[float]:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {})
    value = headers.get("Retry-After") if headers else None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _retryable_gdelt_error(error: BaseException) -> bool:
    if isinstance(error, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        retry_after = _retry_after_seconds(error)
        if status_code == 429 and retry_after is not None and retry_after > 300:
            return False
        return status_code in {408, 425, 429} or 500 <= status_code <= 599
    return False


def _gdelt_retry_wait(retry_state) -> float:
    delay = wait_exponential(multiplier=1, min=2, max=10)(retry_state)
    error = retry_state.outcome.exception()
    retry_after = _retry_after_seconds(error) if error else None
    return min(300.0, max(delay, retry_after or 0.0))


def _parse_master_index(text: str, suffix: str) -> list[tuple[datetime, str]]:
    entries = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        file_url = parts[2]
        parsed_url = urlparse(file_url)
        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            continue
        filename = parsed_url.path.rsplit("/", 1)[-1]
        if not filename.endswith(suffix):
            continue
        timestamp = filename[: -len(suffix)]
        try:
            file_date = datetime.strptime(timestamp, "%Y%m%d%H%M%S")
        except ValueError:
            continue
        entries.append((file_date, file_url))
    return entries


class GDELTDiscovery:
    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
            follow_redirects=True,
        )
        self.source_store = get_source_store()
        self.run_store = get_ingestion_run_store()
        self.diagnostics = self._new_diagnostics()

    @staticmethod
    def _new_diagnostics() -> Dict[str, Dict[str, Any]]:
        return {
            stage: {
                "status": "not_run",
                "errors": 0,
                "error_categories": {},
                "rows_seen": 0,
                "records_accepted": 0,
                "records_rejected": 0,
                "malformed_rows": 0,
                "filtered_rows": 0,
            }
            for stage in ("events", "gkg")
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=_gdelt_retry_wait,
        retry=retry_if_exception(_retryable_gdelt_error),
        reraise=True,
    )
    async def _get_response(self, url: str) -> httpx.Response:
        response = await self.client.get(url)
        response.raise_for_status()
        return response

    def _record_error(
        self, stage: str, error: Exception, category: Optional[str] = None
    ) -> None:
        stage_diagnostics = self.diagnostics[stage]
        stage_diagnostics["errors"] += 1
        self.diagnostics[stage]["status"] = "failed"
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)
        if not category:
            if isinstance(status_code, int):
                category = (
                    "rate_limited_429"
                    if status_code == 429
                    else f"http_error_{status_code}"
                )
            else:
                category = classify_request_error(error)
        categories = stage_diagnostics["error_categories"]
        categories[category] = categories.get(category, 0) + 1
        stage_diagnostics["status"] = category

    async def close(self):
        await self.client.aclose()

    async def get_latest_master_file(self, hours_back: int = 6, stage: str = "events") -> Optional[str]:
        """Get the latest GDELT master file URL."""
        try:
            response = await self._get_response(GDELT_MASTER_URL)
            suffix = ".gkg.csv.zip" if stage == "gkg" else ".export.CSV.zip"
            entries = _parse_master_index(response.text, suffix)
            if not entries:
                self.diagnostics[stage]["status"] = "malformed_response"
                self.diagnostics[stage]["errors"] += 1
                self.diagnostics[stage]["error_categories"]["malformed_response"] = (
                    self.diagnostics[stage]["error_categories"].get(
                        "malformed_response", 0
                    )
                    + 1
                )
                return None
            cutoff = datetime.utcnow() - timedelta(hours=hours_back)
            for file_date, file_url in reversed(entries):
                if file_date >= cutoff:
                    self.diagnostics[stage]["status"] = "master_file_found"
                    return file_url
            self.diagnostics[stage]["status"] = "no_recent_data"
        except Exception as e:
            self._record_error(stage, e)
            print(f"GDELT master index request failed: {classify_request_error(e)}")
        return None

    async def download_and_parse_events(
        self, file_url: str, max_records: int = 5000
    ) -> List[Dict[str, Any]]:
        """Download and parse GDELT events file."""
        events = []
        try:
            response = await self._get_response(file_url)

            content = response.content
            if file_url.endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    csv_names = [
                        name for name in zf.namelist() if name.endswith(".CSV")
                    ]
                    if not csv_names:
                        raise zipfile.BadZipFile("GDELT event archive has no CSV")
                    with zf.open(csv_names[0]) as f:
                        content = f.read()

            if file_url.endswith(".gz"):
                content = gzip.decompress(content)

            text = content.decode("utf-8", errors="strict")
            reader = csv.reader(text.splitlines(), delimiter="\t")

            for i, row in enumerate(reader):
                if i >= max_records:
                    break
                stage_diagnostics = self.diagnostics["events"]
                stage_diagnostics["rows_seen"] += 1
                if len(row) < 60:
                    stage_diagnostics["records_rejected"] += 1
                    stage_diagnostics["malformed_rows"] += 1
                    continue

                event, row_status = self._parse_gdelt_row_with_status(row)
                if event:
                    events.append(event)
                    stage_diagnostics["records_accepted"] += 1
                else:
                    stage_diagnostics["records_rejected"] += 1
                    counter = (
                        "malformed_rows"
                        if row_status == "malformed"
                        else "filtered_rows"
                    )
                    stage_diagnostics[counter] += 1

        except Exception as e:
            category = (
                "malformed_response"
                if isinstance(e, (UnicodeDecodeError, csv.Error, zipfile.BadZipFile))
                else classify_request_error(e)
            )
            self._record_error("events", e, category=category)
            print(f"GDELT events response failed: {category}")
        return events

    def _parse_gdelt_row(self, row: List[str]) -> Optional[Dict[str, Any]]:
        """Parse a single GDELT event row."""
        event, _ = self._parse_gdelt_row_with_status(row)
        return event

    def _parse_gdelt_row_with_status(
        self, row: List[str]
    ) -> tuple[Optional[Dict[str, Any]], str]:
        try:
            global_event_id = row[0]
            day = row[1]
            actor1_name = row[6]
            actor1_country = row[7]
            actor2_name = row[14]
            actor2_country = row[15]
            event_code = row[26]
            event_root_code = row[28]
            quad_class = row[29]
            goldstein_scale = row[30]
            action_geo_lat = row[49]
            action_geo_lon = row[50]
            source_url = row[58]
        except IndexError:
            return None, "malformed"

        parsed_url = urlparse(source_url)
        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            return None, "filtered"
        event_type = self._map_event_type(event_root_code)
        if not event_type:
            return None, "filtered"

        try:
            event_date = datetime.strptime(day, "%Y%m%d")
            num_mentions = int(row[31]) if row[31] else 0
            num_sources = int(row[32]) if row[32] else 0
            num_articles = int(row[33]) if row[33] else 0
            avg_tone = float(row[34]) if row[34] else 0.0
            latitude = float(action_geo_lat) if action_geo_lat else None
            longitude = float(action_geo_lon) if action_geo_lon else None
            goldstein = float(goldstein_scale) if goldstein_scale else 0.0
            quad_class_value = int(quad_class) if quad_class else 0
        except ValueError:
            return None, "malformed"

        return {
            "gdelt_id": global_event_id,
            "date": event_date,
            "event_type": event_type,
            "event_code": event_code,
            "event_root_code": event_root_code,
            "actor1": actor1_name,
            "actor1_country": actor1_country,
            "actor2": actor2_name,
            "actor2_country": actor2_country,
            "latitude": latitude,
            "longitude": longitude,
            "goldstein": goldstein,
            "num_mentions": num_mentions,
            "num_sources": num_sources,
            "num_articles": num_articles,
            "avg_tone": avg_tone,
            "source_url": source_url,
            "quad_class": quad_class_value,
        }, "accepted"

    def _map_event_type(self, root_code: str) -> Optional[str]:
        """Map GDELT CAMEO root code to our event types."""
        return EVENT_TYPE_MAP.get(root_code)

    async def discover(self, hours_back: int = 6, max_records: int = 1000) -> List[Dict[str, Any]]:
        """Main discovery entry point."""
        master_file = await self.get_latest_master_file(hours_back, stage="events")
        if not master_file:
            return []

        events = await self.download_and_parse_events(master_file, max_records)
        if self.diagnostics["events"]["errors"] and events:
            self.diagnostics["events"]["status"] = "partial_failure"
        elif self.diagnostics["events"]["errors"]:
            pass
        elif self.diagnostics["events"]["status"] == "no_recent_data":
            pass
        elif self.diagnostics["events"]["malformed_rows"]:
            self.diagnostics["events"]["status"] = "malformed_response"
        elif not events:
            self.diagnostics["events"]["status"] = "valid_zero_results"
        elif self.diagnostics["events"]["status"] != "failed":
            self.diagnostics["events"]["status"] = "valid_results"
        return events

    async def discover_gkg(self, hours_back: int = 6, max_records: int = 500) -> List[Dict[str, Any]]:
        """Discover from GDELT Global Knowledge Graph."""
        articles = []
        self.diagnostics["gkg"]["status"] = "attempted"
        try:
            response = await self._get_response(GDELT_MASTER_URL)
            self.diagnostics["gkg"]["status"] = "master_index_retrieved"
            entries = _parse_master_index(response.text, ".gkg.csv.zip")
            if not entries:
                self.diagnostics["gkg"]["status"] = "malformed_response"
                self.diagnostics["gkg"]["errors"] += 1
                errors = self.diagnostics["gkg"]["error_categories"]
                errors["malformed_response"] = errors.get("malformed_response", 0) + 1
                return articles

            cutoff = datetime.utcnow() - timedelta(hours=hours_back)
            gkg_files = [
                file_url
                for file_date, file_url in reversed(entries)
                if file_date >= cutoff
            ][:3]

            if not gkg_files:
                self.diagnostics["gkg"]["status"] = "no_recent_data"
            else:
                self.diagnostics["gkg"]["status"] = "files_found"

            for file_url in gkg_files:
                if len(articles) >= max_records:
                    break
                try:
                    response = await self._get_response(file_url)
                    content = response.content

                    with zipfile.ZipFile(io.BytesIO(content)) as zf:
                        csv_names = [
                            name for name in zf.namelist() if name.endswith(".csv")
                        ]
                        if not csv_names:
                            raise zipfile.BadZipFile("GDELT GKG archive has no CSV")
                        with zf.open(csv_names[0]) as f:
                            text = f.read().decode("utf-8", errors="strict")

                    reader = csv.reader(text.splitlines(), delimiter="\t")
                    for i, row in enumerate(reader):
                        if i >= max_records:
                            break
                        stage_diagnostics = self.diagnostics["gkg"]
                        stage_diagnostics["rows_seen"] += 1
                        if len(row) < 25:
                            stage_diagnostics["records_rejected"] += 1
                            stage_diagnostics["malformed_rows"] += 1
                            continue
                        article, row_status = self._parse_gkg_row_with_status(row)
                        if article:
                            articles.append(article)
                            stage_diagnostics["records_accepted"] += 1
                        else:
                            stage_diagnostics["records_rejected"] += 1
                            counter = (
                                "malformed_rows"
                                if row_status == "malformed"
                                else "filtered_rows"
                            )
                            stage_diagnostics[counter] += 1
                except Exception as e:
                    category = (
                        "malformed_response"
                        if isinstance(e, (UnicodeDecodeError, csv.Error, zipfile.BadZipFile))
                        else classify_request_error(e)
                    )
                    self._record_error("gkg", e, category=category)
                    print(f"GDELT GKG response failed: {category}")
            if self.diagnostics["gkg"]["errors"] and articles:
                self.diagnostics["gkg"]["status"] = "partial_failure"
            elif self.diagnostics["gkg"]["errors"]:
                pass
            elif self.diagnostics["gkg"]["malformed_rows"]:
                self.diagnostics["gkg"]["status"] = "malformed_response"
            elif gkg_files and not articles:
                self.diagnostics["gkg"]["status"] = "valid_zero_results"
            elif gkg_files:
                self.diagnostics["gkg"]["status"] = "valid_results"
        except Exception as e:
            self._record_error("gkg", e)
            print(f"GDELT GKG index request failed: {classify_request_error(e)}")
        return articles

    def _parse_gkg_row(self, row: List[str]) -> Optional[Dict[str, Any]]:
        """Parse GKG row."""
        article, _ = self._parse_gkg_row_with_status(row)
        return article

    def _parse_gkg_row_with_status(
        self, row: List[str]
    ) -> tuple[Optional[Dict[str, Any]], str]:
        try:
            date = row[0]
            source_id = row[1]
            url = row[2]
            title = row[5] if len(row) > 5 else ""
            language = row[3] if len(row) > 3 else "en"
        except IndexError:
            return None, "malformed"

        parsed_url = urlparse(url)
        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            return None, "filtered"

        try:
            event_date = datetime.strptime(date[:8], "%Y%m%d")
        except ValueError:
            return None, "malformed"

        return {
            "gdelt_gkg_id": f"{date}_{source_id}",
            "date": event_date,
            "url": url,
            "title": title,
            "language": language,
            "source_id": source_id,
        }, "accepted"


async def run_gdelt_discovery(hours_back: int = 6, max_events: int = 1000, max_articles: int = 500) -> Dict[str, Any]:
    """Run GDELT discovery and store results."""
    discovery = GDELTDiscovery()
    run_store = get_ingestion_run_store()

    run = IngestionRun(
        sources_attempted=2,
    )
    run = run_store.create(run)

    try:
        events = await discovery.discover(hours_back, max_events)
        articles = await discovery.discover_gkg(hours_back, max_articles)

        run.articles_seen = len(events) + len(articles)
        run.candidates_created = len(events)

        source_store = get_source_store()
        for event in events:
            source = Source(
                publisher="GDELT",
                title=event.get("actor1", "GDELT Event") + " - " + event.get("actor2", ""),
                url=event["source_url"],
                canonical_url=canonicalize_url(event["source_url"]),
                published_at=event["date"],
                source_type=SourceType.DATASET,
                country=event.get("actor1_country"),
                article_hash=compute_article_hash(event.get("actor1", ""), event["source_url"]),
            )
            stored_source = source_store.create(source)
            if stored_source.id == source.id:
                run.sources_succeeded += 1

        for article in articles:
            source = Source(
                publisher="GDELT GKG",
                title=article.get("title", "GDELT Article"),
                url=article["url"],
                canonical_url=canonicalize_url(article["url"]),
                published_at=article["date"],
                source_type=SourceType.DATASET,
                language=article.get("language", "en"),
                article_hash=compute_article_hash(article.get("title", ""), article["url"]),
            )
            stored_source = source_store.create(source)
            if stored_source.id == source.id:
                run.sources_succeeded += 1

        run.finished_at = datetime.utcnow()
        run.status = "completed"
        run_store.update(run.id, run.model_dump(mode="json"))

        return {
            "run_id": run.id,
            "events_found": len(events),
            "articles_found": len(articles),
            "sources_created": run.sources_succeeded,
            "diagnostics": discovery.diagnostics,
        }
    except Exception as e:
        run.finished_at = datetime.utcnow()
        run.status = "failed"
        run.errors.append(classify_request_error(e))
        run_store.update(run.id, run.model_dump(mode="json"))
        raise
    finally:
        await discovery.close()

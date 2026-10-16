from __future__ import annotations

import asyncio
import csv
import gzip
import io
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
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


class GDELTDiscovery:
    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
            follow_redirects=True,
        )
        self.source_store = get_source_store()
        self.run_store = get_ingestion_run_store()

    async def close(self):
        await self.client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError)),
        reraise=True
    )
    async def get_latest_master_file(self, hours_back: int = 6) -> Optional[str]:
        """Get the latest GDELT master file URL."""
        try:
            response = await self.client.get(GDELT_MASTER_URL)
            response.raise_for_status()
            lines = response.text.strip().split("\n")
            cutoff = datetime.utcnow() - timedelta(hours=hours_back)
            for line in reversed(lines):
                parts = line.split()
                if len(parts) >= 3:
                    file_url = parts[2]
                    try:
                        date_str = file_url.split("/")[-1].replace(".export.CSV.zip", "")
                        file_date = datetime.strptime(date_str, "%Y%m%d%H%M%S")
                        if file_date >= cutoff:
                            return file_url
                    except (ValueError, IndexError):
                        continue
        except Exception as e:
            print(f"Error fetching GDELT master file: {e}")
        return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError)),
        reraise=True
    )
    async def download_and_parse_events(self, file_url: str, max_records: int = 5000) -> List[Dict[str, Any]]:
        """Download and parse GDELT events file."""
        events = []
        try:
            response = await self.client.get(file_url)
            response.raise_for_status()
            
            content = response.content
            if file_url.endswith(".zip"):
                import zipfile
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    for name in zf.namelist():
                        if name.endswith(".CSV"):
                            with zf.open(name) as f:
                                content = f.read()
                            break
            
            if file_url.endswith(".gz") or file_url.endswith(".CSV.zip"):
                try:
                    content = gzip.decompress(content)
                except Exception:
                    pass
            
            text = content.decode("utf-8", errors="ignore")
            reader = csv.reader(text.splitlines(), delimiter="\t")
            
            for i, row in enumerate(reader):
                if i >= max_records:
                    break
                if len(row) < 60:
                    continue
                
                try:
                    event = self._parse_gdelt_row(row)
                    if event:
                        events.append(event)
                except Exception:
                    continue
                    
        except Exception as e:
            print(f"Error parsing GDELT events: {e}")
        return events

    def _parse_gdelt_row(self, row: List[str]) -> Optional[Dict[str, Any]]:
        """Parse a single GDELT event row."""
        try:
            global_event_id = row[0]
            day = row[1]
            month_year = row[2]
            year = row[3]
            fraction_date = row[4]
            actor1_code = row[5]
            actor1_name = row[6]
            actor1_country = row[7]
            actor1_known_group = row[8]
            actor1_ethnic = row[9]
            actor1_religion1 = row[10]
            actor1_religion2 = row[11]
            actor1_type = row[12]
            actor2_code = row[13]
            actor2_name = row[14]
            actor2_country = row[15]
            actor2_known_group = row[16]
            actor2_ethnic = row[17]
            actor2_religion1 = row[18]
            actor2_religion2 = row[19]
            actor2_type = row[20]
            is_root_event = row[21]
            event_code = row[26]
            event_base_code = row[27]
            event_root_code = row[28]
            quad_class = row[29]
            goldstein_scale = row[30]
            num_mentions = int(row[31]) if row[31] else 0
            num_sources = int(row[32]) if row[32] else 0
            num_articles = int(row[33]) if row[33] else 0
            avg_tone = float(row[34]) if row[34] else 0.0
            actor1_geo_lat = row[35]
            actor1_geo_lon = row[36]
            actor2_geo_lat = row[37]
            actor2_geo_lon = row[38]
            action_geo_lat = row[49]
            action_geo_lon = row[50]
            action_geo_feature_id = row[51]
            date_added = row[57]
            source_url = row[58] if len(row) > 58 else ""

            if not source_url or not source_url.startswith("http"):
                return None

            event_type = self._map_event_type(event_root_code)
            if not event_type:
                return None

            try:
                event_date = datetime.strptime(day, "%Y%m%d")
            except ValueError:
                event_date = datetime.utcnow()

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
                "latitude": float(action_geo_lat) if action_geo_lat else None,
                "longitude": float(action_geo_lon) if action_geo_lon else None,
                "goldstein": float(goldstein_scale) if goldstein_scale else 0.0,
                "num_mentions": num_mentions,
                "num_sources": num_sources,
                "num_articles": num_articles,
                "avg_tone": avg_tone,
                "source_url": source_url,
                "quad_class": int(quad_class) if quad_class else 0,
            }
        except Exception:
            return None

    def _map_event_type(self, root_code: str) -> Optional[str]:
        """Map GDELT CAMEO root code to our event types."""
        return EVENT_TYPE_MAP.get(root_code)

    async def discover(self, hours_back: int = 6, max_records: int = 1000) -> List[Dict[str, Any]]:
        """Main discovery entry point."""
        master_file = await self.get_latest_master_file(hours_back)
        if not master_file:
            return []
        
        events = await self.download_and_parse_events(master_file, max_records)
        return events

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError)),
        reraise=True
    )
    async def discover_gkg(self, hours_back: int = 6, max_records: int = 500) -> List[Dict[str, Any]]:
        """Discover from GDELT Global Knowledge Graph."""
        articles = []
        try:
            response = await self.client.get(GDELT_MASTER_URL)
            response.raise_for_status()
            lines = response.text.strip().split("\n")
            cutoff = datetime.utcnow() - timedelta(hours=hours_back)
            
            gkg_files = []
            for line in reversed(lines):
                parts = line.split()
                if len(parts) >= 3 and "gkg" in parts[2].lower():
                    file_url = parts[2]
                    try:
                        date_str = file_url.split("/")[-1].replace(".gkg.csv.zip", "")
                        file_date = datetime.strptime(date_str, "%Y%m%d%H%M%S")
                        if file_date >= cutoff:
                            gkg_files.append(file_url)
                        if len(gkg_files) >= 3:
                            break
                    except (ValueError, IndexError):
                        continue
            
            for file_url in gkg_files:
                if len(articles) >= max_records:
                    break
                try:
                    response = await self.client.get(file_url)
                    response.raise_for_status()
                    content = response.content
                    
                    import zipfile
                    with zipfile.ZipFile(io.BytesIO(content)) as zf:
                        for name in zf.namelist():
                            if name.endswith(".csv"):
                                with zf.open(name) as f:
                                    text = f.read().decode("utf-8", errors="ignore")
                                break
                    
                    reader = csv.reader(text.splitlines(), delimiter="\t")
                    for i, row in enumerate(reader):
                        if i >= max_records:
                            break
                        if len(row) < 25:
                            continue
                        article = self._parse_gkg_row(row)
                        if article:
                            articles.append(article)
                except Exception as e:
                    print(f"Error parsing GKG file {file_url}: {e}")
                    
        except Exception as e:
            print(f"Error in GKG discovery: {e}")
        return articles

    def _parse_gkg_row(self, row: List[str]) -> Optional[Dict[str, Any]]:
        """Parse GKG row."""
        try:
            date = row[0]
            source_id = row[1]
            url = row[2]
            title = row[5] if len(row) > 5 else ""
            language = row[3] if len(row) > 3 else "en"
            
            if not url or not url.startswith("http"):
                return None
            
            try:
                event_date = datetime.strptime(date[:8], "%Y%m%d")
            except ValueError:
                event_date = datetime.utcnow()
            
            return {
                "gdelt_gkg_id": f"{date}_{source_id}",
                "date": event_date,
                "url": url,
                "title": title,
                "language": language,
                "source_id": source_id,
            }
        except Exception:
            return None


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
            source_store.create(source)
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
            source_store.create(source)
            run.sources_succeeded += 1
        
        run.finished_at = datetime.utcnow()
        run.status = "completed"
        run_store.update(run.id, run.model_dump(mode="json"))
        
        return {
            "run_id": run.id,
            "events_found": len(events),
            "articles_found": len(articles),
            "sources_created": run.sources_succeeded,
        }
    except Exception as e:
        run.finished_at = datetime.utcnow()
        run.status = "failed"
        run.errors.append(str(e))
        run_store.update(run.id, run.model_dump(mode="json"))
        raise
    finally:
        await discovery.close()

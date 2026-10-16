from __future__ import annotations

import asyncio
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable
from urllib.parse import urlparse

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from pydantic import BaseModel, HttpUrl

from app.models.schemas import SourceType
from app.utils import canonicalize_url, compute_article_hash
from app.config import SourceConfig, get_settings


class SourceRole(str, Enum):
    DISCOVERY = "discovery"
    EVIDENCE = "evidence"
    STRUCTURED_DATA = "structured_data"


class ConnectorType(str, Enum):
    RSS = "rss"
    NEWS_SITE = "news_site"
    API = "api"
    DATA_PROVIDER = "data_provider"
    GENERIC = "generic"


@runtime_checkable
class SourceAdapter(Protocol):
    @property
    def connector_type(self) -> ConnectorType: ...
    @property
    def source_roles(self) -> List[SourceRole]: ...
    @property
    def connector_name(self) -> str: ...
    async def fetch_new_records(self, since: Optional[datetime] = None, max_records: Optional[int] = None) -> List["NormalizedSourceRecord"]: ...
    async def normalize_record(self, raw_record: Any) -> "NormalizedSourceRecord": ...
    def supports_incremental_fetch(self) -> bool: ...
    def get_ingestion_stats(self) -> "IngestionStats": ...
    async def health_check(self) -> bool: ...
    async def close(self) -> None: ...


@dataclass
class NormalizedSourceRecord:
    source_record_id: str
    publisher: str
    publisher_domain: str
    original_url: str
    canonical_url: str
    article_title: str
    publication_time: Optional[datetime]
    retrieval_time: datetime
    language: str = "en"
    available_text: Optional[str] = None
    original_source_type: SourceType = SourceType.NEWS
    content_hash: Optional[str] = None
    syndication_hints: List[str] = field(default_factory=list)
    origin_hints: List[str] = field(default_factory=list)
    connector_metadata: Dict[str, Any] = field(default_factory=dict)
    author: Optional[str] = None
    publisher_domain_verified: bool = False
    license_notes: Optional[str] = None
    robots_allowed: bool = True
    paywall_detected: bool = False
    content_type: Optional[str] = None


@dataclass
class IngestionStats:
    connector_name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    records_fetched: int = 0
    records_normalized: int = 0
    records_stored: int = 0
    duplicates_skipped: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    fetch_duration_seconds: float = 0.0
    normalization_duration_seconds: float = 0.0
    storage_duration_seconds: float = 0.0
    
    @property
    def success(self) -> bool:
        return len(self.errors) == 0
    @property
    def total_duration_seconds(self) -> float:
        return (self.fetch_duration_seconds + self.normalization_duration_seconds + self.storage_duration_seconds)


class IngestionResult(BaseModel):
    success: bool
    records: List[NormalizedSourceRecord] = field(default_factory=list)
    stats: Optional["IngestionStats"] = None
    error: Optional[str] = None


class BaseSourceAdapter(ABC):
    def __init__(self, connector_name: str, connector_type: ConnectorType, source_roles: List[SourceRole], source_config: Optional[Any] = None):
        self._connector_name = connector_name
        self._connector_type = connector_type
        self._source_roles = source_roles
        self._source_config = None
        self._stats = IngestionStats(connector_name=connector_name)
    
    @property
    def connector_type(self) -> ConnectorType:
        return self._connector_type
    @property
    def source_roles(self) -> List[SourceRole]:
        return self._source_roles
    @property
    def connector_name(self) -> str:
        return self._connector_name
    def supports_incremental_fetch(self) -> bool: return True
    def get_ingestion_stats(self) -> "IngestionStats": return self._stats
    @abstractmethod
    async def fetch_new_records(self, since: Optional[datetime] = None, max_records: Optional[int] = None) -> List["NormalizedSourceRecord"]: ...
    @abstractmethod
    async def normalize_record(self, raw_record: Any) -> "NormalizedSourceRecord": ...
    async def health_check(self) -> bool: return True
    async def close(self) -> None: pass
    def _generate_source_record_id(self, publisher: str, url: str) -> str:
        content = f"{publisher}:{url}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    def _extract_publisher_domain(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            if domain.startswith("www."): domain = domain[4:]
            return domain
        except Exception: return "unknown"
    def _canonicalize_url(self, url: str) -> str:
        from app.utils import canonicalize_url
        return canonicalize_url(url)
    def _compute_content_hash(self, title: str, url: str) -> str:
        from app.utils import compute_article_hash
        return compute_article_hash(title, url)


# RSS Adapter implementation - standalone functions to avoid any coroutine issues
def _extract_publisher_domain(url: str) -> str:
    """Extract publisher domain from URL."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return "unknown"


def _generate_source_record_id(publisher: str, url: str) -> str:
    content = f"{publisher}:{url}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _normalize_article(article: "FetchResult", source_config: "SourceConfig") -> Dict[str, Any]:
    """Normalize a FetchResult into a normalized source record - standalone function to avoid coroutine issues."""
    from app.utils import canonicalize_url, compute_article_hash
    from urllib.parse import urlparse
    
    article_title = article.title or "Untitled"
    final_url = article.final_url
    publisher_domain = _extract_publisher_domain(final_url)
    canonical_url = canonicalize_url(final_url)
    content_hash = compute_article_hash(article_title, final_url)
    
    pub_time = article.published_at
    if pub_time is not None:
        if isinstance(pub_time, datetime):
            pub_time_str = pub_time.isoformat()
        else:
            pub_time_str = str(pub_time)
    else:
        pub_time_str = None
    
    return {
        "source_record_id": _generate_source_record_id(source_config.name, final_url),
        "publisher": source_config.name,
        "publisher_domain": _extract_publisher_domain(final_url),
        "original_url": final_url,
        "canonical_url": canonicalize_url(final_url),
        "article_title": article.title or "Untitled",
        "publication_time": pub_time_str,
        "retrieval_time": datetime.utcnow().isoformat(),
        "language": source_config.language or "en",
        "available_text": article.content,
        "original_source_type": "news",
        "content_hash": compute_article_hash(article_title, final_url),
        "syndication_hints": [],
        "origin_hints": [],
        "connector_metadata": {"feed_url": str(source_config.url), "feed_name": source_config.name},
        "author": article.author,
        "publisher_domain_verified": False,
        "paywall_detected": article.paywall_detected,
        "content_type": article.content_type,
    }


def _extract_publisher_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return "unknown"


def _generate_source_record_id(publisher: str, url: str) -> str:
    content = f"{publisher}:{url}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


class RSSAdapter:
    """Generic RSS/Atom feed adapter implementing SourceAdapter protocol."""
    
    def __init__(self, source_config: "SourceConfig"):
        self._source_config = source_config
        self._connector_name = f"rss_{source_config.name}"
        self._connector_type = "rss"
        self._source_roles = ["discovery", "evidence"]
        self._source_config = source_config
        self._stats = {
            "connector_name": f"rss_{source_config.name}",
            "start_time": None,
            "end_time": None,
            "records_fetched": 0,
            "records_normalized": 0,
            "records_stored": 0,
            "duplicates_skipped": 0,
            "errors": [],
            "warnings": [],
            "fetch_duration_seconds": 0.0,
            "normalization_duration_seconds": 0.0,
            "storage_duration_seconds": 0.0,
        }
    
    @property
    def connector_type(self) -> str:
        return "rss"
    
    @property
    def source_roles(self) -> List[str]:
        return ["discovery", "evidence"]
    
    @property
    def connector_name(self) -> str:
        return self._connector_name
    
    def supports_incremental_fetch(self) -> bool:
        return True
    
    def get_ingestion_stats(self) -> Dict[str, Any]:
        return self._stats
    
    async def fetch_new_records(
        self, 
        since: Optional[datetime] = None,
        max_records: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch new records from RSS feed."""
        from app.sources.fetcher import fetch_from_multiple_sources
        from app.config import SourceConfig as ConfigSourceConfig
        from pydantic import HttpUrl
        
        self._stats["start_time"] = datetime.utcnow()
        self._stats["records_fetched"] = 0
        self._stats["records_normalized"] = 0
        self._stats["errors"] = []
        self._stats["warnings"] = []
        
        try:
            fetcher_config = ConfigSourceConfig(
                name=self._source_config.name,
                url=HttpUrl(str(self._source_config.url)),
                type=self._source_config.type,
                country=self._source_config.country,
                language=self._source_config.language,
                priority=self._source_config.priority,
                poll_interval_hours=self._source_config.poll_interval_hours,
                robots_policy=self._source_config.robots_policy,
                enabled=self._source_config.enabled,
                reason=self._source_config.reason,
            )
            
            from app.sources.fetcher import fetch_from_multiple_sources
            results = await fetch_from_multiple_sources([fetcher_config], max_concurrent=1)
            articles = results.get(self._source_config.name, [])
            
            self._stats["records_fetched"] = len(articles)
            print(f"DEBUG: Fetched {len(articles)} articles from feed")
            
            normalized = []
            for i, article in enumerate(articles):
                print(f"DEBUG: Processing article {i}: {article.title[:50] if article.title else 'No title'}")
                print(f"  success={article.success}, error={article.error}")
                print(f"  published_at={article.published_at} (type={type(article.published_at)})")
                if not article.success:
                    self._stats.setdefault("warnings", []).append(f"Failed to fetch {article.final_url}: {article.error}")
                    continue
                
                try:
                    normalized_record = _normalize_article(article, self._source_config)
                    self._stats["records_normalized"] = self._stats.get("records_normalized", 0) + 1
                    normalized.append(normalized_record)
                    print(f"  Normalized OK: {normalized_record['article_title'][:50]}")
                except Exception as e:
                    print(f"  ERROR normalizing: {e}")
                    import traceback
                    traceback.print_exc()
                    self._stats.setdefault("warnings", []).append(f"Failed to normalize {article.final_url}: {e}")
            
            self._stats["end_time"] = datetime.utcnow()
            if self._stats["start_time"]:
                if isinstance(self._stats["start_time"], str):
                    start = datetime.fromisoformat(self._stats["start_time"])
                else:
                    start = self._stats["start_time"]
                self._stats["fetch_duration_seconds"] = (datetime.utcnow() - start).total_seconds()
            
            print(f"DEBUG: Returning {len(normalized)} normalized records")
            return normalized
            
        except Exception as e:
            self._stats.setdefault("errors", []).append(str(e))
            import traceback
            traceback.print_exc()
            return []
        finally:
            pass
    
    def _normalize_article(self, article: "FetchResult") -> Dict[str, Any]:
        """This method is deprecated - use the standalone _normalize_article function instead."""
        raise NotImplementedError("Use the standalone _normalize_article function")
    
    def _extract_publisher_domain(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
            return domain
        except Exception:
            return "unknown"
    
    def _generate_source_record_id(self, publisher: str, url: str) -> str:
        content = f"{publisher}:{url}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def get_ingestion_stats(self) -> Dict[str, Any]:
        return self._stats
    
    async def health_check(self) -> bool:
        try:
            from app.sources.fetcher import fetch_from_multiple_sources
            from app.config import SourceConfig as ConfigSourceConfig
            from pydantic import HttpUrl
            fetcher_config = ConfigSourceConfig(
                name=self._source_config.name, url=HttpUrl(str(self._source_config.url)),
                type=self._source_config.type, country=self._source_config.country,
                language=self._source_config.language, priority=self._source_config.priority,
                poll_interval_hours=self._source_config.poll_interval_hours,
                robots_policy=self._source_config.robots_policy, enabled=self._source_config.enabled,
                reason=self._source_config.reason,
            )
            from app.sources.fetcher import fetch_from_multiple_sources
            results = await fetch_from_multiple_sources([fetcher_config], max_concurrent=1)
            articles = results.get(self._source_config.name, [])
            return any(r.success for r in articles)
        except Exception:
            return False
    
    async def close(self) -> None:
        pass


class ReliefWebAdapter:
    """ReliefWeb API v2 adapter implementing SourceAdapter protocol."""
    
    def __init__(self, source_config: "SourceConfig"):
        self._source_config = source_config
        self._connector_name = f"reliefweb_{source_config.name}"
        self._connector_type = "api"
        self._source_roles = ["discovery", "evidence", "structured_data"]
        self._source_config = source_config
        self._client = None
        self._stats = {
            "connector_name": f"reliefweb_{source_config.name}",
            "start_time": None,
            "end_time": None,
            "records_fetched": 0,
            "records_normalized": 0,
            "records_stored": 0,
            "duplicates_skipped": 0,
            "errors": [],
            "warnings": [],
            "fetch_duration_seconds": 0.0,
            "normalization_duration_seconds": 0.0,
            "storage_duration_seconds": 0.0,
        }
    
    @property
    def connector_type(self) -> str:
        return "api"
    
    @property
    def source_roles(self) -> List[str]:
        return ["discovery", "evidence", "structured_data"]
    
    @property
    def connector_name(self) -> str:
        return self._connector_name
    
    def supports_incremental_fetch(self) -> bool:
        return True
    
    def get_ingestion_stats(self) -> Dict[str, Any]:
        return self._stats
    
    def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client with proper configuration."""
        if self._client is None:
            from app.config import get_settings
            settings = get_settings()
            
            # Check for ReliefWeb app name
            app_name = getattr(settings, 'reliefweb_appname', None)
            if not app_name:
                raise ValueError("RELIEFWEB_APPNAME not configured in settings")
            
            headers = {
                "User-Agent": f"HUMANITY-Bot/1.0 (ReliefWeb API; {app_name})",
                "Accept": "application/json",
            }
            
            timeout = httpx.Timeout(
                connect=self._source_config.timeout_seconds or 30.0,
                read=60.0,
                write=10.0,
                pool=10.0
            )
            limits = httpx.Limits(max_connections=2, max_keepalive_connections=1)
            
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
                follow_redirects=True,
                headers=headers,
            )
        return self._client
    
    def _build_api_url(self, params: Dict[str, Any]) -> str:
        """Build the ReliefWeb API URL with query parameters."""
        base_url = "https://api.reliefweb.int/v2/reports"
        params = {
            "appname": getattr(get_settings(), 'reliefweb_appname', None) or "humanity-events",
            "profile": "full",
            "limit": 100,  # Max per request
            "preserve_facets": "true",
            "fields[report]": "id,title,url,body,date.created,date.changed,date.original,language,countries,primary_country,disasters,theme,format,source,origin",
            "sort": "-date.created",
        }
        params.update(params)
        query_string = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        return f"https://api.reliefweb.int/v2/reports?{query_string}"
    
    def _build_filter_params(self, since: Optional[datetime] = None, max_records: Optional[int] = None) -> Dict[str, Any]:
        """Build filter parameters for the API request."""
        params = {}
        
        # Build filter for date range
        filters = []
        
        if since:
            date_str = since.strftime("%Y-%m-%dT%H:%M:%S")
            filters.append(f"date.created:>={date_str}")
        
        # Add filters for relevant topics
        # Focus on humanitarian, conflict, disaster, displacement
        filters.append("format:report")
        
        if filters:
            params["filter"] = " AND ".join(filters)
        
        if max_records:
            params["limit"] = min(max_records, 1000)
        else:
            params["limit"] = 100
            
        return params
    
    def _normalize_report(self, report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalize a ReliefWeb report into a NormalizedSourceRecord."""
        try:
            fields = report.get("fields", {})
            
            # Extract basic fields
            report_id = report.get("id", "")
            title = fields.get("title", "Untitled")
            url = fields.get("url", "")
            body = fields.get("body", "")
            summary = fields.get("summary", "")
            
            # Dates
            date_created = fields.get("date", {}).get("created")
            date_changed = fields.get("date", {}).get("changed")
            date_original = fields.get("date", {}).get("original")
            
            pub_time = None
            if date_created:
                try:
                    pub_time = datetime.fromisoformat(date_created.replace("Z", "+00:00"))
                except:
                    pass
            
            # Extract countries
            countries = fields.get("countries", [])
            country_code = None
            country_name = None
            if countries:
                country_code = countries[0].get("iso3", "")
                country_name = countries[0].get("name", "")
            
            # Extract primary country
            primary_country = fields.get("primary_country", {})
            if primary_country and not country_code:
                country_code = primary_country.get("iso3", "")
                country_name = primary_country.get("name", "")
            
            # Extract themes/disasters
            disasters = fields.get("disasters", [])
            disaster_names = [d.get("name", "") for d in disasters]
            
            # Extract themes
            themes = fields.get("theme", [])
            theme_names = [t.get("name", "") for t in themes]
            
            # Extract source
            sources = fields.get("source", [])
            source_names = [s.get("name", "") for s in sources]
            
            # Build available text
            available_text = body or summary or ""
            
            # Generate content hash
            content_hash = compute_article_hash(title or "", fields.get("url", "") or "")
            
            # Generate canonical URL
            original_url = fields.get("url", "")
            canonical_url = canonicalize_url(original_url) if original_url else ""
            
            # Build connector metadata
            connector_metadata = {
                "reliefweb_id": report_id,
                "disasters": disaster_names,
                "themes": theme_names,
                "sources": source_names,
                "date_changed": date_changed,
                "date_original": date_original,
            }
            
            # Determine original source type
            source_type = "structured_data"
            
            # Parse publication time
            pub_time = None
            if date_created:
                try:
                    pub_time = datetime.fromisoformat(date_created.replace("Z", "+00:00"))
                except:
                    pub_time = datetime.utcnow()
            
            return {
                "source_record_id": self._generate_source_record_id(
                    f"ReliefWeb-{report_id}", fields.get("url", "")
                ),
                "publisher": "ReliefWeb",
                "publisher_domain": "reliefweb.int",
                "original_url": fields.get("url", ""),
                "canonical_url": canonicalize_url(fields.get("url", "")) if fields.get("url") else "",
                "article_title": title or "Untitled",
                "publication_time": pub_time.isoformat() if pub_time else None,
                "retrieval_time": datetime.utcnow().isoformat(),
                "language": fields.get("language", "en"),
                "available_text": body or "",
                "original_source_type": "structured_data",
                "content_hash": compute_article_hash(
                    title or "",
                    fields.get("url", "") or ""
                ),
                "syndication_hints": [],
                "origin_hints": [f"reliefweb_id:{report_id}"],
                "connector_metadata": connector_metadata,
                "author": None,
                "publisher_domain_verified": True,
                "paywall_detected": False,
                "content_type": "application/json",
            }
        except Exception as e:
            print(f"Error normalizing ReliefWeb report: {e}")
            return None
    
    async def fetch_new_records(
        self,
        since: Optional[datetime] = None,
        max_records: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch new records from ReliefWeb API."""
        self._stats["start_time"] = datetime.utcnow()
        self._stats["records_fetched"] = 0
        self._stats["records_normalized"] = 0
        self._stats["errors"] = []
        self._stats["warnings"] = []
        
        try:
            client = self._get_client()
            
            all_records = []
            offset = 0
            limit = 100
            max_to_fetch = max_records or self._source_config.max_articles_per_fetch or 100
            
            while len(all_records) < max_to_fetch:
                params = self._build_filter_params(since, max_to_fetch - len(all_records))
                params["offset"] = offset
                params["limit"] = min(limit, max_to_fetch - len(all_records))
                
                url = "https://api.reliefweb.int/v2/reports"
                params_with_app = {
                    "appname": getattr(get_settings(), 'reliefweb_appname', None) or "humanity-events",
                    "profile": "full",
                    "limit": min(limit, max_to_fetch - len(all_records)),
                    "preserve_facets": "true",
                    "fields[report]": "id,title,url,body,summary,date.created,date.changed,date.original,language,countries,primary_country,disasters,theme,format,source,origin",
                    "sort": "-date.created",
                }
                if since:
                    date_str = since.strftime("%Y-%m-%dT%H:%M:%S")
                    params["filter"] = f"date.created:>={date_str}"
                
                params.update(params_with_app)
                
                print(f"DEBUG: Fetching ReliefWeb page {offset // 100 + 1}")
                
                try:
                    response = await self._client.get(url, params=params)
                    response.raise_for_status()
                    data = response.json()
                    
                    reports = data.get("data", [])
                    if not reports:
                        break
                    
                    for report in reports:
                        normalized = self._normalize_report(report)
                        if normalized:
                            all_records.append(normalized)
                    
                    # Check if we have more pages
                    total_count = data.get("totalCount", 0)
                    if len(all_records) >= total_count or len(reports) == 0:
                        break
                    
                    offset += len(reports)
                    
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:
                        # Rate limited - respect Retry-After
                        retry_after = int(e.response.headers.get("Retry-After", "60"))
                        await asyncio.sleep(min(retry_after, 120))
                        continue
                    elif e.response.status_code >= 500:
                        # Server error - retry
                        self._stats.setdefault("warnings", []).append(f"Server error {e.response.status_code}, retrying...")
                        await asyncio.sleep(5)
                        continue
                    else:
                        self._stats.setdefault("errors", []).append(f"HTTP error: {e}")
                        break
                        
                except httpx.TimeoutException:
                    self._stats.setdefault("warnings", []).append("Request timeout")
                    break
                except Exception as e:
                    self._stats.setdefault("errors", []).append(str(e))
                    break
                
                offset += limit
                await asyncio.sleep(0.5)  # Rate limiting
            
            self._stats["end_time"] = datetime.utcnow()
            if self._stats["start_time"]:
                start = datetime.fromisoformat(self._stats["start_time"]) if isinstance(self._stats["start_time"], str) else self._stats["start_time"]
                self._stats["fetch_duration_seconds"] = (datetime.utcnow() - start).total_seconds()
            
            return all_records
            
        except Exception as e:
            self._stats.setdefault("errors", []).append(str(e))
            import traceback
            traceback.print_exc()
            return []
        finally:
            pass
    
    def _normalize_report(self, report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalize a ReliefWeb report into a NormalizedSourceRecord."""
        try:
            fields = report.get("fields", {})
            report_id = report.get("id", "")
            title = fields.get("title", "Untitled")
            url = fields.get("url", "")
            body = fields.get("body", "")
            summary = fields.get("summary", "")
            
            # Dates
            date_created = fields.get("date", {}).get("created")
            date_changed = fields.get("date", {}).get("changed")
            date_original = fields.get("date", {}).get("original")
            
            pub_time = None
            if date_created:
                try:
                    pub_time = datetime.fromisoformat(date_created.replace("Z", "+00:00"))
                except:
                    pass
            
            # Countries
            countries = fields.get("countries", [])
            country_code = ""
            country_name = ""
            if countries:
                country_code = countries[0].get("iso3", "")
                country_name = countries[0].get("name", "")
            
            primary_country = fields.get("primary_country", {})
            if primary_country and not country_code:
                country_code = primary_country.get("iso3", "")
                country_name = primary_country.get("name", "")
            
            # Disasters and themes
            disasters = fields.get("disasters", [])
            disaster_names = [d.get("name", "") for d in disasters]
            
            themes = fields.get("theme", [])
            theme_names = [t.get("name", "") for t in themes]
            
            # Sources
            sources = fields.get("source", [])
            source_names = [s.get("name", "") for s in sources]
            
            # Build available text
            available_text = body or summary or ""
            
            # Generate content hash
            content_hash = compute_article_hash(title or "", url or "")
            
            # Canonical URL
            canonical_url = canonicalize_url(url) if url else ""
            
            # Connector metadata
            connector_metadata = {
                "reliefweb_id": report_id,
                "disasters": disaster_names,
                "themes": theme_names,
                "sources": source_names,
                "date_changed": date_changed,
                "date_original": date_original,
            }
            
            # Parse publication time
            pub_time = None
            if date_created:
                try:
                    pub_time = datetime.fromisoformat(date_created.replace("Z", "+00:00"))
                except:
                    pub_time = datetime.utcnow()
            
            return {
                "source_record_id": self._generate_source_record_id(
                    f"ReliefWeb-{report_id}", url
                ),
                "publisher": "ReliefWeb",
                "publisher_domain": "reliefweb.int",
                "original_url": url,
                "canonical_url": canonical_url,
                "article_title": title or "Untitled",
                "publication_time": pub_time.isoformat() if pub_time else None,
                "retrieval_time": datetime.utcnow().isoformat(),
                "language": fields.get("language", "en"),
                "available_text": body or summary or "",
                "original_source_type": "structured_data",
                "content_hash": compute_article_hash(title or "", url or ""),
                "syndication_hints": [],
                "origin_hints": [f"reliefweb_id:{report_id}"],
                "connector_metadata": connector_metadata,
                "author": None,
                "publisher_domain_verified": True,
                "paywall_detected": False,
                "content_type": "application/json",
            }
        except Exception as e:
            print(f"Error normalizing ReliefWeb report: {e}")
            return None
    
    async def fetch_new_records(
        self,
        since: Optional[datetime] = None,
        max_records: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Fetch new records from ReliefWeb API."""
        self._stats["start_time"] = datetime.utcnow()
        self._stats["records_fetched"] = 0
        self._stats["records_normalized"] = 0
        self._stats["errors"] = []
        self._stats["warnings"] = []
        
        if not hasattr(self, '_client') or self._client is None:
            self._client = self._get_client()
        
        all_records = []
        try:
            client = self._get_client()
            
            all_records = []
            offset = 0
            limit = 100
            max_to_fetch = max_records or self._source_config.max_articles_per_fetch or 100
            
            while len(all_records) < max_to_fetch:
                params = self._build_filter_params(since, max_to_fetch - len(all_records))
                params["offset"] = offset
                params["limit"] = min(limit, max_to_fetch - len(all_records))
                
                url = "https://api.reliefweb.int/v2/reports"
                params_with_app = {
                    "appname": getattr(get_settings(), 'reliefweb_appname', None) or "humanity-events",
                    "profile": "full",
                    "limit": min(limit, max_to_fetch - len(all_records)),
                    "preserve_facets": "true",
                    "fields[report]": "id,title,url,body,summary,date.created,date.changed,date.original,language,countries,primary_country,disasters,theme,format,source,origin",
                    "sort": "-date.created",
                }
                if since:
                    date_str = since.strftime("%Y-%m-%dT%H:%M:%S")
                    params["filter"] = f"date.created:>={date_str}"
                
                params.update(params_with_app)
                
                print(f"DEBUG: Fetching ReliefWeb page {offset // 100 + 1}")
                
                try:
                    response = await self._client.get(url, params=params)
                    response.raise_for_status()
                    data = response.json()
                    
                    reports = data.get("data", [])
                    if not reports:
                        break
                    
                    for report in reports:
                        normalized = self._normalize_report(report)
                        if normalized:
                            all_records.append(normalized)
                    
                    # Check if we have more pages
                    total_count = data.get("totalCount", 0)
                    if len(all_records) >= total_count or len(reports) == 0:
                        break
                    
                    offset += len(reports)
                    
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:
                        retry_after = int(e.response.headers.get("Retry-After", "60"))
                        await asyncio.sleep(min(retry_after, 120))
                        continue
                    elif e.response.status_code >= 500:
                        self._stats.setdefault("warnings", []).append(f"Server error {e.response.status_code}, retrying...")
                        await asyncio.sleep(5)
                        continue
                    else:
                        self._stats.setdefault("errors", []).append(f"HTTP error: {e}")
                        break
                        
                except httpx.TimeoutException:
                    self._stats.setdefault("warnings", []).append("Request timeout")
                    break
                except Exception as e:
                    self._stats.setdefault("errors", []).append(str(e))
                    break
                
                offset += limit
                await asyncio.sleep(0.5)  # Rate limiting
            
            self._stats["end_time"] = datetime.utcnow()
            if self._stats["start_time"]:
                start = datetime.fromisoformat(self._stats["start_time"]) if isinstance(self._stats["start_time"], str) else self._stats["start_time"]
                self._stats["fetch_duration_seconds"] = (datetime.utcnow() - start).total_seconds()
            
            return all_records
            
        except Exception as e:
            self._stats.setdefault("errors", []).append(str(e))
            import traceback
            traceback.print_exc()
            return []
        finally:
            pass
    
    def get_ingestion_stats(self) -> Dict[str, Any]:
        return self._stats
    
    async def health_check(self) -> bool:
        try:
            client = self._get_client()
            response = await client.get(
                "https://api.reliefweb.int/v2/reports",
                params={"appname": "humanity-events", "limit": 1},
                timeout=10.0
            )
            return response.status_code == 200
        except Exception:
            return False
    
    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


# Update the required imports section at the end of the file
from typing import Any, Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse

class SourceRole(str, Enum):
    DISCOVERY = "discovery"
    EVIDENCE = "evidence"
    STRUCTURED_DATA = "structured_data"

class ConnectorType(str, Enum):
    RSS = "rss"
    NEWS_SITE = "news_site"
    API = "api"
    DATA_PROVIDER = "data_provider"
    GENERIC = "generic"

class ConnectorProtocol:
    connector_type: str
    source_roles: List[str]
    connector_name: str
    async def fetch_new_records(self, since: Optional[datetime] = None, max_records: Optional[int] = None) -> List[Dict[str, Any]]: ...
    async def normalize_record(self, raw_record: Any) -> Dict[str, Any]: ...
    def supports_incremental_fetch(self) -> bool: ...
    def get_ingestion_stats(self) -> Dict[str, Any]: ...
    async def health_check(self) -> bool: ...
    async def close(self) -> None: ...

# Required imports at module level
import asyncio
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable
from urllib.parse import urlparse
from pydantic import BaseModel, HttpUrl
from app.models.schemas import SourceType
from app.utils import canonicalize_url, compute_article_hash
from app.config import SourceConfig
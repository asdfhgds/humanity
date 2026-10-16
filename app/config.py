from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, HttpUrl
from pydantic_settings import BaseSettings


class SourceConfig(BaseModel):
    name: str
    url: HttpUrl
    type: str
    country: str
    language: str
    priority: int
    poll_interval_hours: int
    robots_policy: str
    enabled: bool
    reason: str
    roles: List[str] = ["discovery", "evidence"]
    connector_type: str = "rss"
    timeout_seconds: int = 30
    rate_limit_per_minute: int = 30
    rate_limit_per_hour: int = 500
    max_articles_per_fetch: int = 50
    fetch_full_content: bool = True
    respect_robots_txt: bool = True


class TopicConfig(BaseModel):
    event_types: List[Dict[str, Any]]
    severity_scale: Dict[int, str]
    status_values: List[str]
    confidence_levels: List[str]
    verification_status: List[str]
    source_types: List[str]
    claim_types: List[str]


class CountryConfig(BaseModel):
    code: str
    name: str
    region: str
    subregion: str
    priority: int
    conflict_zones: List[str] = []
    monitored: bool
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class ScoringConfig(BaseModel):
    weights: Dict[str, float]
    penalties: Dict[str, float]
    thresholds: Dict[str, Any]
    importance_factors: Dict[str, Any]
    score_caps: Dict[str, int]
    daily_brief: Dict[str, Any]
    overlooked_engine: Dict[str, Any]


class Settings(BaseSettings):
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"
    tavily_api_key: str = ""
    cloudflare_api_token: str = ""
    cloudflare_account_id: str = ""
    reliefweb_appname: str = ""

    max_articles_per_run: int = 1000
    max_llm_calls_per_run: int = 100
    max_search_calls_per_run: int = 50
    min_publish_score: float = 70.0
    min_confidence: float = 0.6
    enable_tavily: bool = False
    enable_autopublish: bool = True
    production_mode: bool = False

    data_dir: Path = Path("data")
    config_dir: Path = Path("config")
    web_dir: Path = Path("web")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def load_sources() -> Dict[str, List[SourceConfig]]:
    config_path = get_settings().config_dir / "sources.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    result = {}
    for category, sources in data.items():
        result[category] = [SourceConfig(**s) for s in sources]
    return result


@lru_cache
def load_topics() -> TopicConfig:
    config_path = get_settings().config_dir / "topics.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return TopicConfig(**data)


@lru_cache
def load_countries() -> List[CountryConfig]:
    config_path = get_settings().config_dir / "countries.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [CountryConfig(**c) for c in data["countries"]]


@lru_cache
def load_scoring() -> ScoringConfig:
    config_path = get_settings().config_dir / "scoring.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return ScoringConfig(**data)


def get_enabled_sources() -> List[SourceConfig]:
    all_sources = load_sources()
    enabled = []
    for category_sources in all_sources.values():
        enabled.extend([s for s in category_sources if s.enabled])
    return sorted(enabled, key=lambda s: -s.priority)


def get_country_by_code(code: str) -> Optional[CountryConfig]:
    for country in load_countries():
        if country.code == code.upper():
            return country
    return None


def get_conflict_zones_for_country(code: str) -> List[str]:
    country = get_country_by_code(code)
    return country.conflict_zones if country else []

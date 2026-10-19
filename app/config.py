from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

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
    roles: list[str] = ["discovery", "evidence"]
    connector_type: str = "rss"
    timeout_seconds: int = 30
    rate_limit_per_minute: int = 30
    rate_limit_per_hour: int = 500
    max_articles_per_fetch: int = 50
    fetch_full_content: bool = True
    respect_robots_txt: bool = True


class TopicConfig(BaseModel):
    event_types: list[dict[str, Any]]
    severity_scale: dict[int, str]
    status_values: list[str]
    confidence_levels: list[str]
    verification_status: list[str]
    source_types: list[str]
    claim_types: list[str]


class CountryConfig(BaseModel):
    code: str
    name: str
    region: str
    subregion: str
    priority: int
    conflict_zones: list[str] = []
    monitored: bool
    latitude: float | None = None
    longitude: float | None = None


class ScoringConfig(BaseModel):
    weights: dict[str, float]
    penalties: dict[str, float]
    thresholds: dict[str, Any]
    importance_factors: dict[str, Any]
    score_caps: dict[str, int]
    daily_brief: dict[str, Any]
    overlooked_engine: dict[str, Any]


class Settings(BaseSettings):
    gemini_api_keys: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"
    tavily_api_key: str = ""
    cloudflare_api_token: str = ""
    cloudflare_account_id: str = ""
    reliefweb_appname: str = ""

    max_articles_per_run: int = 500
    max_llm_calls_per_run: int = 25
    max_search_calls_per_run: int = 50
    min_publish_score: float = 70.0
    min_confidence: float = 0.6
    enable_tavily: bool = False
    enable_autopublish: bool = False
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
def load_sources() -> dict[str, list[SourceConfig]]:
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
def load_countries() -> list[CountryConfig]:
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


def get_enabled_sources() -> list[SourceConfig]:
    all_sources = load_sources()
    enabled = []
    for category_sources in all_sources.values():
        enabled.extend([s for s in category_sources if s.enabled])
    return sorted(enabled, key=lambda s: -s.priority)


def get_country_by_code(code: str) -> CountryConfig | None:
    for country in load_countries():
        if country.code == code.upper():
            return country
    return None


def get_conflict_zones_for_country(code: str) -> list[str]:
    country = get_country_by_code(code)
    return country.conflict_zones if country else []

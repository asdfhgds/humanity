from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, HttpUrl
import uuid


class EventType(str, Enum):
    PROTEST = "PROTEST"
    CONFLICT = "CONFLICT"
    ATTACK = "ATTACK"
    CASUALTY = "CASUALTY"
    DISPLACEMENT = "DISPLACEMENT"
    DISASTER = "DISASTER"
    REPRESSION = "REPRESSION"
    HUMANITARIAN = "HUMANITARIAN"
    DIPLOMACY = "DIPLOMACY"
    PEACE_PROCESS = "PEACE_PROCESS"


class EventStatus(str, Enum):
    DEVELOPING = "developing"
    ONGOING = "ongoing"
    CONTAINED = "contained"
    RESOLVED = "resolved"
    HISTORICAL = "historical"


class PublicationStatus(str, Enum):
    DISCOVERED = "discovered"
    EXTRACTED = "extracted"
    HELD = "held"
    PUBLISHED = "published"
    PUBLISHED_WITH_WARNING = "published_with_warning"
    REJECTED = "rejected"


class ConfidenceLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERIFIED = "VERIFIED"


class VerificationStatus(str, Enum):
    REPORTED = "reported"
    CORROBORATED = "corroborated"
    DISPUTED = "disputed"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"
    INDEPENDENTLY_VERIFIED = "independently_verified"


class SourceType(str, Enum):
    NEWS = "news"
    GOVERNMENT = "government"
    NGO = "ngo"
    UN = "un"
    ACADEMIC = "academic"
    DATASET = "dataset"
    SOCIAL = "social"
    OFFICIAL_STATEMENT = "official_statement"
    WIRE_SERVICE = "wire_service"


class SourceIndependence(str, Enum):
    INDEPENDENT = "independent"
    LIKELY_SYNDICATED = "likely_syndicated"
    LIKELY_COPIED = "likely_copied"
    UNKNOWN = "unknown"


class SourceRelationship(BaseModel):
    source_id_a: str
    source_id_b: str
    relationship: SourceIndependence
    evidence: List[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    detected_at: datetime = Field(default_factory=datetime.utcnow)


class ClaimType(str, Enum):
    CASUALTY = "casualty"
    PARTICIPANTS = "participants"
    ARRESTED = "arrested"
    DISPLACED = "displaced"
    DAMAGE = "damage"
    DURATION = "duration"
    PERPETRATOR = "perpetrator"
    TARGET = "target"
    WEAPON = "weapon"
    LOCATION = "location"
    DATE = "date"
    CAUSE = "cause"
    DEMAND = "demand"
    RESPONSE = "response"


class LocationPrecision(str, Enum):
    EXACT = "exact"
    CITY = "city"
    REGION = "region"
    COUNTRY = "country"
    UNKNOWN = "unknown"


class Source(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    publisher: str
    title: str
    url: HttpUrl
    image_url: Optional[HttpUrl] = None
    canonical_url: Optional[HttpUrl] = None
    published_at: Optional[datetime] = None
    retrieved_at: datetime = Field(default_factory=datetime.utcnow)
    language: str = "en"
    country: Optional[str] = None
    source_type: SourceType = SourceType.NEWS
    author: Optional[str] = None
    article_hash: Optional[str] = None
    content_hash: Optional[str] = None
    robots_allowed: bool = True
    license_notes: Optional[str] = None
    reliability_class: Optional[str] = None
    content: Optional[str] = None
    original_source_url: Optional[str] = None
    original_report_id: Optional[str] = None
    independence_classification: SourceIndependence = SourceIndependence.UNKNOWN
    source_relationships: List[SourceRelationship] = Field(default_factory=list)
    extraction_status: Optional[str] = None
    extraction_error_category: Optional[str] = None
    extraction_last_attempt_at: Optional[datetime] = None
    extraction_retry_after: Optional[datetime] = None


class Claim(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_id: str
    claim_type: ClaimType
    subject: str
    predicate: str
    value: Optional[float] = None
    unit: Optional[str] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    source_id: str
    source_quote: str
    source_location: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    verification_status: VerificationStatus = VerificationStatus.REPORTED
    created_at: datetime = Field(default_factory=datetime.utcnow)
    geography: Optional[str] = None
    reporting_period_start: Optional[datetime] = None
    reporting_period_end: Optional[datetime] = None
    normalized_value: Optional[float] = None
    normalized_unit: Optional[str] = None
    extraction_method: str = "unknown"
    extraction_model: Optional[str] = None
    extracted_at: datetime = Field(default_factory=datetime.utcnow)


class ClaimComparison(BaseModel):
    claim_id_a: str
    claim_id_b: str
    relationship: str
    details: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)


class EventIdentity(BaseModel):
    """Event identity information for stable event tracking across ingestions."""
    canonical_id: str  # stable hash of (type, location, date, actors)
    candidate_ids: List[str] = Field(default_factory=list)  # source record IDs that contributed
    match_confidence: float = Field(default=0.0, ge=0.0, le=1.0)  # confidence in this identity
    match_method: str = "deterministic"  # deterministic|model|hybrid
    matched_with: List[str] = Field(default_factory=list)  # other event IDs this merged with
    # Additive fields keep legacy JSON records readable while preserving why a
    # record was or was not attached to an existing real-world occurrence.
    decision: str = "DISTINCT_EVENT"
    decision_reasons: List[str] = Field(default_factory=list)
    aliases: List[str] = Field(default_factory=list)
    merge_history: List[Dict[str, Any]] = Field(default_factory=list)


class EvidenceAssessment(BaseModel):
    independent_source_count: int = 0
    syndicated_source_count: int = 0
    unknown_independence_count: int = 0
    contradiction_count: int = 0
    evidence_confidence: float = Field(ge=0.0, le=100.0)
    publication_ready: bool = False
    reasons: List[str] = Field(default_factory=list)


class Event(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    canonical_id: Optional[str] = None
    event_identity: Optional["EventIdentity"] = None
    evidence_assessment: Optional["EvidenceAssessment"] = None
    publication_reason: Optional[str] = None
    is_mock: bool = False
    event_type: EventType
    title: str
    slug: str
    description: str
    summary: str
    country_code: str
    country_name: str
    region: Optional[str] = None
    city: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_precision: LocationPrecision = LocationPrecision.UNKNOWN
    start_time: datetime
    end_time: Optional[datetime] = None
    severity: int = Field(ge=1, le=10, default=5)
    status: EventStatus = EventStatus.DEVELOPING
    publication_status: PublicationStatus = PublicationStatus.DISCOVERED
    confidence: ConfidenceLevel = ConfidenceLevel.LOW
    visibility: bool = True
    first_detected_at: datetime = Field(default_factory=datetime.utcnow)
    last_updated_at: datetime = Field(default_factory=datetime.utcnow)
    published_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    estimated_participants_min: Optional[int] = None
    estimated_participants_max: Optional[int] = None
    deaths_min: Optional[int] = None
    deaths_max: Optional[int] = None
    injured_min: Optional[int] = None
    injured_max: Optional[int] = None
    arrested_min: Optional[int] = None
    arrested_max: Optional[int] = None
    displaced_min: Optional[int] = None
    displaced_max: Optional[int] = None

    source_ids: List[str] = Field(default_factory=list)
    claim_ids: List[str] = Field(default_factory=list)
    related_event_ids: List[str] = Field(default_factory=list)
    conflict_id: Optional[str] = None
    topic_tags: List[str] = Field(default_factory=list)

    importance_score: float = 0.0
    novelty_score: float = 0.0
    corroboration_score: float = 0.0

    def get_death_range(self) -> Optional[str]:
        if self.deaths_min is not None and self.deaths_max is not None:
            if self.deaths_min == self.deaths_max:
                return str(self.deaths_min)
            return f"{self.deaths_min}–{self.deaths_max}"
        elif self.deaths_min is not None:
            return f"at least {self.deaths_min}"
        elif self.deaths_max is not None:
            return f"up to {self.deaths_max}"
        return None

    def get_injured_range(self) -> Optional[str]:
        if self.injured_min is not None and self.injured_max is not None:
            if self.injured_min == self.injured_max:
                return str(self.injured_min)
            return f"{self.injured_min}–{self.injured_max}"
        elif self.injured_min is not None:
            return f"at least {self.injured_min}"
        elif self.injured_max is not None:
            return f"up to {self.injured_max}"
        return None


class Location(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    country_code: str
    latitude: float
    longitude: float
    precision: LocationPrecision = LocationPrecision.CITY
    place_type: str = "city"
    admin1: Optional[str] = None
    admin2: Optional[str] = None
    population: Optional[int] = None
    timezone: Optional[str] = None
    geocoded_at: datetime = Field(default_factory=datetime.utcnow)
    source: str = "manual"


class Country(BaseModel):
    code: str
    name: str
    region: str
    subregion: str
    priority: int = 5
    conflict_zones: List[str] = Field(default_factory=list)
    monitored: bool = True
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class Conflict(BaseModel):
    id: str
    name: str
    description: str
    countries: List[str] = Field(default_factory=list)
    region: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    status: str = "active"
    event_ids: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    methodology: Optional[str] = None


class IngestionRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    sources_attempted: int = 0
    sources_succeeded: int = 0
    articles_seen: int = 0
    candidates_created: int = 0
    duplicates_removed: int = 0
    events_created: int = 0
    events_updated: int = 0
    events_published: int = 0
    events_held: int = 0
    llm_calls: int = 0
    search_calls: int = 0
    articles_filtered: int = 0
    duplicate_records_removed: int = 0
    claims_created: int = 0
    claims_compared: int = 0
    retries: int = 0
    source_health: Dict[str, str] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)
    status: str = "running"

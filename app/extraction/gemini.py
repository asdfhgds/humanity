from __future__ import annotations

import json
import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field, ValidationError
from google import genai
from google.genai import types
from app.config import get_settings
from app.utils import clean_text, truncate
from app.models.schemas import EventType, ClaimType, VerificationStatus


class ExtractedClaim(BaseModel):
    type: str
    subject: str
    predicate: str
    value: Optional[float] = None
    unit: Optional[str] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    source_quote: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)


class ExtractionResult(BaseModel):
    is_mock: bool = False
    is_event: bool
    event_type: Optional[str] = None
    title: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    date: Optional[str] = None
    claims: List[ExtractedClaim] = Field(default_factory=list)
    importance_score: float = Field(ge=0.0, le=100.0, default=50.0)
    confidence_score: float = Field(ge=0.0, le=100.0, default=50.0)
    publish_recommendation: str = "HOLD"
    summary: Optional[str] = None
    key_facts: List[str] = Field(default_factory=list)
    uncertainty_notes: List[str] = Field(default_factory=list)


EXTRACTION_PROMPT = """You are an event extraction system for HUMANITY, a global human events intelligence platform.

Analyze the provided news article and extract structured information about human events.

EVENT TYPES:
- PROTEST: Public demonstrations, rallies, strikes, civil disobedience
- CONFLICT: Armed battles, clashes, military operations between organized groups
- ATTACK: Airstrikes, bombings, explosions, targeted killings, drone strikes
- CASUALTY: Reported deaths or injuries of civilians or combatants
- DISPLACEMENT: Forced migration, refugees, IDPs, evacuations
- DISASTER: Natural disasters, industrial accidents, humanitarian emergencies
- REPRESSION: Arrests, detentions, censorship, torture, enforced disappearances
- HUMANITARIAN: Aid delivery, humanitarian emergencies, infrastructure damage
- DIPLOMACY: Diplomatic negotiations, summits, treaties, international agreements
- PEACE_PROCESS: Ceasefires, peace talks, peace agreements, demobilization

CLAIM TYPES:
- casualty: deaths, injuries
- participants: protest/event attendance
- arrested: detentions, arrests
- displaced: refugees, IDPs
- damage: infrastructure, buildings
- duration: how long event lasted
- perpetrator: who carried out action
- target: what/who was targeted
- weapon: weapons used
- location: where event occurred
- date: when event occurred
- cause: reason for protest/event
- demand: protester demands
- response: authority response

RULES:
1. Only extract claims EXPLICITLY stated in the text with direct quotes
2. Use min_value/max_value for ranges, never invent precise numbers
3. If a number is not in the text, do not include it
4. Distinguish between reported, estimated, and confirmed figures
5. Set confidence as a number between 0.0 and 1.0 based on source specificity and directness
6. If article does not describe a significant human event, set is_event=false
7. publish_recommendation: PUBLISH (high confidence, clear event), PUBLISH_WARNING (multiple sources disagree, uncertain numbers), HOLD (single weak source, unclear event)

REQUIRED JSON SCHEMA (must match exactly):
{
  "is_event": true,
  "event_type": "CONFLICT",
  "title": "Brief event title",
  "country": "US",
  "city": "City name or null",
  "latitude": 40.7128,
  "longitude": -74.0060,
  "date": "2026-10-11",
  "claims": [
    {
      "type": "casualty",
      "subject": "civilians",
      "predicate": "killed",
      "value": 25,
      "unit": "people",
      "min_value": 20,
      "max_value": 30,
      "source_quote": "exact quote from article supporting this claim",
      "confidence": 0.8
    }
  ],
  "importance_score": 75.0,
  "confidence_score": 80.0,
  "publish_recommendation": "PUBLISH",
  "summary": "One paragraph summary",
  "key_facts": ["fact 1", "fact 2"],
  "uncertainty_notes": ["uncertainty 1"]
}

Return ONLY valid JSON matching the schema above. Do not include any other text."""


@dataclass(frozen=True)
class ExtractionOutcome:
    result: Optional[ExtractionResult] = None
    error_category: Optional[str] = None
    attempt_errors: tuple[str, ...] = ()
    attempts: int = 0
    stop_batch: bool = False


@dataclass(frozen=True)
class _ErrorPolicy:
    category: str
    retryable: bool = False
    stop_batch: bool = False
    retry_after_seconds: Optional[float] = None


def _provider_status(error: Exception) -> tuple[Optional[int], str]:
    code = getattr(error, "code", None)
    status = getattr(error, "status", None)
    if not isinstance(code, int):
        code = getattr(getattr(error, "response", None), "status_code", None)
    return code if isinstance(code, int) else None, str(status or "").upper()


def _provider_message(error: Exception) -> str:
    message = getattr(error, "message", None)
    details = getattr(error, "details", None)
    values = [str(message or ""), str(details or "")]
    if not message and not details:
        values.append(str(error))
    return " ".join(values).lower()


def _retry_after_seconds(error: Exception) -> Optional[float]:
    direct = getattr(error, "retry_after", None)
    if isinstance(direct, (int, float)):
        return max(0.0, float(direct))
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        value = headers.get("retry-after")
        if value:
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                pass
    details = getattr(error, "details", None)
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            retry_delay = detail.get("retryDelay") or detail.get("retry_delay")
            if isinstance(retry_delay, str):
                match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)s\s*", retry_delay)
                if match:
                    return max(0.0, float(match.group(1)))
    return None


def _classify_api_error(error: Exception) -> _ErrorPolicy:
    code, status = _provider_status(error)
    message = _provider_message(error)
    name = type(error).__name__.lower()
    retry_after = _retry_after_seconds(error)

    if code == 401 or status in {"UNAUTHENTICATED", "AUTHENTICATION"}:
        return _ErrorPolicy("authentication_failure", stop_batch=True)
    if code == 403 or status in {"PERMISSION_DENIED", "PERMISSION"}:
        return _ErrorPolicy("permission_failure", stop_batch=True)
    if code == 402 or any(term in message for term in ("payment required", "billing disabled", "credits depleted")):
        return _ErrorPolicy("billing_or_quota_exhausted", stop_batch=True)

    rate_limited = any(
        term in message
        for term in (
            "rate_limit_exceeded",
            "too_many_requests",
            "requests per minute",
            "tokens per minute",
            "per-second limit",
            "per-minute limit",
            "retry after",
        )
    )
    daily_quota = any(
        term in message
        for term in ("daily quota", "quota exceeded", "quota_exceeded", "per day", "requests per day")
    )
    if status == "RESOURCE_EXHAUSTED" and not rate_limited:
        daily_quota = True
    if code == 429:
        if daily_quota or not rate_limited:
            return _ErrorPolicy("quota_exhausted", stop_batch=True)
        return _ErrorPolicy(
            "transient_rate_limit",
            retryable=True,
            retry_after_seconds=retry_after,
        )

    if code is not None and code >= 500:
        return _ErrorPolicy(
            "provider_server_error",
            retryable=True,
            retry_after_seconds=retry_after,
        )
    if isinstance(error, (TimeoutError, ConnectionError, OSError, httpx.TransportError)) or any(
        term in name for term in ("timeout", "connecterror", "networkerror", "transporterror")
    ):
        return _ErrorPolicy("network_failure", retryable=True)
    return _ErrorPolicy("provider_error", stop_batch=True)


class GeminiExtractor:
    MAX_RETRIES_PER_ARTICLE = 2
    MAX_PROVIDER_RETRY_WAIT_SECONDS = 60
    RETRY_BASE_SECONDS = 1

    def __init__(self, client: Optional[Any] = None, sleep=asyncio.sleep):
        settings = get_settings()
        self.api_key = settings.gemini_api_key
        self.model_name = settings.gemini_model
        self.enabled = bool(self.api_key)
        self.max_calls = max(0, settings.max_llm_calls_per_run)
        self.api_calls = 0
        self.quota_exhausted = False
        self.client = client or (genai.Client(api_key=self.api_key) if self.enabled else None)
        self._sleep = sleep

    def _build_prompt(self, title: str, content: str, url: str) -> str:
        content_clean = clean_text(truncate(content, 8000))
        return f"""{EXTRACTION_PROMPT}

ARTICLE:
Title: {title}
URL: {url}
Content: {content_clean}

Respond with valid JSON only."""

    async def extract_with_diagnostics(
        self, title: str, content: str, url: str
    ) -> ExtractionOutcome:
        if not self.enabled or self.client is None:
            return ExtractionOutcome(error_category="provider_disabled", stop_batch=True)
        if self.quota_exhausted:
            return ExtractionOutcome(error_category="not_attempted_quota_exhausted", stop_batch=True)

        prompt = self._build_prompt(title, content, url)
        attempt_errors: list[str] = []
        for attempt_index in range(self.MAX_RETRIES_PER_ARTICLE + 1):
            if self.api_calls >= self.max_calls:
                return ExtractionOutcome(
                    error_category="call_budget_exhausted",
                    attempt_errors=tuple(attempt_errors),
                    attempts=len(attempt_errors),
                    stop_batch=True,
                )
            self.api_calls += 1
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        response_mime_type="application/json",
                    ),
                )
                try:
                    response_text = response.text
                except (ValueError, AttributeError):
                    return ExtractionOutcome(
                        error_category="invalid_model_output",
                        attempt_errors=tuple([*attempt_errors, "invalid_model_output"]),
                        attempts=len(attempt_errors) + 1,
                    )
                if not response_text:
                    return ExtractionOutcome(
                        error_category="invalid_model_output",
                        attempt_errors=tuple([*attempt_errors, "invalid_model_output"]),
                        attempts=len(attempt_errors) + 1,
                    )
                try:
                    result = ExtractionResult.model_validate_json(response_text)
                except (ValidationError, ValueError, json.JSONDecodeError):
                    return ExtractionOutcome(
                        error_category="invalid_model_output",
                        attempt_errors=tuple([*attempt_errors, "invalid_model_output"]),
                        attempts=len(attempt_errors) + 1,
                    )
                if not result.is_event:
                    return ExtractionOutcome(
                        result=result,
                        error_category="non_event",
                        attempt_errors=tuple(attempt_errors),
                        attempts=len(attempt_errors) + 1,
                    )
                return ExtractionOutcome(
                    result=result,
                    attempt_errors=tuple(attempt_errors),
                    attempts=len(attempt_errors) + 1,
                )
            except Exception as error:
                policy = _classify_api_error(error)
                attempt_errors.append(policy.category)
                if policy.category == "quota_exhausted" or policy.category == "billing_or_quota_exhausted":
                    self.quota_exhausted = True
                    return ExtractionOutcome(
                        error_category=policy.category,
                        attempt_errors=tuple(attempt_errors),
                        attempts=len(attempt_errors),
                        stop_batch=True,
                    )
                if not policy.retryable or attempt_index >= self.MAX_RETRIES_PER_ARTICLE:
                    return ExtractionOutcome(
                        error_category=policy.category,
                        attempt_errors=tuple(attempt_errors),
                        attempts=len(attempt_errors),
                        stop_batch=policy.stop_batch or policy.retryable,
                    )
                delay = policy.retry_after_seconds
                if delay is None:
                    delay = self.RETRY_BASE_SECONDS * (2**attempt_index)
                if delay > self.MAX_PROVIDER_RETRY_WAIT_SECONDS:
                    return ExtractionOutcome(
                        error_category=policy.category,
                        attempt_errors=tuple(attempt_errors),
                        attempts=len(attempt_errors),
                        stop_batch=True,
                    )
                await self._sleep(delay)

        raise AssertionError("bounded extraction retry loop exited unexpectedly")

    async def extract(self, title: str, content: str, url: str) -> Optional[ExtractionResult]:
        """Compatibility wrapper; callers needing diagnostics should use detailed outcomes."""
        return (await self.extract_with_diagnostics(title, content, url)).result

    async def extract_many(self, articles: List[Dict[str, Any]]) -> List[ExtractionOutcome]:
        results: list[ExtractionOutcome] = []
        for index, article in enumerate(articles):
            outcome = await self.extract_with_diagnostics(
                article.get("title", ""),
                article.get("content", ""),
                article.get("url", ""),
            )
            results.append(outcome)
            if outcome.stop_batch:
                skipped_category = {
                    "quota_exhausted": "not_attempted_quota_exhausted",
                    "billing_or_quota_exhausted": "not_attempted_quota_exhausted",
                    "call_budget_exhausted": "not_attempted_call_budget_exhausted",
                    "transient_rate_limit": "not_attempted_after_rate_limit",
                    "provider_server_error": "not_attempted_after_provider_error",
                    "network_failure": "not_attempted_after_network_error",
                    "authentication_failure": "not_attempted_after_authentication_failure",
                    "permission_failure": "not_attempted_after_permission_failure",
                    "provider_error": "not_attempted_after_provider_error",
                    "provider_disabled": "not_attempted_provider_disabled",
                }.get(outcome.error_category, "not_attempted_after_extraction_failure")
                results.extend(
                    ExtractionOutcome(error_category=skipped_category, stop_batch=True)
                    for _ in articles[index + 1 :]
                )
                break
        return results

    async def extract_batch(self, articles: List[Dict[str, Any]]) -> List[Optional[ExtractionResult]]:
        return [outcome.result for outcome in await self.extract_many(articles)]


def validate_extraction_result(result: ExtractionResult) -> bool:
    """Validate extraction result meets minimum requirements."""
    if not result.is_event:
        return False
    if not result.event_type or result.event_type not in [e.value for e in EventType]:
        return False
    if not result.title or len(result.title) < 5:
        return False
    if not result.country:
        return False
    if result.confidence_score < 30:
        return False
    return True


def claims_to_model_claims(
    claims: List[ExtractedClaim],
    event_id: str,
    source_id: str,
    source_text: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Convert extracted claims to model format."""
    model_claims = []
    normalized_source = " ".join((source_text or "").split()).casefold()
    for claim in claims:
        normalized_quote = " ".join(claim.source_quote.split()).casefold()
        if source_text is not None and (
            not normalized_quote or normalized_quote not in normalized_source
        ):
            continue

        try:
            claim_type = ClaimType(claim.type.lower())
        except ValueError:
            claim_type = ClaimType.DURATION
        
        model_claims.append({
            "event_id": event_id,
            "claim_type": claim_type,
            "subject": claim.subject,
            "predicate": claim.predicate,
            "value": claim.value,
            "unit": claim.unit,
            "min_value": claim.min_value,
            "max_value": claim.max_value,
            "source_id": source_id,
            "source_quote": claim.source_quote,
            "confidence": claim.confidence,
            "verification_status": VerificationStatus.REPORTED,
        })
    return model_claims

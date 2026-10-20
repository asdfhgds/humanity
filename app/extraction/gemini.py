from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.models.schemas import ClaimType, EventType, VerificationStatus
from app.utils import clean_text, truncate


class ExtractedClaim(BaseModel):
    type: str
    subject: str
    predicate: str
    value: float | None = None
    unit: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    source_quote: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)


class ExtractionResult(BaseModel):
    is_mock: bool = False
    is_event: bool
    event_type: str | None = None
    title: str | None = None
    country: str | None = None
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    date: str | None = None
    claims: list[ExtractedClaim] = Field(default_factory=list)
    importance_score: float = Field(ge=0.0, le=100.0, default=50.0)
    confidence_score: float = Field(ge=0.0, le=100.0, default=50.0)
    publish_recommendation: str = "HOLD"
    summary: str | None = None
    key_facts: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)


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
    result: ExtractionResult | None = None
    error_category: str | None = None
    attempt_errors: tuple[str, ...] = ()
    attempts: int = 0
    stop_batch: bool = False
    retry_after_seconds: float | None = None
    unavailable_categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ErrorPolicy:
    category: str
    retryable: bool = False
    stop_batch: bool = False
    retry_after_seconds: float | None = None


def configured_gemini_keys(settings=None) -> tuple[str, ...]:
    """Return a stable, deduplicated key pool with the legacy key appended."""
    settings = settings or get_settings()
    values = []
    pool = getattr(settings, "gemini_api_keys", "") or ""
    legacy = getattr(settings, "gemini_api_key", "") or ""
    values.extend(pool.splitlines())
    values.extend(legacy.splitlines())
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_rotation_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (
        parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    )


class GeminiKeyRotation:
    """Round-robin credentials with sanitized cooldown state persisted per data directory."""

    STATE_VERSION = 1

    def __init__(self, keys: tuple[str, ...], state_path: Path):
        self.slot_ids = tuple(
            f"slot_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}"
            for key in keys
        )
        self._slot_keys = dict(zip(self.slot_ids, keys))
        self.state_path = state_path
        self._slots: dict[str, dict[str, Any]] = {
            slot_id: {"cooldown_until": None, "last_error_category": None}
            for slot_id in self.slot_ids
        }
        self.last_used_slot_id: str | None = None
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(data, dict) or data.get("version") != self.STATE_VERSION:
            return

        stored_slots = data.get("slots")
        if isinstance(stored_slots, dict):
            for slot_id in self.slot_ids:
                entry = stored_slots.get(slot_id)
                if not isinstance(entry, dict):
                    continue
                category = entry.get("last_error_category")
                if isinstance(category, str) and re.fullmatch(
                    r"[a-z_]{1,64}", category
                ):
                    self._slots[slot_id]["last_error_category"] = category
                cooldown = _parse_rotation_time(entry.get("cooldown_until"))
                if cooldown and cooldown > _utc_now():
                    self._slots[slot_id]["cooldown_until"] = cooldown

        last_used = data.get("last_used_slot_id")
        if last_used in self.slot_ids:
            self.last_used_slot_id = last_used

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.STATE_VERSION,
            "last_used_slot_id": self.last_used_slot_id,
            "slots": {
                slot_id: {
                    "cooldown_until": (
                        state["cooldown_until"].isoformat()
                        if state["cooldown_until"]
                        else None
                    ),
                    "last_error_category": state["last_error_category"],
                }
                for slot_id, state in self._slots.items()
            },
        }
        temporary_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True), encoding="utf-8"
        )
        temporary_path.replace(self.state_path)

    def available_slots(
        self, *, now: datetime | None = None, exclude: set[str] | None = None
    ) -> list[str]:
        if not self.slot_ids:
            return []
        current_time = now or _utc_now()
        excluded = exclude or set()
        start = (
            self.slot_ids.index(self.last_used_slot_id) + 1
            if self.last_used_slot_id in self.slot_ids
            else 0
        )
        ordered = self.slot_ids[start:] + self.slot_ids[:start]
        return [
            slot_id
            for slot_id in ordered
            if slot_id not in excluded
            and (
                self._slots[slot_id]["cooldown_until"] is None
                or self._slots[slot_id]["cooldown_until"] <= current_time
            )
        ]

    def earliest_cooldown_seconds(self, *, now: datetime | None = None) -> float | None:
        current_time = now or _utc_now()
        active = [
            state["cooldown_until"]
            for state in self._slots.values()
            if state["cooldown_until"] and state["cooldown_until"] > current_time
        ]
        if not active:
            return None
        return max(0.0, (min(active) - current_time).total_seconds())

    def record_use(self, slot_id: str) -> None:
        self.last_used_slot_id = slot_id
        self._save()

    def mark_unavailable(
        self, slot_id: str, category: str, cooldown_seconds: float
    ) -> None:
        self._slots[slot_id] = {
            "cooldown_until": _utc_now()
            + timedelta(seconds=max(0.0, cooldown_seconds)),
            "last_error_category": category,
        }
        self._save()

    def mark_success(self, slot_id: str) -> None:
        self._slots[slot_id] = {"cooldown_until": None, "last_error_category": None}
        self._save()

    def mark_retry_ready(self, slot_id: str) -> None:
        self._slots[slot_id] = {"cooldown_until": None, "last_error_category": None}
        self._save()


def _provider_status(error: Exception) -> tuple[int | None, str]:
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


def _retry_after_seconds(error: Exception) -> float | None:
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

    model_not_available = any(
        term in message
        for term in ("model not found", "model is not found", "unsupported model")
    )
    if model_not_available and (code == 400 or code == 404):
        return _ErrorPolicy("unsupported_model", stop_batch=True)
    if status in {"INVALID_ARGUMENT", "BAD_REQUEST"} or code in {400, 422}:
        return _ErrorPolicy("invalid_request", stop_batch=True)
    if code == 404 or status in {"NOT_FOUND", "MODEL_NOT_FOUND"}:
        if any(term in message for term in ("model", "publisher")):
            return _ErrorPolicy("unsupported_model", stop_batch=True)
        return _ErrorPolicy("provider_configuration_error", stop_batch=True)
    if code == 401 or status in {"UNAUTHENTICATED", "AUTHENTICATION"}:
        return _ErrorPolicy("authentication_failure", stop_batch=True)
    if code == 403 or status in {"PERMISSION_DENIED", "PERMISSION"}:
        return _ErrorPolicy("permission_failure", stop_batch=True)
    if code == 402 or any(
        term in message
        for term in ("payment required", "billing disabled", "credits depleted")
    ):
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
        for term in (
            "daily quota",
            "quota exceeded",
            "quota_exceeded",
            "per day",
            "requests per day",
        )
    )
    if status == "RESOURCE_EXHAUSTED" and not rate_limited:
        daily_quota = True
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        if daily_quota or not rate_limited:
            return _ErrorPolicy("quota_exhausted", stop_batch=True)
        return _ErrorPolicy(
            "transient_rate_limit",
            retryable=True,
            retry_after_seconds=retry_after,
        )

    if (code is not None and code >= 500) or status in {
        "UNAVAILABLE",
        "DEADLINE_EXCEEDED",
        "INTERNAL",
        "ABORTED",
    }:
        return _ErrorPolicy(
            "provider_server_error",
            retryable=True,
            retry_after_seconds=retry_after,
        )
    if isinstance(
        error, (TimeoutError, ConnectionError, OSError, httpx.TransportError)
    ) or any(
        term in name
        for term in ("timeout", "connecterror", "networkerror", "transporterror")
    ):
        return _ErrorPolicy("network_failure", retryable=True)
    if isinstance(error, (TypeError, ValueError)):
        return _ErrorPolicy("client_configuration_error", stop_batch=True)
    return _ErrorPolicy("provider_error", stop_batch=True)


class GeminiExtractor:
    MAX_RETRIES_PER_ARTICLE = 2
    MAX_PROVIDER_RETRY_WAIT_SECONDS = 60
    RETRY_BASE_SECONDS = 1
    QUOTA_COOLDOWN_SECONDS = 24 * 60 * 60
    RATE_LIMIT_COOLDOWN_SECONDS = 15 * 60
    CREDENTIAL_FAILURE_COOLDOWN_SECONDS = 24 * 60 * 60
    TEMPORARY_FAILURE_COOLDOWN_SECONDS = 5 * 60

    def __init__(
        self,
        client: Any | None = None,
        sleep=asyncio.sleep,
        client_factory=None,
    ):
        settings = get_settings()
        self.api_keys = configured_gemini_keys(settings)
        self.model_name = settings.gemini_model
        self.enabled = bool(self.api_keys)
        self.max_calls = max(0, settings.max_llm_calls_per_run)
        self.api_calls = 0
        self.rotation = GeminiKeyRotation(
            self.api_keys, settings.data_dir / ".gemini-key-rotation.json"
        )
        self._client_factory = client_factory or (
            lambda api_key: genai.Client(api_key=api_key)
        )
        self._clients: dict[str, Any] = {}
        self._outcome_attempts = 0
        self._slot_attempts = {slot_id: 0 for slot_id in self.rotation.slot_ids}
        self._slot_successes = {slot_id: 0 for slot_id in self.rotation.slot_ids}
        self._slot_failures: dict[str, dict[str, int]] = {
            slot_id: {} for slot_id in self.rotation.slot_ids
        }
        self._failover_attempts = 0
        self._failover_recoveries = 0
        if client is not None and self.rotation.slot_ids:
            self._clients[self.rotation.slot_ids[0]] = client
        self._sleep = sleep

    def rotation_diagnostics(self) -> dict[str, Any]:
        now = _utc_now()
        slots = []
        for slot_id in self.rotation.slot_ids:
            state = self.rotation._slots[slot_id]
            cooldown_until = state["cooldown_until"]
            slots.append(
                {
                    "slot_id": slot_id,
                    "attempts": self._slot_attempts[slot_id],
                    "successful_responses": self._slot_successes[slot_id],
                    "failure_categories": dict(
                        sorted(self._slot_failures[slot_id].items())
                    ),
                    "cooldown_until": (
                        cooldown_until.isoformat()
                        if cooldown_until and cooldown_until > now
                        else None
                    ),
                    "last_error_category": state["last_error_category"],
                }
            )
        return {
            "configured_slot_count": len(self.rotation.slot_ids),
            "api_calls": self.api_calls,
            "call_budget": self.max_calls,
            "failover_attempts": self._failover_attempts,
            "failover_recoveries": self._failover_recoveries,
            "slots": slots,
        }

    @property
    def api_calls_for_outcome(self) -> int:
        return self._outcome_attempts

    def _client_for(self, slot_id: str) -> Any:
        if slot_id not in self._clients:
            self._clients[slot_id] = self._client_factory(
                self.rotation._slot_keys[slot_id]
            )
        return self._clients[slot_id]

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
        if not self.enabled:
            return ExtractionOutcome(
                error_category="provider_disabled", stop_batch=True
            )

        prompt = self._build_prompt(title, content, url)
        attempt_errors: list[str] = []
        self._outcome_attempts = 0
        attempted_slots: set[str] = set()
        failed_slots: set[str] = set()
        retries_by_slot: dict[str, int] = {}
        while True:
            if self.api_calls >= self.max_calls:
                return ExtractionOutcome(
                    error_category="call_budget_exhausted",
                    attempt_errors=tuple(attempt_errors),
                    attempts=self.api_calls_for_outcome,
                    stop_batch=True,
                )

            available = self.rotation.available_slots(exclude=attempted_slots)
            if not available:
                cooldown = self.rotation.earliest_cooldown_seconds()
                unavailable = tuple(
                    dict.fromkeys(
                        self.rotation._slots[slot_id]["last_error_category"]
                        for slot_id in self.rotation.slot_ids
                        if self.rotation._slots[slot_id]["last_error_category"]
                    )
                )
                category = (
                    "all_credentials_unavailable"
                    if cooldown is not None
                    and (len(self.api_keys) > 1 or not attempt_errors)
                    else (
                        attempt_errors[-1]
                        if attempt_errors
                        else "all_credentials_unavailable"
                    )
                )
                return ExtractionOutcome(
                    error_category=category,
                    attempt_errors=tuple(attempt_errors),
                    attempts=self.api_calls_for_outcome,
                    stop_batch=True,
                    retry_after_seconds=cooldown,
                    unavailable_categories=unavailable or tuple(attempt_errors),
                )

            slot_id = available[0]
            if failed_slots and slot_id not in failed_slots:
                self._failover_attempts += 1
            attempted_slots.add(slot_id)
            self.rotation.record_use(slot_id)
            self.api_calls += 1
            self._outcome_attempts += 1
            self._slot_attempts[slot_id] += 1
            try:
                response = await self._client_for(slot_id).aio.models.generate_content(
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
                        attempt_errors=(*attempt_errors, "invalid_model_output"),
                        attempts=self.api_calls_for_outcome,
                    )
                if not response_text:
                    return ExtractionOutcome(
                        error_category="invalid_model_output",
                        attempt_errors=(*attempt_errors, "invalid_model_output"),
                        attempts=self.api_calls_for_outcome,
                    )
                try:
                    result = ExtractionResult.model_validate_json(response_text)
                except (ValidationError, ValueError, json.JSONDecodeError):
                    return ExtractionOutcome(
                        error_category="invalid_model_output",
                        attempt_errors=(*attempt_errors, "invalid_model_output"),
                        attempts=self.api_calls_for_outcome,
                    )
                self.rotation.mark_success(slot_id)
                self._slot_successes[slot_id] += 1
                if failed_slots and slot_id not in failed_slots:
                    self._failover_recoveries += 1
                if not result.is_event:
                    return ExtractionOutcome(
                        result=result,
                        error_category="non_event",
                        attempt_errors=tuple(attempt_errors),
                        attempts=self.api_calls_for_outcome,
                    )
                return ExtractionOutcome(
                    result=result,
                    attempt_errors=tuple(attempt_errors),
                    attempts=self.api_calls_for_outcome,
                )
            except Exception as error:
                policy = _classify_api_error(error)
                attempt_errors.append(policy.category)
                failed_slots.add(slot_id)
                failures = self._slot_failures[slot_id]
                failures[policy.category] = failures.get(policy.category, 0) + 1
                if policy.category in ("quota_exhausted", "billing_or_quota_exhausted"):
                    cooldown_seconds = self.QUOTA_COOLDOWN_SECONDS
                elif policy.category in (
                    "authentication_failure",
                    "permission_failure",
                ):
                    cooldown_seconds = self.CREDENTIAL_FAILURE_COOLDOWN_SECONDS
                elif policy.category == "transient_rate_limit":
                    cooldown_seconds = max(
                        self.RATE_LIMIT_COOLDOWN_SECONDS,
                        policy.retry_after_seconds or 0,
                    )
                elif policy.retryable:
                    cooldown_seconds = max(
                        self.TEMPORARY_FAILURE_COOLDOWN_SECONDS,
                        policy.retry_after_seconds or 0,
                    )
                else:
                    cooldown_seconds = 0
                if cooldown_seconds:
                    self.rotation.mark_unavailable(
                        slot_id, policy.category, cooldown_seconds
                    )

                if policy.retryable and len(self.api_keys) == 1:
                    retry_count = retries_by_slot.get(slot_id, 0)
                    delay = policy.retry_after_seconds
                    if delay is None:
                        delay = self.RETRY_BASE_SECONDS * (2**retry_count)
                    if (
                        retry_count < self.MAX_RETRIES_PER_ARTICLE
                        and delay <= self.MAX_PROVIDER_RETRY_WAIT_SECONDS
                        and self.api_calls < self.max_calls
                    ):
                        await self._sleep(delay)
                        self.rotation.mark_retry_ready(slot_id)
                        attempted_slots.discard(slot_id)
                        retries_by_slot[slot_id] = retry_count + 1
                        continue
                    if retry_count >= self.MAX_RETRIES_PER_ARTICLE:
                        return ExtractionOutcome(
                            error_category=policy.category,
                            attempt_errors=tuple(attempt_errors),
                            attempts=self.api_calls_for_outcome,
                            stop_batch=True,
                        )

                if not policy.retryable and policy.category not in (
                    "quota_exhausted",
                    "billing_or_quota_exhausted",
                    "authentication_failure",
                    "permission_failure",
                ):
                    return ExtractionOutcome(
                        error_category=policy.category,
                        attempt_errors=tuple(attempt_errors),
                        attempts=self.api_calls_for_outcome,
                        stop_batch=policy.stop_batch,
                    )
                if self.api_calls >= self.max_calls:
                    return ExtractionOutcome(
                        error_category="call_budget_exhausted",
                        attempt_errors=tuple(attempt_errors),
                        attempts=self.api_calls_for_outcome,
                        stop_batch=True,
                    )

        raise AssertionError("bounded extraction retry loop exited unexpectedly")

    async def extract(
        self, title: str, content: str, url: str
    ) -> ExtractionResult | None:
        """Compatibility wrapper; callers needing diagnostics should use detailed outcomes."""
        return (await self.extract_with_diagnostics(title, content, url)).result

    async def extract_many(
        self, articles: list[dict[str, Any]]
    ) -> list[ExtractionOutcome]:
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
                    "all_credentials_unavailable": "not_attempted_all_credentials_unavailable",
                }.get(outcome.error_category, "not_attempted_after_extraction_failure")
                results.extend(
                    ExtractionOutcome(
                        error_category=skipped_category,
                        stop_batch=True,
                        retry_after_seconds=outcome.retry_after_seconds,
                        unavailable_categories=outcome.unavailable_categories,
                    )
                    for _ in articles[index + 1 :]
                )
                break
        return results

    async def extract_batch(
        self, articles: list[dict[str, Any]]
    ) -> list[ExtractionResult | None]:
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
    claims: list[ExtractedClaim],
    event_id: str,
    source_id: str,
    source_text: str | None = None,
) -> list[dict[str, Any]]:
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

        model_claims.append(
            {
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
            }
        )
    return model_claims

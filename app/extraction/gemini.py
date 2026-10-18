from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ValidationError
import google.generativeai as genai
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


class GeminiExtractor:
    def __init__(self):
        settings = get_settings()
        self.api_key = settings.gemini_api_key
        self.model_name = settings.gemini_model
        self.enabled = bool(self.api_key)
        self.last_error_category: Optional[str] = None
        
        if self.enabled:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(self.model_name)
        else:
            self.model = None

    def _build_prompt(self, title: str, content: str, url: str) -> str:
        content_clean = clean_text(truncate(content, 8000))
        return f"""{EXTRACTION_PROMPT}

ARTICLE:
Title: {title}
URL: {url}
Content: {content_clean}

Respond with valid JSON only."""

    async def extract(self, title: str, content: str, url: str) -> Optional[ExtractionResult]:
        if not self.enabled or not self.model:
            self.last_error_category = "provider_disabled"
            return None

        self.last_error_category = None
        prompt = self._build_prompt(title, content, url)
        
        try:
            response = await self.model.generate_content_async(
                prompt,
                generation_config=genai.GenerationConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )
            
            if not response.text:
                self.last_error_category = "empty_response"
                return None
            
            data = json.loads(response.text)
            return ExtractionResult(**data)
            
        except json.JSONDecodeError as e:
            self.last_error_category = "invalid_json"
            print(f"Gemini extraction failed: {e}")
            return None
        except ValidationError as e:
            self.last_error_category = "invalid_schema"
            print(f"Gemini extraction failed: {e}")
            return None
        except Exception as e:
            self.last_error_category = type(e).__name__
            print(f"Gemini API error: {e}")
            return None

    async def extract_batch(self, articles: List[Dict[str, Any]]) -> List[Optional[ExtractionResult]]:
        # Hard budget: callers cannot silently exceed the configured quota.
        articles = articles[:get_settings().max_llm_calls_per_run]
        results = []
        for article in articles:
            result = await self.extract(
                article.get("title", ""),
                article.get("content", ""),
                article.get("url", "")
            )
            results.append(result)
        return results


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


def claims_to_model_claims(claims: List[ExtractedClaim], event_id: str, source_id: str) -> List[Dict[str, Any]]:
    """Convert extracted claims to model format."""
    model_claims = []
    for claim in claims:
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

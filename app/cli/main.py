from __future__ import annotations

import asyncio
import io
import tempfile
from collections import Counter
from datetime import datetime, timedelta
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

from app.discovery.manager import run_discovery
from app.discovery.gdelt import run_gdelt_discovery
from app.extraction.gemini import (
    ExtractionOutcome,
    ExtractionResult,
    GeminiExtractor,
    claims_to_model_claims,
    validate_extraction_result,
)
from app.clustering.cluster import EventClusterer
from app.clustering.identity_resolver import EventIdentityResolver
from app.scoring.score import EventScorer, decide_publish
from app.publishing.publisher import EventPublisher
from app.datastore import get_claim_store, get_event_store, get_source_store, get_ingestion_run_store
from app.config import get_settings
from app.models.schemas import Claim, Event, EventIdentity, EventType, ConfidenceLevel, EventStatus
from app.utils import parse_date_flexible

app = typer.Typer(help="HUMANITY - Autonomous Global Human Events Intelligence Platform")
console = Console()

_EXTRACTION_RETRY_DELAYS = {
    "quota_exhausted": timedelta(hours=24),
    "billing_or_quota_exhausted": timedelta(hours=24),
    "authentication_failure": timedelta(hours=24),
    "permission_failure": timedelta(hours=24),
    "transient_rate_limit": timedelta(minutes=15),
    "network_failure": timedelta(minutes=30),
    "provider_server_error": timedelta(minutes=30),
    "invalid_model_output": timedelta(hours=6),
    "provider_error": timedelta(hours=6),
}


def _source_needs_extraction(source, *, now=None) -> bool:
    if not source.content or getattr(source, "extraction_status", None) == "completed":
        return False
    retry_after = getattr(source, "extraction_retry_after", None)
    current_time = now or datetime.utcnow()
    return retry_after is None or retry_after <= current_time


def _sources_ready_for_extraction(sources, events, limit, *, now=None):
    processed_source_ids = {
        source_id
        for event in events
        for source_id in event.source_ids
    }
    return [
        source
        for source in sources
        if source.id not in processed_source_ids and _source_needs_extraction(source, now=now)
    ][:limit]


def _record_extraction_outcome(source, outcome: ExtractionOutcome, source_store) -> None:
    result = outcome.result
    category = outcome.error_category
    if result is not None and category == "non_event":
        updates = {
            "extraction_status": "completed",
            "extraction_error_category": None,
            "extraction_last_attempt_at": datetime.utcnow(),
            "extraction_retry_after": None,
        }
    elif result is not None and validate_extraction_result(result):
        updates = {
            "extraction_status": "completed",
            "extraction_error_category": None,
            "extraction_last_attempt_at": datetime.utcnow(),
            "extraction_retry_after": None,
        }
    else:
        if outcome.attempts == 0 and category in (
            None,
            "call_budget_exhausted",
            "not_attempted_call_budget_exhausted",
            "provider_disabled",
        ):
            return
        deferred_category = category.removeprefix("not_attempted_") if category else ""
        retry_category = deferred_category or category or "provider_error"
        retry_category = {
            "after_rate_limit": "transient_rate_limit",
            "after_network_error": "network_failure",
            "after_provider_error": "provider_error",
            "after_authentication_failure": "authentication_failure",
            "after_permission_failure": "permission_failure",
        }.get(retry_category, retry_category)
        retry_delay = _EXTRACTION_RETRY_DELAYS.get(
            retry_category, timedelta(hours=6)
        )
        updates = {
            "extraction_status": "deferred" if outcome.attempts == 0 else "retryable_failure",
            "extraction_error_category": category,
            "extraction_retry_after": datetime.utcnow() + retry_delay,
        }
        if outcome.attempts:
            updates["extraction_last_attempt_at"] = datetime.utcnow()

    if source_store.update(source.id, updates) is None:
        raise RuntimeError("Could not persist extraction progress for a source")


def _persist_extracted_event(source, result: ExtractionResult, event_store, claim_store):
    event_type = EventType(result.event_type)
    source_text = source.content or source.title or ""
    event = Event(
        event_type=event_type,
        title=(result.title or source.title or "Untitled event")[:200],
        slug="",
        description=source_text[:500],
        summary=(result.summary or source_text or source.title or "")[:500],
        country_code=source.country or "XX",
        country_name=source.country or result.country or "Unknown",
        city=result.city,
        latitude=result.latitude,
        longitude=result.longitude,
        start_time=(
            parse_date_flexible(result.date)
            if result.date
            else source.published_at or datetime.utcnow()
        ),
        severity=5,
        status=EventStatus.DEVELOPING,
        confidence=ConfidenceLevel.MEDIUM,
        source_ids=[source.id],
    )
    resolver = EventIdentityResolver(event_store=event_store)
    identity = resolver.resolve(event)
    existing = (
        event_store.get(identity.matched_event_id)
        if identity.decision == "SAME_EVENT" and identity.matched_event_id
        else None
    )
    is_new = existing is None
    if existing:
        source_was_already_attached = source.id in existing.source_ids
        if not source_was_already_attached:
            existing.source_ids.append(source.id)
        existing.canonical_id = identity.canonical_id
        source_ids = list(dict.fromkeys(existing.source_ids))
        existing.event_identity = EventIdentity(
            canonical_id=identity.canonical_id,
            candidate_ids=source_ids,
            match_confidence=identity.confidence,
            matched_with=[existing.id],
            decision="SAME_EVENT",
            decision_reasons=list(identity.reasons),
        )
        event_store.update(
            existing.id,
            {
                "source_ids": source_ids,
                "canonical_id": existing.canonical_id,
                "event_identity": existing.event_identity.model_dump(mode="json"),
            },
        )
        target = event_store.get(existing.id) or existing
    else:
        event.canonical_id = identity.canonical_id
        event.event_identity = EventIdentity(
            canonical_id=identity.canonical_id,
            candidate_ids=[source.id],
            match_confidence=identity.confidence,
            matched_with=[],
            decision=identity.decision,
            decision_reasons=list(identity.reasons),
        )
        target = event_store.create(event) or event
        source_was_already_attached = False

    claims = []
    if not source_was_already_attached:
        claims = [
            Claim(**claim_data)
            for claim_data in claims_to_model_claims(
                result.claims,
                target.id,
                source.id,
                source_text=source.content or "",
            )
        ]
        if claims:
            claim_store.bulk_create(claims)
    return target, is_new, claims


async def _extract_sources(sources, extractor):
    if hasattr(extractor, "extract_many"):
        articles = [
            {"title": source.title or "", "content": source.content or "", "url": str(source.url)}
            for source in sources
        ]
        outcomes = await extractor.extract_many(articles)
        return list(zip(sources, outcomes))

    results = []
    stopped = False
    for source in sources:
        if stopped:
            results.append(
                (source, ExtractionOutcome(error_category="not_attempted_after_extraction_failure"))
            )
            continue
        try:
            if hasattr(extractor, "extract_with_diagnostics"):
                outcome = await extractor.extract_with_diagnostics(
                    source.title or "", source.content or "", str(source.url)
                )
            else:
                result = await extractor.extract(
                    source.title or "", source.content or "", str(source.url)
                )
                outcome = ExtractionOutcome(
                    result=result,
                    error_category="no_result" if result is None else None,
                    attempts=1,
                )
        except Exception as error:
            outcome = ExtractionOutcome(
                error_category=f"unexpected_{type(error).__name__}", attempts=1, stop_batch=True
            )
        results.append((source, outcome))
        stopped = outcome.stop_batch
    return results


@app.command()
def discover(
    hours: int = typer.Option(6, help="Hours back to search"),
):
    """Run full discovery (GDELT + RSS feeds + article fetching)."""
    console.print(f"[bold blue]Running full discovery (last {hours}h)...[/bold blue]")
    
    console.print("Fetching from all sources...")
    result = asyncio.run(run_discovery(hours))
    
    console.print(f"[green]Discovery complete:[/green]")
    console.print(f"  GDELT events: {result['gdelt_events']}")
    console.print(f"  RSS articles fetched: {result['rss_articles_fetched']}")
    console.print(f"  Sources created: {result['sources_created']}")
    console.print(f"  Run ID: {result['run_id']}")


@app.command()
def extract(
    limit: int = typer.Option(50, help="Max sources to process"),
    min_priority: int = typer.Option(1, help="Minimum source priority"),
):
    """Extract events from collected sources using Gemini."""
    console.print("[bold blue]Running event extraction...[/bold blue]")
    
    source_store = get_source_store()
    event_store = get_event_store()
    extractor = GeminiExtractor()
    
    if not extractor.enabled:
        console.print("[red]Gemini API key not configured. Set GEMINI_API_KEY in .env[/red]")
        return
    
    # Get sources with content (from RSS fetching)
    sources = source_store.get_all()
    sources = [s for s in sources if s.priority >= min_priority and s.content][:limit]
    
    if not sources:
        console.print("[yellow]No sources with content found. Run discovery first.[/yellow]")
        return
    
    console.print(f"Processing {len(sources)} sources with content...")
    
    outcomes = asyncio.run(_extract_sources(sources, extractor))
    claim_store = get_claim_store()
    created = 0
    attached = 0
    calls = 0
    attempt_errors = Counter()
    for i, (source, outcome) in enumerate(outcomes):
        console.print(f"  [{i+1}/{len(sources)}] Extracting from {source.publisher}...")
        calls += outcome.attempts
        attempt_errors.update(outcome.attempt_errors)
        result = outcome.result
        if result and validate_extraction_result(result):
            _, is_new, _ = _persist_extracted_event(source, result, event_store, claim_store)
            created += int(is_new)
            attached += int(not is_new)
            _record_extraction_outcome(source, outcome, source_store)
        else:
            console.print(
                f"    [yellow]Extraction not persisted: "
                f"{outcome.error_category or 'invalid_model_output'}[/yellow]"
            )
            _record_extraction_outcome(source, outcome, source_store)

    console.print(f"[green]Created {created} events; attached {attached} additional reports[/green]")
    console.print(f"  Gemini API attempts: {calls}")
    if attempt_errors:
        console.print(f"  Attempt errors: {dict(sorted(attempt_errors.items()))}")


@app.command()
def cluster():
    """Report event count; identity is resolved conservatively during extraction."""
    event_store = get_event_store()
    events = event_store.get_all()
    console.print(
        f"[green]No destructive post-hoc merges performed; "
        f"{len(events)} events retain their source and claim provenance.[/green]"
    )


@app.command()
def score():
    """Score all unpublished events."""
    console.print("[bold blue]Scoring events...[/bold blue]")
    
    event_store = get_event_store()
    publisher = EventPublisher()
    
    events = event_store.get_all()
    unpublished = [e for e in events if not e.published_at]
    
    console.print(f"Scoring {len(unpublished)} unpublished events...")
    
    published = 0
    for i, event in enumerate(unpublished):
        console.print(f"  [{i+1}/{len(unpublished)}] Scoring {event.title[:50]}...")
        result = publisher.publish_event(event)
        if result.get("published"):
            published += 1
    
    console.print(f"[green]Published {published} events[/green]")


@app.command()
def publish():
    """Run full publish pipeline: score and publish."""
    console.print("[bold blue]Running publish pipeline...[/bold blue]")
    
    event_store = get_event_store()
    publisher = EventPublisher()
    
    events = event_store.get_all()
    unpublished = [e for e in events if not e.published_at]
    
    console.print(f"Processing {len(unpublished)} unpublished events...")
    
    published = 0
    for i, event in enumerate(unpublished):
        console.print(f"  [{i+1}/{len(unpublished)}] Publishing {event.title[:50]}...")
        result = publisher.publish_event(event)
        if result.get("published"):
            published += 1
    
    # Generate daily brief
    publisher.generate_daily_brief()
    console.print(f"[green]Published {published} events, generated daily brief[/green]")


@app.command()
def run(
    hours: int = typer.Option(6, help="Hours back for discovery"),
    extract_limit: int = typer.Option(50, help="Max sources to extract"),
):
    """Run complete autonomous pipeline: discovery -> extraction -> clustering -> scoring -> publishing."""
    console.print("[bold green]HUMANITY - Autonomous Pipeline Starting[/bold green]")
    console.print("=" * 50)
    
    # 1. Discovery (GDELT + RSS + article fetching)
    console.print("\n[bold]1. DISCOVERY[/bold]")
    discovery_result = asyncio.run(run_discovery(hours))
    console.print(f"  GDELT events: {discovery_result['gdelt_events']}")
    console.print(f"  RSS articles with content: {discovery_result['rss_articles_fetched']}")
    console.print(f"  Sources created: {discovery_result['sources_created']}")
    
    # 2. Extraction (Gemini structured extraction from article content)
    console.print("\n[bold]2. EXTRACTION[/bold]")
    extractor = GeminiExtractor()
    if not extractor.enabled:
        console.print("  [yellow]Gemini not configured, skipping extraction[/yellow]")
        return
    
    source_store = get_source_store()
    event_store = get_event_store()
    sources = _sources_ready_for_extraction(
        source_store.get_all(), event_store.get_all(), extract_limit
    )

    if not sources:
        console.print("  [yellow]No unprocessed sources with content found[/yellow]")
    else:
        console.print(f"  Extracting from {len(sources)} sources with content...")

    extraction_pairs = asyncio.run(_extract_sources(sources, extractor))
    claim_store = get_claim_store()
    created = 0
    attached = 0
    api_attempts = 0
    attempt_errors = Counter()
    for i, (source, outcome) in enumerate(extraction_pairs):
        console.print(f"  [{i+1}/{len(sources)}] {source.publisher}...")
        api_attempts += outcome.attempts
        attempt_errors.update(outcome.attempt_errors)
        extraction_result = outcome.result
        if extraction_result and validate_extraction_result(extraction_result):
            _, is_new, _ = _persist_extracted_event(
                source, extraction_result, event_store, claim_store
            )
            created += int(is_new)
            attached += int(not is_new)
            _record_extraction_outcome(source, outcome, source_store)
        else:
            console.print(
                f"    [yellow]Extraction not persisted: "
                f"{outcome.error_category or 'invalid_model_output'}[/yellow]"
            )
            _record_extraction_outcome(source, outcome, source_store)
    
    console.print(f"  [green]Created {created} events; attached {attached} additional reports[/green]")
    console.print(f"  Gemini API attempts: {api_attempts}")
    if attempt_errors:
        console.print(f"  Attempt errors: {dict(sorted(attempt_errors.items()))}")
    
    # 3. Clustering
    console.print("\n[bold]3. CLUSTERING[/bold]")
    console.print("  Extraction-time identity resolution attached matching reports without heuristic post-merge.")
    
    # 4. Scoring & Publishing
    console.print("\n[bold]4. SCORING & PUBLISHING[/bold]")
    publisher = EventPublisher()
    published = 0
    if get_settings().enable_autopublish:
        events = event_store.get_all()
        unpublished = [e for e in events if not e.published_at]
        for event in unpublished:
            result = publisher.publish_event(event)
            if result.get("published"):
                published += 1
    else:
        console.print("  [yellow]Auto-publication disabled; eligible events remain unpublished[/yellow]")
    
    # 5. Daily Brief
    console.print("\n[bold]5. DAILY BRIEF[/bold]")
    publisher.generate_daily_brief()
    
    console.print(f"\n[bold green]Pipeline complete![/bold green]")
    console.print(f"  Events published: {published}")
    console.print(f"  Run ID: {discovery_result['run_id']}")


@app.command("smoke-test")
def smoke_test(
    hours: int = typer.Option(6, min=1, max=72, help="Hours back for source discovery"),
    extract_limit: int = typer.Option(25, min=1, max=50, help="Maximum articles to send to Gemini"),
):
    """Verify live source discovery and Gemini extraction without publishing."""
    settings = get_settings()
    if not settings.gemini_api_key:
        console.print("[red]Smoke test requires GEMINI_API_KEY; no source requests were made.[/red]")
        raise typer.Exit(code=1)

    original_settings = {
        "data_dir": settings.data_dir,
        "web_dir": settings.web_dir,
        "enable_autopublish": settings.enable_autopublish,
        "production_mode": settings.production_mode,
    }
    counts = {
        "discovered_articles": 0,
        "extraction_attempts": 0,
        "successful_extractions": 0,
        "failed_extractions": 0,
        "invalid_extractions": 0,
        "validated_events": 0,
        "publication_eligible_candidates": 0,
        "invalid_or_non_event_results": 0,
        "insufficient_independent_sources": 0,
        "suspected_syndication": 0,
        "evidence_assessment_failures": 0,
        "evidence_contradictions": 0,
        "publication_score_holds": 0,
        "confidence_holds": 0,
        "eligible_unpublished_candidates": 0,
        "claims_created": 0,
        "claims_with_source_attribution": 0,
        "extraction_error_categories": {},
        "publication_reason_counts": {},
    }
    discovery_counts = {
        "gdelt_events": 0,
        "gdelt_articles": 0,
        "gdelt_diagnostics": {},
        "rss_feeds_attempted": 0,
        "rss_feeds_completed": 0,
        "rss_feeds_with_errors": 0,
        "rss_fetch_attempts": 0,
        "rss_fetch_successes": 0,
        "rss_fetch_failures": 0,
        "rss_discovery_errors": 0,
    }
    error_type: Optional[str] = None

    try:
        with tempfile.TemporaryDirectory(prefix="humanity-smoke-") as temp_dir:
            temp_root = Path(temp_dir)
            settings.data_dir = temp_root / "data"
            settings.web_dir = temp_root / "web"
            settings.enable_autopublish = False
            settings.production_mode = True

            try:
                # Source connectors and provider errors can contain URLs or
                # response fragments; only aggregate counters leave this scope.
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    discovery_result = asyncio.run(run_discovery(hours))
                    for key in discovery_counts:
                        discovery_counts[key] = discovery_result.get(
                            key, discovery_counts[key]
                        )
                    source_store = get_source_store()
                    event_store = get_event_store()
                    discovered_sources = [
                        source for source in source_store.get_all() if source.content
                    ]
                    counts["discovered_articles"] = len(discovered_sources)
                    sources = discovered_sources[:min(extract_limit, settings.max_llm_calls_per_run)]
                    extractor = GeminiExtractor()
                    if not extractor.enabled:
                        raise RuntimeError("Gemini extraction provider is not enabled")

                    from app.scoring.score import EventScorer, decide_publish
                    from app.verification.evidence import EvidenceAssessor
                    from app.verification.syndication import SyndicationDetector
                    from app.models.schemas import SourceIndependence
                    from app.config import load_scoring

                    claim_store = get_claim_store()
                    scorer = EventScorer(event_store=event_store, source_store=source_store)
                    extraction_results = asyncio.run(_extract_sources(sources, extractor))
                    for source, outcome in extraction_results:
                        counts["extraction_attempts"] += outcome.attempts
                        extraction_result = outcome.result
                        if outcome.error_category and not extraction_result:
                            counts["failed_extractions"] += 1
                            categories = counts["extraction_error_categories"]
                            categories[outcome.error_category] = (
                                categories.get(outcome.error_category, 0) + 1
                            )
                            if outcome.error_category == "invalid_model_output":
                                counts["invalid_extractions"] += 1
                                counts["invalid_or_non_event_results"] += 1
                            continue
                        if extraction_result is None:
                            counts["failed_extractions"] += 1
                            category = outcome.error_category or "no_result"
                            categories = counts["extraction_error_categories"]
                            categories[category] = categories.get(category, 0) + 1
                            continue
                        counts["successful_extractions"] += 1
                        if (
                            outcome.error_category == "non_event"
                            or extraction_result.is_mock
                            or not validate_extraction_result(extraction_result)
                        ):
                            counts["invalid_extractions"] += 1
                            counts["invalid_or_non_event_results"] += 1
                            category = (
                                "non_event"
                                if outcome.error_category == "non_event"
                                else "invalid_model_output"
                            )
                            categories = counts["extraction_error_categories"]
                            categories[category] = categories.get(category, 0) + 1
                            continue

                        counts["validated_events"] += 1
                        event, _, claims = _persist_extracted_event(
                            source, extraction_result, event_store, claim_store
                        )
                        counts["claims_created"] += len(claims)
                        counts["claims_with_source_attribution"] += sum(
                            claim.source_id == source.id
                            and bool(claim.source_quote.strip())
                            and claim.source_quote.strip() in (source.content or "")
                            for claim in claims
                        )

                    detector = SyndicationDetector()
                    for event in event_store.get_all():
                        event_sources = source_store.get_by_event(event.id)
                        relationships = detector.relationships(event_sources)
                        for source in event_sources:
                            related = [
                                relationship
                                for relationship in relationships
                                if source.id in (
                                    relationship.source_id_a,
                                    relationship.source_id_b,
                                )
                            ]
                            classifications = [relationship.relationship for relationship in related]
                            if any(
                                classification in (
                                    SourceIndependence.LIKELY_COPIED,
                                    SourceIndependence.LIKELY_SYNDICATED,
                                )
                                for classification in classifications
                            ):
                                classification = SourceIndependence.LIKELY_SYNDICATED
                            elif related and all(
                                classification == SourceIndependence.INDEPENDENT
                                for classification in classifications
                            ):
                                classification = SourceIndependence.INDEPENDENT
                            else:
                                classification = source.independence_classification
                            if classification != source.independence_classification:
                                source_store.update(
                                    source.id,
                                    {"independence_classification": classification.value},
                                )
                                source.independence_classification = classification

                        claims = claim_store.get_by_event(event.id)
                        evidence = EvidenceAssessor().assess(event, event_sources, claims)
                        reasons = counts["publication_reason_counts"]
                        if not evidence.publication_ready:
                            counts["evidence_assessment_failures"] += 1
                        if "insufficient_independent_sources" in evidence.reasons:
                            counts["insufficient_independent_sources"] += 1
                        if evidence.syndicated_source_count:
                            counts["suspected_syndication"] += 1
                        if evidence.contradiction_count:
                            counts["evidence_contradictions"] += 1
                        for reason in evidence.reasons:
                            reasons[reason] = reasons.get(reason, 0) + 1
                        if evidence.publication_ready:
                            scores = scorer.score_event(event, event_sources)
                            decision = decide_publish(event, scores, source_store)
                            if decision != "HOLD":
                                counts["publication_eligible_candidates"] += 1
                                counts["eligible_unpublished_candidates"] += 1
                            else:
                                counts["publication_score_holds"] += int(
                                    scores.get("final", 0)
                                    < load_scoring().thresholds["auto_publish_warning"]
                                )
                                counts["confidence_holds"] += int(
                                    event.confidence.value not in {"HIGH", "VERIFIED", "MEDIUM"}
                                )
            except Exception as error:
                error_type = type(error).__name__
    finally:
        for name, value in original_settings.items():
            setattr(settings, name, value)

    console.print("[bold]HUMANITY live-source smoke test (isolated; publishing disabled)[/bold]")
    gdelt_diagnostics = discovery_counts["gdelt_diagnostics"]
    for stage, result_key, count_key in (
        ("GDELT events", "events", "gdelt_events"),
        ("GDELT GKG", "gkg", "gdelt_articles"),
    ):
        stage_diagnostics = gdelt_diagnostics.get(stage.split()[-1].lower(), {})
        error_categories = ",".join(
            f"{category}:{count}"
            for category, count in sorted(stage_diagnostics.get("error_categories", {}).items())
        ) or "none"
        console.print(
            f"  {stage}: {discovery_counts[count_key]} found; "
            f"status={stage_diagnostics.get('status', 'not_reported')}; "
            f"errors={stage_diagnostics.get('errors', 'not_reported')}; "
            f"error categories={error_categories}"
        )
    console.print(
        f"  RSS feeds: {discovery_counts['rss_feeds_completed']}/"
        f"{discovery_counts['rss_feeds_attempted']} completed; "
        f"{discovery_counts['rss_feeds_with_errors']} with errors; "
        f"discovery errors={discovery_counts['rss_discovery_errors']}"
    )
    console.print(
        f"  RSS fetch results: {discovery_counts['rss_fetch_successes']} succeeded, "
        f"{discovery_counts['rss_fetch_failures']} failed "
        f"of {discovery_counts['rss_fetch_attempts']} attempts"
    )
    for label, key in (
        ("Discovered articles", "discovered_articles"),
        ("Extraction attempts", "extraction_attempts"),
        ("Successful extractions", "successful_extractions"),
        ("Failed extractions", "failed_extractions"),
        ("Invalid extraction results", "invalid_extractions"),
        ("Validated events", "validated_events"),
        ("Publication-eligible candidates", "publication_eligible_candidates"),
        ("Invalid or non-event results", "invalid_or_non_event_results"),
        ("Insufficient independent sources", "insufficient_independent_sources"),
        ("Events with suspected syndication", "suspected_syndication"),
        ("Evidence-assessment failures", "evidence_assessment_failures"),
        ("Evidence contradictions", "evidence_contradictions"),
        ("Publication-score holds", "publication_score_holds"),
        ("Confidence holds", "confidence_holds"),
        ("Eligible candidates retained as unpublished", "eligible_unpublished_candidates"),
        ("Claims created", "claims_created"),
        ("Claims with source attribution", "claims_with_source_attribution"),
    ):
        console.print(f"  {label}: {counts[key]}")
    extraction_errors = ",".join(
        f"{category}:{count}"
        for category, count in sorted(counts["extraction_error_categories"].items())
    ) or "none"
    console.print(f"  Extraction error categories: {extraction_errors}")
    publication_reasons = ",".join(
        f"{reason}:{count}"
        for reason, count in sorted(counts["publication_reason_counts"].items())
    ) or "none"
    console.print(f"  Evidence assessment reasons: {publication_reasons}")
    if error_type:
        console.print(f"[red]Smoke test stopped safely ({error_type}); no publication was attempted.[/red]")
        raise typer.Exit(code=1)
    console.print("  Auto-publication: disabled")
    console.print("  Storage: temporary directory (removed at completion)")


@app.command()
def stats():
    """Show database statistics."""
    event_store = get_event_store()
    source_store = get_source_store()
    run_store = get_ingestion_run_store()
    
    events = event_store.get_all()
    sources = source_store.get_all()
    runs = run_store.get_latest(5)
    
    table = Table(title="HUMANITY Database Stats")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    
    table.add_row("Total Events", str(len(events)))
    table.add_row("Published Events", str(len([e for e in events if e.visibility])))
    table.add_row("Total Sources", str(len(sources)))
    table.add_row("Ingestion Runs", str(len(runs)))
    
    # By type
    type_counts = {}
    for e in events:
        type_counts[e.event_type.value] = type_counts.get(e.event_type.value, 0) + 1
    
    for etype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        table.add_row(f"  {etype}", str(count))
    
    console.print(table)
    
    # Recent runs
    if runs:
        run_table = Table(title="Recent Ingestion Runs")
        run_table.add_column("Run ID", style="cyan")
        run_table.add_column("Started", style="yellow")
        run_table.add_column("Status", style="green")
        run_table.add_column("Articles", style="blue")
        run_table.add_column("Events", style="blue")
        run_table.add_column("Published", style="blue")
        
        for run in runs:
            run_table.add_row(
                run.id[:8],
                run.started_at.strftime("%Y-%m-%d %H:%M"),
                run.status,
                str(run.articles_seen),
                str(run.events_created),
                str(run.events_published),
            )
        console.print(run_table)


@app.command()
def health():
    """Run source health checks."""
    console.print("[bold blue]Running health checks...[/bold blue]")
    
    source_store = get_source_store()
    sources = source_store.get_all()
    
    console.print(f"Checking {len(sources)} sources...")
    
    # In a real implementation, this would check each source's availability
    for source in sources[:5]:  # Limit for demo
        console.print(f"  {source.publisher}: OK (mock)")
    
    console.print("[green]Health check complete[/green]")


@app.command(name="legacy-run", hidden=True)
def legacy_run(
    hours: int = typer.Option(6, help="Hours back for discovery"),
    max_events: int = typer.Option(1000, help="Max events to discover"),
    extract_limit: int = typer.Option(50, help="Max sources to extract"),
    daily_brief_only: bool = typer.Option(False, help="Only generate daily brief"),
    cleanup_only: bool = typer.Option(False, help="Only run cleanup"),
):
    """Run complete autonomous pipeline."""
    if daily_brief_only:
        console.print("[bold blue]Generating daily brief only...[/bold blue]")
        publisher = EventPublisher()
        publisher.generate_daily_brief()
        console.print("[green]Daily brief generated[/green]")
        return
    
    if cleanup_only:
        console.print("[bold blue]Running cleanup...[/bold blue]")
        # Cleanup old data, etc.
        console.print("[green]Cleanup complete[/green]")
        return
    
    console.print("[bold green]HUMANITY - Autonomous Pipeline Starting[/bold green]")
    console.print("=" * 50)
    
    # 1. Discovery
    console.print("\n[bold]1. DISCOVERY[/bold]")
    result = asyncio.run(run_gdelt_discovery(hours, max_events, max_events // 2))
    console.print(f"  Found {result['events_found']} events, {result['articles_found']} articles")
    
    # 2. Extraction
    console.print("\n[bold]2. EXTRACTION[/bold]")
    extractor = GeminiExtractor()
    if extractor.enabled:
        # In production, fetch article content and extract
        console.print("  Gemini extraction enabled")
    else:
        console.print("  [yellow]Gemini not configured, using mock extraction[/yellow]")
    
    source_store = get_source_store()
    event_store = get_event_store()
    sources = source_store.get_all()[:extract_limit]
    
    for source in sources:
        event = _create_mock_event_from_source(source)
        if event:
            event_store.create(event)
    
    console.print(f"  Created events from {len(sources)} sources")
    
    # 3. Clustering
    console.print("\n[bold]3. CLUSTERING[/bold]")
    clusterer = EventClusterer()
    # Clustering happens during publish
    
    # 4. Scoring & Publishing
    console.print("\n[bold]4. SCORING & PUBLISHING[/bold]")
    publisher = EventPublisher()
    published = 0
    if get_settings().enable_autopublish:
        events = event_store.get_all()
        unpublished = [e for e in events if not e.published_at]
        for event in unpublished:
            result = publisher.publish_event(event)
            if result.get("published"):
                published += 1
    else:
        console.print("  [yellow]Auto-publication disabled; eligible events remain unpublished[/yellow]")
    
    # 5. Daily Brief
    console.print("\n[bold]5. DAILY BRIEF[/bold]")
    publisher.generate_daily_brief()
    
    console.print(f"\n[bold green]✓ Pipeline complete![/bold green]")
    console.print(f"  Events published: {published}")
    console.print(f"  Run ID: {result['run_id']}")


@app.command()
def web_build():
    """Build the Astro website."""
    import subprocess
    console.print("[bold blue]Building Astro website...[/bold blue]")
    result = subprocess.run(["npm", "run", "build"], cwd="web", capture_output=True, text=True)
    if result.returncode == 0:
        console.print("[green]Build successful![/green]")
    else:
        console.print(f"[red]Build failed:[/red]\n{result.stderr}")


@app.command()
def web_dev():
    """Start Astro dev server."""
    import subprocess
    console.print("[bold blue]Starting Astro dev server...[/bold blue]")
    subprocess.run(["npm", "run", "dev"], cwd="web")


if __name__ == "__main__":
    app()

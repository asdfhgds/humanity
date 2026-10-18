from __future__ import annotations

import asyncio
import io
import sys
import tempfile
from datetime import datetime
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

from app.discovery.manager import run_discovery
from app.discovery.gdelt import run_gdelt_discovery
from app.extraction.gemini import GeminiExtractor, validate_extraction_result
from app.clustering.cluster import EventClusterer
from app.scoring.score import EventScorer, decide_publish
from app.publishing.publisher import EventPublisher
from app.datastore import get_event_store, get_source_store, get_ingestion_run_store
from app.config import get_settings
from app.models.schemas import Claim, Event, EventType, ConfidenceLevel, EventStatus
from app.utils import canonicalize_url, compute_article_hash

app = typer.Typer(help="HUMANITY - Autonomous Global Human Events Intelligence Platform")
console = Console()


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
    
    created = 0
    for i, source in enumerate(sources):
        console.print(f"  [{i+1}/{len(sources)}] Extracting from {source.publisher}...")
        
        # Extract using Gemini with article content
        result = asyncio.run(extractor.extract(source.title or "", source.content or "", str(source.url)))
        
        if result and validate_extraction_result(result):
            # Create event from extraction result
            from app.models.schemas import Event, EventType, EventStatus, ConfidenceLevel
            import uuid
            
            # Map event type string to enum
            try:
                event_type = EventType(result.event_type)
            except ValueError:
                event_type = EventType.PROTEST
            
            # Parse date
            from app.utils import parse_date_flexible
            start_time = parse_date_flexible(result.date) if result.date else source.published_at or datetime.utcnow()
            
            # Determine country
            country_code = source.country or "XX"
            country_name = source.country or "Unknown"
            
            event = Event(
                id=str(uuid.uuid4()),
                canonical_id=compute_article_hash(source.title or "", str(source.url)),
                event_type=event_type,
                title=result.title[:200] if result.title else source.title[:200],
                slug="",
                description=source.content[:500] if source.content else source.title,
                summary=result.summary[:500] if result.summary else (source.content[:500] if source.content else source.title[:300]),
                country_code=country_code,
                country_name=country_name,
                city=result.city,
                start_time=start_time,
                severity=5,
                status=EventStatus.DEVELOPING,
                confidence=ConfidenceLevel.MEDIUM,
                source_ids=[source.id],
            )
            
            event_store.create(event)
            
            # Create claims from extraction
            from app.extraction.gemini import claims_to_model_claims
            from app.datastore import get_claim_store
            claim_store = get_claim_store()
            model_claims = claims_to_model_claims(result.claims, event.id, source.id)
            claim_store.bulk_create([type('obj', (object,), c) for c in model_claims])
            
            created += 1
        else:
            console.print(f"    [yellow]Extraction failed or validation failed[/yellow]")
    
    console.print(f"[green]Created {created} events from {len(sources)} sources[/green]")


@app.command()
def cluster():
    """Cluster events to remove duplicates."""
    console.print("[bold blue]Running event clustering...[/bold blue]")
    
    event_store = get_event_store()
    source_store = get_source_store()
    clusterer = EventClusterer()
    
    events = event_store.get_all()
    console.print(f"Clustering {len(events)} events...")
    
    # For each event, find similar
    merged = 0
    for event in events:
        sources = source_store.get_by_event(event.id)
        if not sources:
            continue
        
        # Use first source as candidate
        source = sources[0]
        candidate = {
            "source_url": str(source.url),
            "title": source.title,
            "latitude": event.latitude,
            "longitude": event.longitude,
            "date": event.start_time,
        }
        
        similar = clusterer.find_similar_events(candidate)
        for similar_event, score in similar:
            if similar_event.id != event.id and score > 0.8:
                # Merge: add source to existing event
                if source.id not in similar_event.source_ids:
                    similar_event.source_ids.append(source.id)
                    event_store.update(similar_event.id, {"source_ids": similar_event.source_ids})
                    # Delete duplicate
                    event_store.delete(event.id)
                    merged += 1
                break
    
    console.print(f"[green]Merged {merged} duplicate events[/green]")


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
    # Get sources with content
    sources = [s for s in source_store.get_all() if s.content][:extract_limit]
    
    if not sources:
        console.print("  [yellow]No sources with content found[/yellow]")
        return
    
    console.print(f"  Extracting from {len(sources)} sources with content...")
    
    created = 0
    for i, source in enumerate(sources):
        console.print(f"  [{i+1}/{len(sources)}] {source.publisher}...")
        
        extraction_result = asyncio.run(extractor.extract(source.title or "", source.content or "", str(source.url)))
        
        if extraction_result and validate_extraction_result(extraction_result):
            from app.models.schemas import Event, EventType, EventStatus, ConfidenceLevel
            import uuid
            
            try:
                event_type = EventType(extraction_result.event_type)
            except ValueError:
                event_type = EventType.PROTEST
            
            from app.utils import parse_date_flexible
            start_time = parse_date_flexible(extraction_result.date) if extraction_result.date else source.published_at or datetime.utcnow()
            
            country_code = source.country or "XX"
            country_name = source.country or "Unknown"
            
            event = Event(
                id=str(uuid.uuid4()),
                canonical_id=compute_article_hash(source.title or "", str(source.url)),
                event_type=event_type,
                title=extraction_result.title[:200] if extraction_result.title else source.title[:200],
                slug="",
                description=source.content[:500] if source.content else source.title,
                summary=extraction_result.summary[:500] if extraction_result.summary else (source.content[:500] if source.content else source.title[:300]),
                country_code=country_code,
                country_name=country_name,
                city=extraction_result.city,
                start_time=start_time,
                severity=5,
                status=EventStatus.DEVELOPING,
                confidence=ConfidenceLevel.MEDIUM,
                source_ids=[source.id],
            )
            
            event_store.create(event)
            
            # Create claims
            from app.extraction.gemini import claims_to_model_claims
            from app.datastore import get_claim_store
            claim_store = get_claim_store()
            model_claims = claims_to_model_claims(extraction_result.claims, event.id, source.id)
            claim_store.bulk_create([type('obj', (object,), c) for c in model_claims])
            
            created += 1
        else:
            console.print(f"    [yellow]Extraction validation failed[/yellow]")
    
    console.print(f"  [green]Created {created} events from extraction[/green]")
    
    # 3. Clustering
    console.print("\n[bold]3. CLUSTERING[/bold]")
    clusterer = EventClusterer()
    
    events = event_store.get_all()
    unpublished = [e for e in events if not e.published_at]
    console.print(f"  Checking {len(unpublished)} unpublished events for duplicates...")
    
    merged = 0
    for event in unpublished:
        sources_list = source_store.get_by_event(event.id)
        if not sources_list:
            continue
        
        source = sources_list[0]
        candidate = {
            "source_url": str(source.url),
            "title": source.title,
            "latitude": event.latitude,
            "longitude": event.longitude,
            "date": event.start_time,
        }
        
        similar = clusterer.find_similar_events(candidate)
        for similar_event, score in similar:
            if similar_event.id != event.id and score > 0.8:
                if source.id not in similar_event.source_ids:
                    similar_event.source_ids.append(source.id)
                    event_store.update(similar_event.id, {"source_ids": similar_event.source_ids})
                    event_store.delete(event.id)
                    merged += 1
                break
    
    console.print(f"  [green]Merged {merged} duplicate events[/green]")
    
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


async def _extract_smoke_sources(sources, extractor):
    results = []
    for source in sources:
        try:
            result = await extractor.extract(
                source.title or "", source.content or "", str(source.url)
            )
        except Exception as error:
            results.append((source, None, f"unexpected_{type(error).__name__}"))
        else:
            results.append((source, result, None))
    return results


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
        "claims_created": 0,
        "claims_with_source_attribution": 0,
        "extraction_error_categories": {},
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

                    from app.extraction.gemini import claims_to_model_claims
                    from app.scoring.score import EventScorer, decide_publish
                    from app.verification.evidence import EvidenceAssessor
                    from app.verification.syndication import SyndicationDetector
                    from app.utils import parse_date_flexible
                    from app.datastore import get_claim_store
                    from app.models.schemas import SourceIndependence

                    claim_store = get_claim_store()
                    scorer = EventScorer(event_store=event_store, source_store=source_store)
                    extraction_results = asyncio.run(
                        _extract_smoke_sources(sources, extractor)
                    )
                    for source, extraction_result, extraction_error in extraction_results:
                        counts["extraction_attempts"] += 1
                        if extraction_error:
                            counts["failed_extractions"] += 1
                            category = extraction_error
                            categories = counts["extraction_error_categories"]
                            categories[category] = categories.get(category, 0) + 1
                            continue
                        if extraction_result is None:
                            counts["failed_extractions"] += 1
                            category = getattr(extractor, "last_error_category", None) or "no_result"
                            categories = counts["extraction_error_categories"]
                            categories[category] = categories.get(category, 0) + 1
                            continue
                        counts["successful_extractions"] += 1
                        if extraction_result.is_mock or not validate_extraction_result(extraction_result):
                            counts["invalid_extractions"] += 1
                            continue

                        counts["validated_events"] += 1
                        event_type = EventType(extraction_result.event_type)
                        event = Event(
                            event_type=event_type,
                            title=extraction_result.title[:200],
                            slug="",
                            description=(source.content or source.title or "")[:500],
                            summary=(
                                extraction_result.summary[:500]
                                if extraction_result.summary
                                else (source.content[:500] if source.content else source.title[:300])
                            ),
                            canonical_id=compute_article_hash(source.title or "", str(source.url)),
                            country_code=source.country or "XX",
                            country_name=source.country or "Unknown",
                            city=extraction_result.city,
                            start_time=(
                                parse_date_flexible(extraction_result.date)
                                if extraction_result.date
                                else source.published_at or datetime.utcnow()
                            ),
                            severity=5,
                            status=EventStatus.DEVELOPING,
                            confidence=ConfidenceLevel.MEDIUM,
                            source_ids=[source.id],
                        )
                        event_store.create(event)
                        claims = [
                            Claim(**claim)
                            for claim in claims_to_model_claims(
                                extraction_result.claims, event.id, source.id
                            )
                        ]
                        counts["claims_created"] += len(claims)
                        counts["claims_with_source_attribution"] += sum(
                            claim.source_id == source.id
                            and bool(claim.source_quote.strip())
                            and claim.source_quote.strip() in (source.content or "")
                            for claim in claims
                        )
                        claim_store.bulk_create(claims)

                    clusterer = EventClusterer()
                    events = event_store.get_all()
                    unpublished = [event for event in events if not event.published_at]
                    for event in unpublished:
                        event_sources = source_store.get_by_event(event.id)
                        if not event_sources:
                            continue
                        source = event_sources[0]
                        candidate = {
                            "source_url": str(source.url),
                            "title": source.title,
                            "latitude": event.latitude,
                            "longitude": event.longitude,
                            "date": event.start_time,
                        }
                        for similar_event, similarity in clusterer.find_similar_events(candidate):
                            if similar_event.id != event.id and similarity > 0.8:
                                if source.id not in similar_event.source_ids:
                                    similar_event.source_ids.append(source.id)
                                    event_store.update(
                                        similar_event.id,
                                        {"source_ids": similar_event.source_ids},
                                    )
                                    event_store.delete(event.id)
                                break

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
                        if evidence.publication_ready:
                            scores = scorer.score_event(event, event_sources)
                            if decide_publish(event, scores, source_store) != "HOLD":
                                counts["publication_eligible_candidates"] += 1
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
        ("Claims created", "claims_created"),
        ("Claims with source attribution", "claims_with_source_attribution"),
    ):
        console.print(f"  {label}: {counts[key]}")
    extraction_errors = ",".join(
        f"{category}:{count}"
        for category, count in sorted(counts["extraction_error_categories"].items())
    ) or "none"
    console.print(f"  Extraction error categories: {extraction_errors}")
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

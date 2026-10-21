from __future__ import annotations

import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional
from app.config import get_enabled_sources, get_settings, load_source_rights, load_sources
from app.sources.adapter import RSSAdapter, ReliefWebAdapter
from app.sources.fetcher import SourceFetcher, fetch_from_multiple_sources
from app.discovery.gdelt import run_gdelt_discovery, GDELTDiscovery
from app.utils import canonicalize_url, compute_article_hash
from app.models.schemas import Source, SourceType, IngestionRun
from app.datastore import get_source_store, get_ingestion_run_store
from app.diagnostics import request_error_label


class DiscoveryManager:
    """Orchestrates discovery from all sources: GDELT, RSS feeds, and article fetching."""
    
    def __init__(self):
        self.settings = get_settings()
        self.source_store = get_source_store()
        self.run_store = get_ingestion_run_store()
        self.gdelt_discovery = GDELTDiscovery()
        
        # Fetching limits from env
        self.max_articles_per_run = self.settings.max_articles_per_run
        self.max_llm_calls_per_run = self.settings.max_llm_calls_per_run

    @staticmethod
    def _safe_error(error: Exception) -> str:
        """Keep diagnostics useful without leaking URLs containing credentials."""
        return f"{type(error).__name__}: {str(error).split('?')[0][:240]}"
    
    async def close(self):
        await self.gdelt_discovery.close()
    
    async def discover_all(self, hours_back: int = 6) -> Dict[str, Any]:
        """Run complete discovery from all sources."""
        run = IngestionRun(sources_attempted=0)
        run = self.run_store.create(run)
        
        all_sources_created = 0
        all_articles = []
        all_gdelt_events = []
        
        try:
            # 1. GDELT Discovery
            print("Running GDELT discovery...")
            gdelt_result = await run_gdelt_discovery(hours_back, self.max_articles_per_run, self.max_articles_per_run // 2)
            all_gdelt_events = gdelt_result.get("events_found", 0)
            all_sources_created += gdelt_result.get("sources_created", 0)
            print(f"GDELT: {all_gdelt_events} events, {gdelt_result.get('articles_found', 0)} articles")
            
            # 2. RSS Feed Discovery + Article Fetching
            print("Running RSS feed discovery...")
            rss_sources = self._get_rss_sources()
            run.sources_attempted += len(rss_sources)
            
            if rss_sources:
                total_rss_articles = 0
                
                # Create adapters for each RSS source
                adapters = [RSSAdapter(source) for source in rss_sources]
                
                try:
                    # Fetch from all RSS feeds concurrently with limit
                    semaphore = asyncio.Semaphore(3)
                    
                    async def fetch_from_adapter(adapter: RSSAdapter):
                        async with semaphore:
                            try:
                                records = await adapter.fetch_new_records()
                                stats = adapter.get_ingestion_stats()
                                return adapter._source_config.name, records, stats
                            except Exception as e:
                                print(f"Error fetching from {adapter.connector_name}: {e}")
                                run.errors.append(self._safe_error(e))
                                run.source_health[adapter.connector_name] = "failed"
                                return adapter._source_config.name, [], adapter.get_ingestion_stats()
                            finally:
                                await adapter.close()
                    
                    tasks = [fetch_from_adapter(adapter) for adapter in adapters]
                    results = await asyncio.gather(*tasks)
                    
                    total_rss_articles = 0
                    for source_name, records, stats in results:
                        for record in records[:max(0, self.max_articles_per_run - total_rss_articles)]:
                            if record.get("available_text"):
                                # Store as source with article content
                                source = Source(
                                    publisher=record["publisher"],
                                    title=record["article_title"] or "RSS Article",
                                    url=record["original_url"],
                                    canonical_url=record["canonical_url"],
                                    published_at=datetime.fromisoformat(record["publication_time"]) if record.get("publication_time") else None,
                                    retrieved_at=datetime.utcnow(),
                                    source_type=SourceType.NEWS,
                                    language=record.get("language", "en"),
                                    article_hash=record.get("content_hash"),
                                    content=record.get("available_text"),  # Store article content for extraction
                                )
                                stored_source = self.source_store.create(source)
                                if stored_source.id != source.id:
                                    continue
                                source = stored_source
                                all_sources_created += 1
                                total_rss_articles += 1
                                run.source_health[source_name] = "healthy"
                                
                                all_articles.append({
                                    "source_id": source.id,
                                    "source_name": record["publisher"],
                                    "title": record["article_title"],
                                    "content": record.get("available_text"),
                                    "url": record["original_url"],
                                    "published_at": datetime.fromisoformat(record["publication_time"]) if record.get("publication_time") else None,
                                })
                        
                        print(f"RSS: {total_rss_articles} articles with content fetched")
                except Exception as e:
                    print(f"RSS discovery error: {e}")
            
            # 3. ReliefWeb API Discovery
            print("Running ReliefWeb API discovery...")
            reliefweb_sources = self._get_reliefweb_sources()
            run.sources_attempted += len(reliefweb_sources)
            
            total_reliefweb_articles = 0
            if reliefweb_sources:
                try:
                    # Create adapters for each ReliefWeb source
                    reliefweb_adapters = [ReliefWebAdapter(source) for source in reliefweb_sources]
                    
                    # Fetch from all ReliefWeb sources
                    for adapter in reliefweb_adapters:
                        try:
                            records = await adapter.fetch_new_records()
                            stats = adapter.get_ingestion_stats()
                            
                            total_reliefweb_articles = 0
                            for record in records:
                                if record.get("available_text"):
                                    source = Source(
                                        publisher=record["publisher"],
                                        title=record["article_title"] or "ReliefWeb Report",
                                        url=record["original_url"],
                                        canonical_url=record["canonical_url"],
                                        published_at=datetime.fromisoformat(record["publication_time"]) if record.get("publication_time") else None,
                                        retrieved_at=datetime.utcnow(),
                                        source_type=SourceType.DATASET,
                                        language=record.get("language", "en"),
                                        article_hash=record.get("content_hash"),
                                        content=record.get("available_text"),
                                    )
                                    stored_source = self.source_store.create(source)
                                    if stored_source.id != source.id:
                                        continue
                                    source = stored_source
                                    all_sources_created += 1
                                    total_reliefweb_articles += 1
                                    
                                    all_articles.append({
                                        "source_id": source.id,
                                        "source_name": record["publisher"],
                                        "title": record["article_title"],
                                        "content": record.get("available_text"),
                                        "url": record["original_url"],
                                        "published_at": datetime.fromisoformat(record["publication_time"]) if record.get("publication_time") else None,
                                    })
                            
                            print(f"ReliefWeb: {total_reliefweb_articles} articles with content fetched")
                            await adapter.close()
                        except Exception as e:
                            print(f"ReliefWeb discovery error: {e}")
                except Exception as e:
                    print(f"ReliefWeb discovery error: {e}")

            run.articles_seen = all_gdelt_events + total_rss_articles if 'total_rss_articles' in locals() else all_gdelt_events
            run.candidates_created = all_gdelt_events + (total_rss_articles if 'total_rss_articles' in locals() else 0) + (total_reliefweb_articles if 'total_reliefweb_articles' in locals() else 0)
            run.sources_succeeded = all_sources_created

            run.finished_at = datetime.utcnow()
            run.status = "completed"
            self.run_store.update(run.id, run.model_dump(mode="json"))

            return {
                "run_id": run.id,
                "gdelt_events": all_gdelt_events,
                "rss_articles_fetched": total_rss_articles if 'total_rss_articles' in locals() else 0,
                "reliefweb_articles_fetched": total_reliefweb_articles if 'total_reliefweb_articles' in locals() else 0,
                "sources_created": all_sources_created,
            }

        except Exception as e:
            run.finished_at = datetime.utcnow()
            run.status = "failed"
            run.errors.append(str(e))
            self.run_store.update(run.id, run.model_dump(mode="json"))
            raise
        finally:
            await self.gdelt_discovery.close()
    
    def _get_rss_sources(self) -> List:
        """Get enabled RSS feed sources from config."""
        from app.config import load_sources
        all_sources = load_sources()
        rss_sources = []
        for category, sources in all_sources.items():
            if category == "rss_feeds":
                for source in sources:
                    if source.enabled:
                        rss_sources.append(source)
        return sorted(rss_sources, key=lambda s: -s.priority)
    
    
    async def discover_all(self, hours_back: int = 6) -> Dict[str, Any]:
        """Run complete discovery from all sources."""
        run = IngestionRun(sources_attempted=0)
        run = self.run_store.create(run)
        
        all_sources_created = 0
        all_articles = []
        all_gdelt_events = []
        total_rss_articles = 0
        rss_feeds_attempted = 0
        rss_feeds_completed = 0
        rss_feeds_with_errors = 0
        rss_fetch_attempts = 0
        rss_fetch_successes = 0
        rss_fetch_failures = 0
        rss_discovery_errors = 0
        rss_feeds_skipped_rights = 0
        rss_feed_diagnostics = {
            "outcomes": {},
            "error_categories": {},
            "records_rejected": 0,
        }
        gdelt_diagnostics = {
            "events": {"status": "not_run", "errors": 0, "error_categories": {}},
            "gkg": {"status": "not_run", "errors": 0, "error_categories": {}},
        }
        
        try:
            # 1. GDELT Discovery
            print("Running GDELT discovery...")
            gdelt_result = await run_gdelt_discovery(hours_back, self.max_articles_per_run, self.max_articles_per_run // 2)
            all_gdelt_events = gdelt_result.get("events_found", 0)
            all_sources_created += gdelt_result.get("sources_created", 0)
            gdelt_diagnostics = gdelt_result.get("diagnostics", gdelt_diagnostics)
            print(f"GDELT: {all_gdelt_events} events, {gdelt_result.get('articles_found', 0)} articles")
            
            # 2. RSS Feed Discovery + Article Fetching
            print("Running RSS feed discovery...")
            rss_sources = self._get_rss_sources()
            rss_feeds_skipped_rights = getattr(
                self, "_rss_feeds_skipped_rights", 0
            )
            rss_feeds_attempted = len(rss_sources)
            run.sources_attempted += len(rss_sources)
            
            if rss_sources:
                fetcher = SourceFetcher()
                try:
                    # Fetch from all RSS feeds
                    rss_results = await fetch_from_multiple_sources(
                        rss_sources,
                        max_concurrent=3,
                        max_articles_per_run=self.max_articles_per_run,
                    )

                    rss_feeds_completed = len(rss_results)
                    article_results = {
                        source_name: [
                            article
                            for article in articles
                            if not (
                                article.diagnostic
                                and article.diagnostic.get("stage") == "feed"
                            )
                        ]
                        for source_name, articles in rss_results.items()
                    }
                    rss_fetch_attempts = sum(
                        len(articles) for articles in article_results.values()
                    )
                    rss_fetch_successes = sum(
                        article.success
                        for articles in article_results.values()
                        for article in articles
                    )
                    rss_fetch_failures = rss_fetch_attempts - rss_fetch_successes
                    for articles in rss_results.values():
                        has_error = False
                        for article in articles:
                            diagnostic = article.diagnostic or {}
                            if diagnostic.get("stage") == "feed":
                                status = diagnostic.get("status", "unknown")
                                outcomes = rss_feed_diagnostics["outcomes"]
                                outcomes[status] = outcomes.get(status, 0) + 1
                                rss_feed_diagnostics["records_rejected"] += int(
                                    diagnostic.get("records_rejected", 0)
                                )
                                if diagnostic.get("error_category"):
                                    category = diagnostic["error_category"]
                                    errors = rss_feed_diagnostics["error_categories"]
                                    errors[category] = errors.get(category, 0) + 1
                                    has_error = True
                            elif diagnostic.get("stage") == "record":
                                rss_feed_diagnostics["records_rejected"] += 1
                            elif diagnostic.get("error_category"):
                                category = diagnostic["error_category"]
                                errors = rss_feed_diagnostics["error_categories"]
                                errors[category] = errors.get(category, 0) + 1
                                has_error = True
                            if not article.success and diagnostic.get("stage") != "feed":
                                has_error = True
                        rss_feeds_with_errors += int(has_error)

                    for source_name, articles in rss_results.items():
                        for article in articles:
                            if (
                                article.diagnostic
                                and article.diagnostic.get("stage") == "feed"
                            ):
                                continue
                            if article.success and article.content:
                                # Store as source with article content
                                source = Source(
                                    publisher=source_name,
                                    title=article.title or "RSS Article",
                                    url=article.final_url,
                                    image_url=article.image_url,
                                    canonical_url=canonicalize_url(article.final_url),
                                    published_at=article.published_at,
                                    retrieved_at=datetime.utcnow(),
                                    source_type=SourceType.NEWS,
                                    language="en",
                                    article_hash=compute_article_hash(article.title or "", article.final_url),
                                    content=article.content,  # Store article content for extraction
                                )
                                stored_source = self.source_store.create(source)
                                if stored_source.id != source.id:
                                    continue
                                source = stored_source
                                all_sources_created += 1
                                total_rss_articles += 1
                                
                                all_articles.append({
                                    "source_id": source.id,
                                    "source_name": source_name,
                                    "title": article.title,
                                    "content": article.content,
                                    "url": article.final_url,
                                    "published_at": article.published_at,
                                })
                    
                    print(f"RSS: {total_rss_articles} articles with content fetched")
                    await fetcher.close()
                except Exception as e:
                    rss_discovery_errors += 1
                    print(f"RSS discovery failed: {request_error_label(e)}")
            
            run.articles_seen = all_gdelt_events + total_rss_articles
            run.candidates_created = all_gdelt_events + total_rss_articles
            run.sources_succeeded = all_sources_created
            
            run.finished_at = datetime.utcnow()
            run.status = "completed"
            self.run_store.update(run.id, run.model_dump(mode="json"))
            
            return {
                "run_id": run.id,
                "gdelt_events": all_gdelt_events,
                "gdelt_articles": gdelt_result.get("articles_found", 0),
                "gdelt_diagnostics": gdelt_diagnostics,
                "rss_articles_fetched": total_rss_articles,
                "rss_feeds_attempted": rss_feeds_attempted,
                "rss_feeds_completed": rss_feeds_completed,
                "rss_feeds_with_errors": rss_feeds_with_errors,
                "rss_feeds_skipped_rights": rss_feeds_skipped_rights,
                "rss_fetch_attempts": rss_fetch_attempts,
                "rss_fetch_successes": rss_fetch_successes,
                "rss_fetch_failures": rss_fetch_failures,
                "rss_discovery_errors": rss_discovery_errors,
                "rss_feed_diagnostics": rss_feed_diagnostics,
                "sources_created": all_sources_created,
            }
            
        except Exception as e:
            run.finished_at = datetime.utcnow()
            run.status = "failed"
            run.errors.append(request_error_label(e))
            self.run_store.update(run.id, run.model_dump(mode="json"))
            raise
        finally:
            await self.gdelt_discovery.close()
    
    def _get_rss_sources(self) -> List:
        """Get enabled RSS feed sources from config."""
        all_sources = load_sources()
        source_rights = load_source_rights()
        rss_sources = []
        skipped_rights = 0
        for category, sources in all_sources.items():
            if category == "rss_feeds":
                for source in sources:
                    if source.enabled:
                        rights = source_rights.get(source.name, {})
                        processing_cleared = (
                            rights.get("rights_review") == "reviewed"
                            and rights.get("current_event_processing")
                            in {"allowed", "allowed_with_attribution"}
                            and rights.get("commercial_use")
                            in {"allowed", "allowed_with_attribution"}
                            and rights.get("display_rights")
                            in {"allowed", "allowed_with_attribution"}
                            and rights.get("storage_retention_constraints")
                            in {"allowed", "allowed_with_attribution"}
                        )
                        if processing_cleared:
                            rss_sources.append(source)
                        else:
                            skipped_rights += 1
        self._rss_feeds_skipped_rights = skipped_rights
        return sorted(rss_sources, key=lambda s: -s.priority)
    
    def _get_reliefweb_sources(self) -> List:
        """Get enabled ReliefWeb API sources from config."""
        from app.config import load_sources
        all_sources = load_sources()
        reliefweb_sources = []
        for category, sources in all_sources.items():
            if category == "data_provider":
                for source in sources:
                    if source.enabled and source.connector_type == "api":
                        reliefweb_sources.append(source)
        return sorted(reliefweb_sources, key=lambda s: -s.priority)


async def run_discovery(hours_back: int = 6) -> Dict[str, Any]:
    """Main entry point for complete discovery."""
    manager = DiscoveryManager()
    try:
        return await manager.discover_all(hours_back)
    finally:
        await manager.close()

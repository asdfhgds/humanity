from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar
from threading import Lock

from app.models.schemas import Event, Source, Claim, Location, Country, Conflict, IngestionRun

T = TypeVar("T")


class JSONDataStore:
    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._locks: Dict[str, Lock] = {}
        self._index_cache: Dict[str, Dict[str, Any]] = {}

    def _get_lock(self, collection: str) -> Lock:
        if collection not in self._locks:
            self._locks[collection] = Lock()
        return self._locks[collection]

    def _get_file_path(self, collection: str) -> Path:
        return self.base_path / f"{collection}.json"

    def _load_collection(self, collection: str) -> List[Dict[str, Any]]:
        file_path = self._get_file_path(collection)
        if not file_path.exists():
            return []
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_collection(self, collection: str, data: List[Dict[str, Any]]) -> None:
        file_path = self._get_file_path(collection)
        tmp_path = file_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        tmp_path.replace(file_path)
        if collection in self._index_cache:
            del self._index_cache[collection]

    def _get_index(self, collection: str) -> Dict[str, Any]:
        if collection not in self._index_cache:
            items = self._load_collection(collection)
            self._index_cache[collection] = {item["id"]: item for item in items}
        return self._index_cache[collection]

    def create(self, collection: str, item: Dict[str, Any]) -> Dict[str, Any]:
        with self._get_lock(collection):
            items = self._load_collection(collection)
            items.append(item)
            self._save_collection(collection, items)
        return item

    def bulk_create(self, collection: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        with self._get_lock(collection):
            existing = self._load_collection(collection)
            existing.extend(items)
            self._save_collection(collection, existing)
        return items

    def get(self, collection: str, item_id: str) -> Optional[Dict[str, Any]]:
        index = self._get_index(collection)
        return index.get(item_id)

    def get_all(self, collection: str) -> List[Dict[str, Any]]:
        return self._load_collection(collection)

    def update(self, collection: str, item_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._get_lock(collection):
            items = self._load_collection(collection)
            for i, item in enumerate(items):
                if item["id"] == item_id:
                    items[i] = {**item, **updates, "updated_at": datetime.utcnow().isoformat()}
                    self._save_collection(collection, items)
                    return items[i]
        return None

    def delete(self, collection: str, item_id: str) -> bool:
        with self._get_lock(collection):
            items = self._load_collection(collection)
            new_items = [item for item in items if item["id"] != item_id]
            if len(new_items) == len(items):
                return False
            self._save_collection(collection, new_items)
            return True

    def query(self, collection: str, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        items = self._load_collection(collection)
        results = []
        for item in items:
            match = True
            for key, value in filters.items():
                if key not in item or item[key] != value:
                    match = False
                    break
            if match:
                results.append(item)
        return results

    def query_one(self, collection: str, filters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        results = self.query(collection, filters)
        return results[0] if results else None

    def count(self, collection: str, filters: Optional[Dict[str, Any]] = None) -> int:
        if filters:
            return len(self.query(collection, filters))
        return len(self._load_collection(collection))


class EventStore:
    def __init__(self, data_store: JSONDataStore):
        self.store = data_store
        self.collection = "events"

    def create(self, event: Event) -> Event:
        data = event.model_dump(mode="json")
        self.store.create(self.collection, data)
        return event

    def get(self, event_id: str) -> Optional[Event]:
        data = self.store.get(self.collection, event_id)
        return Event(**data) if data else None

    def get_by_slug(self, slug: str) -> Optional[Event]:
        data = self.store.query_one(self.collection, {"slug": slug})
        return Event(**data) if data else None

    def get_by_canonical_id(self, canonical_id: str) -> Optional[Event]:
        data = self.store.query_one(self.collection, {"canonical_id": canonical_id})
        return Event(**data) if data else None

    def update(self, event_id: str, updates: Dict[str, Any]) -> Optional[Event]:
        data = self.store.update(self.collection, event_id, updates)
        return Event(**data) if data else None

    def get_all(self, limit: Optional[int] = None, offset: int = 0) -> List[Event]:
        items = self.store.get_all(self.collection)
        items.sort(key=lambda x: x.get("start_time", ""), reverse=True)
        if limit:
            items = items[offset:offset + limit]
        return [Event(**item) for item in items]

    def get_by_country(self, country_code: str, limit: Optional[int] = None) -> List[Event]:
        items = self.store.query(self.collection, {"country_code": country_code.upper()})
        items.sort(key=lambda x: x.get("start_time", ""), reverse=True)
        if limit:
            items = items[:limit]
        return [Event(**item) for item in items]

    def get_by_type(self, event_type: str, limit: Optional[int] = None) -> List[Event]:
        items = self.store.query(self.collection, {"event_type": event_type})
        items.sort(key=lambda x: x.get("start_time", ""), reverse=True)
        if limit:
            items = items[:limit]
        return [Event(**item) for item in items]

    def get_published(self, limit: Optional[int] = None) -> List[Event]:
        items = self.store.query(self.collection, {"visibility": True})
        items.sort(key=lambda x: x.get("published_at", ""), reverse=True)
        if limit:
            items = items[:limit]
        return [Event(**item) for item in items]

    def get_publicly_visible(self, limit: Optional[int] = None) -> List[Event]:
        """Get events that should appear in public outputs (PUBLISHED or PUBLISHED_WITH_WARNING)."""
        items = self.store.query(self.collection, {"visibility": True})
        # Filter out HELD and REJECTED events
        visible_items = [
            item for item in items 
            if item.get("publication_status") in ("published", "published_with_warning")
        ]
        visible_items.sort(key=lambda x: x.get("published_at", ""), reverse=True)
        if limit:
            visible_items = visible_items[:limit]
        return [Event(**item) for item in visible_items]

    def get_recent(self, hours: int = 24, limit: Optional[int] = None) -> List[Event]:
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        items = self.store.get_all(self.collection)
        results = []
        for item in items:
            start_time = item.get("start_time")
            if start_time:
                try:
                    dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                    if dt >= cutoff:
                        results.append(item)
                except (ValueError, AttributeError):
                    pass
        results.sort(key=lambda x: x.get("start_time", ""), reverse=True)
        if limit:
            results = results[:limit]
        return [Event(**item) for item in results]

    def search(self, query: str, limit: int = 20) -> List[Event]:
        query_lower = query.lower()
        items = self.store.get_all(self.collection)
        results = []
        for item in items:
            if (query_lower in item.get("title", "").lower() or
                query_lower in item.get("description", "").lower() or
                query_lower in item.get("city", "").lower() or
                query_lower in item.get("country_name", "").lower()):
                results.append(item)
        results.sort(key=lambda x: x.get("start_time", ""), reverse=True)
        return [Event(**item) for item in results[:limit]]

    def delete(self, event_id: str) -> bool:
        return self.store.delete(self.collection, event_id)


class SourceStore:
    def __init__(self, data_store: JSONDataStore):
        self.store = data_store
        self.collection = "sources"

    def create(self, source: Source) -> Source:
        data = source.model_dump(mode="json")
        self.store.create(self.collection, data)
        return source

    def get(self, source_id: str) -> Optional[Source]:
        data = self.store.get(self.collection, source_id)
        return Source(**data) if data else None

    def update(self, source_id: str, updates: Dict[str, Any]) -> Optional[Source]:
        data = self.store.update(self.collection, source_id, updates)
        return Source(**data) if data else None

    def get_by_url(self, url: str) -> Optional[Source]:
        from app.utils import canonicalize_url
        canon = canonicalize_url(url)
        # Try canonical_url first
        data = self.store.query_one(self.collection, {"canonical_url": canon})
        if data:
            return Source(**data)
        # Fallback to exact url match (also try canonicalized)
        data = self.store.query_one(self.collection, {"url": canon})
        if data:
            return Source(**data)
        # Fallback to exact original url
        data = self.store.query_one(self.collection, {"url": url})
        return Source(**data) if data else None

    def get_by_article_hash(self, article_hash: str) -> Optional[Source]:
        data = self.store.query_one(self.collection, {"article_hash": article_hash})
        return Source(**data) if data else None

    def get_all(self) -> List[Source]:
        items = self.store.get_all(self.collection)
        return [Source(**item) for item in items]

    def get_by_event(self, event_id: str) -> List[Source]:
        event_store = EventStore(self.store)
        event = event_store.get(event_id)
        if not event:
            return []
        sources = []
        for source_id in event.source_ids:
            source = self.get(source_id)
            if source:
                sources.append(source)
        return sources


class ClaimStore:
    def __init__(self, data_store: JSONDataStore):
        self.store = data_store
        self.collection = "claims"

    def create(self, claim: Claim) -> Claim:
        data = claim.model_dump(mode="json")
        self.store.create(self.collection, data)
        return claim

    def bulk_create(self, claims: List[Claim]) -> List[Claim]:
        data = [c.model_dump(mode="json") for c in claims]
        self.store.bulk_create(self.collection, data)
        return claims

    def get(self, claim_id: str) -> Optional[Claim]:
        data = self.store.get(self.collection, claim_id)
        return Claim(**data) if data else None

    def get_by_event(self, event_id: str) -> List[Claim]:
        items = self.store.query(self.collection, {"event_id": event_id})
        return [Claim(**item) for item in items]

    def get_by_source(self, source_id: str) -> List[Claim]:
        items = self.store.query(self.collection, {"source_id": source_id})
        return [Claim(**item) for item in items]


class LocationStore:
    def __init__(self, data_store: JSONDataStore):
        self.store = data_store
        self.collection = "locations"

    def create(self, location: Location) -> Location:
        data = location.model_dump(mode="json")
        self.store.create(self.collection, data)
        return location

    def get(self, location_id: str) -> Optional[Location]:
        data = self.store.get(self.collection, location_id)
        return Location(**data) if data else None

    def get_by_coordinates(self, lat: float, lon: float, precision: float = 0.01) -> Optional[Location]:
        items = self.store.get_all(self.collection)
        for item in items:
            if (abs(item.get("latitude", 0) - lat) < precision and
                abs(item.get("longitude", 0) - lon) < precision):
                return Location(**item)
        return None

    def get_by_name_country(self, name: str, country_code: str) -> Optional[Location]:
        data = self.store.query_one(self.collection, {"name": name, "country_code": country_code.upper()})
        return Location(**data) if data else None


class IngestionRunStore:
    def __init__(self, data_store: JSONDataStore):
        self.store = data_store
        self.collection = "ingestion_runs"

    def create(self, run: IngestionRun) -> IngestionRun:
        data = run.model_dump(mode="json")
        self.store.create(self.collection, data)
        return run

    def update(self, run_id: str, updates: Dict[str, Any]) -> Optional[IngestionRun]:
        data = self.store.update(self.collection, run_id, updates)
        return IngestionRun(**data) if data else None

    def get(self, run_id: str) -> Optional[IngestionRun]:
        data = self.store.get(self.collection, run_id)
        return IngestionRun(**data) if data else None

    def get_latest(self, limit: int = 10) -> List[IngestionRun]:
        items = self.store.get_all(self.collection)
        items.sort(key=lambda x: x.get("started_at", ""), reverse=True)
        return [IngestionRun(**item) for item in items[:limit]]


def get_data_store() -> JSONDataStore:
    from app.config import get_settings
    return JSONDataStore(get_settings().data_dir)


def get_event_store() -> EventStore:
    return EventStore(get_data_store())


def get_source_store() -> SourceStore:
    return SourceStore(get_data_store())


def get_claim_store() -> ClaimStore:
    return ClaimStore(get_data_store())


def get_location_store() -> LocationStore:
    return LocationStore(get_data_store())


def get_ingestion_run_store() -> IngestionRunStore:
    return IngestionRunStore(get_data_store())

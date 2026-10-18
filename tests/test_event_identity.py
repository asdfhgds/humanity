from datetime import datetime, timedelta

from app.clustering.identity_resolver import EventIdentityResolver
from app.datastore import EventStore, JSONDataStore
from app.models.schemas import Event, EventType


def event(**overrides):
    values = {
        "event_type": EventType.ATTACK,
        "title": "Airstrike hits Al Noor hospital in Gaza City",
        "slug": "",
        "description": "An airstrike struck Al Noor hospital.",
        "summary": "Airstrike at Al Noor hospital in Gaza City.",
        "country_code": "PS",
        "country_name": "Palestine",
        "city": "Gaza City",
        "latitude": 31.501,
        "longitude": 34.466,
        "start_time": datetime(2026, 10, 12, 10),
    }
    values.update(overrides)
    return Event(**values)


def store_for(tmp_path):
    return EventStore(JSONDataStore(tmp_path))


def test_reports_about_one_occurrence_reuse_existing_canonical_id(tmp_path):
    store = store_for(tmp_path)
    original = event(canonical_id="evt_original")
    store.create(original)

    report = event(
        title="Gaza City hospital hit in airstrike, officials say",
        summary="Officials report an airstrike hit Al Noor hospital in Gaza City.",
        start_time=original.start_time + timedelta(hours=2),
        latitude=31.502,
        longitude=34.467,
    )
    resolution = EventIdentityResolver(store).resolve(report)

    assert resolution.decision == "SAME_EVENT"
    assert resolution.canonical_id == "evt_original"
    assert resolution.matched_event_id == original.id


def test_nearby_distinct_incidents_do_not_merge(tmp_path):
    store = store_for(tmp_path)
    original = event(canonical_id="evt_original")
    store.create(original)
    different = event(
        title="Market fire injures shoppers in Gaza City",
        description="A market fire injured shoppers.",
        summary="Firefighters responded to a market fire.",
        start_time=original.start_time + timedelta(hours=3),
    )

    resolution = EventIdentityResolver(store).resolve(different)

    assert resolution.decision != "SAME_EVENT"
    assert resolution.canonical_id != "evt_original"


def test_similar_reports_in_different_nearby_cities_do_not_merge(tmp_path):
    store = store_for(tmp_path)
    original = event(canonical_id="evt_original")
    store.create(original)
    nearby_city_report = event(
        title=original.title,
        summary=original.summary,
        city="Khan Yunis",
        latitude=31.35,
        longitude=34.30,
        start_time=original.start_time + timedelta(hours=1),
    )

    resolution = EventIdentityResolver(store).resolve(nearby_city_report)

    assert resolution.decision == "DISTINCT_EVENT"
    assert "named cities differ" in resolution.reasons
    assert resolution.canonical_id != "evt_original"


def test_repeated_ingestion_resolves_to_same_canonical_event(tmp_path):
    store = store_for(tmp_path)
    original = event(canonical_id="evt_stable")
    store.create(original)
    resolver = EventIdentityResolver(store)

    first = resolver.resolve(event(title="Al Noor hospital airstrike in Gaza City"))
    second = resolver.resolve(event(title="Al Noor hospital airstrike in Gaza City"))

    assert first.decision == second.decision == "SAME_EVENT"
    assert first.canonical_id == second.canonical_id == "evt_stable"


def test_legacy_event_without_identity_metadata_remains_readable(tmp_path):
    store = store_for(tmp_path)
    legacy = event(canonical_id="legacy-url-hash")
    store.create(legacy)

    loaded = store.get(legacy.id)

    assert loaded is not None
    assert loaded.canonical_id == "legacy-url-hash"
    assert loaded.event_identity is None

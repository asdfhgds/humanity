from datetime import datetime
from app.datastore import EventStore, JSONDataStore, SourceStore, ClaimStore
from app.models.schemas import Event, EventType, Source, SourceIndependence, SourceType
from app.publishing.publisher import EventPublisher

def test_insufficient_evidence_is_held(tmp_path):
    store = JSONDataStore(tmp_path); events, sources, claims = EventStore(store), SourceStore(store), ClaimStore(store)
    source = Source(publisher="Single", title="Report", url="https://single.example", source_type=SourceType.NEWS, independence_classification=SourceIndependence.UNKNOWN)
    sources.create(source)
    event = Event(event_type=EventType.ATTACK, title="Reported attack", slug="", description="Report", summary="Report", country_code="XX", country_name="Unknown", start_time=datetime.now(), source_ids=[source.id], severity=9)
    events.create(event)
    result = EventPublisher(event_store=events, source_store=sources, claim_store=claims).publish_event(event)
    assert not result["published"]
    assert events.get(event.id).publication_status.value == "held"

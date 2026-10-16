from app.models.schemas import Claim, ClaimType, Source, SourceIndependence, SourceType, VerificationStatus
from app.verification.evidence import EvidenceAssessor

def source(name, independence):
    return Source(publisher=name, title="Report", url=f"https://{name}.example", source_type=SourceType.NEWS, independence_classification=independence)

def claim(status=VerificationStatus.REPORTED):
    return Claim(event_id="event", source_id="source", claim_type=ClaimType.CASUALTY, subject="people", predicate="killed", source_quote="report", verification_status=status)

def test_high_importance_cannot_override_insufficient_evidence():
    assessment = EvidenceAssessor().assess(None, [source("one", SourceIndependence.UNKNOWN)], [claim()])
    assert not assessment.publication_ready
    assert "insufficient_independent_sources" in assessment.reasons

def test_independent_agreeing_evidence_is_eligible():
    assessment = EvidenceAssessor().assess(None, [source("one", SourceIndependence.INDEPENDENT), source("two", SourceIndependence.INDEPENDENT)], [claim()])
    assert assessment.publication_ready

def test_disputed_claims_hold_even_with_independent_sources():
    assessment = EvidenceAssessor().assess(None, [source("one", SourceIndependence.INDEPENDENT), source("two", SourceIndependence.INDEPENDENT)], [claim(VerificationStatus.DISPUTED)])
    assert not assessment.publication_ready
    assert "unresolved_claim_contradictions" in assessment.reasons

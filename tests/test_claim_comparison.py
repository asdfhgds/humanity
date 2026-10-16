from app.models.schemas import Claim, ClaimType
from app.verification.claims import ClaimComparator

def claim(value, **kwargs):
    data = dict(event_id="event", source_id=str(value), claim_type=ClaimType.CASUALTY, subject="civilians", predicate="killed", value=value, unit="people", source_quote=str(value))
    data.update(kwargs); return Claim(**data)

def test_close_casualty_claims_corroborate():
    assert ClaimComparator().compare(claim(23), claim(21)).relationship == "corroborates"
def test_conflicting_counts_are_not_averaged():
    assert ClaimComparator().compare(claim(23), claim(80)).relationship == "contradicts"
def test_incompatible_subjects_remain_separate():
    assert ClaimComparator().compare(claim(23), claim(23, subject="combatants")).relationship == "different_scope"

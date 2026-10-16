"""Conservative, provenance-preserving comparison of claims."""
from __future__ import annotations
from app.models.schemas import Claim, ClaimComparison


class ClaimComparator:
    def compare(self, a: Claim, b: Claim) -> ClaimComparison:
        if a.event_id != b.event_id or a.claim_type != b.claim_type:
            return self._result(a, b, "cannot_compare", "different event or claim type", .95)
        if self._period_differs(a, b):
            return self._result(a, b, "different_reporting_period", "reporting periods differ", .9)
        if self._scope_differs(a, b):
            return self._result(a, b, "different_scope", "subjects or geographies differ", .8)
        av, bv = self._value(a), self._value(b)
        if av is None or bv is None or (a.normalized_unit or a.unit) != (b.normalized_unit or b.unit):
            return self._result(a, b, "cannot_compare", "missing or incompatible normalized values", .75)
        low, high = sorted((av, bv)); ratio = (high - low) / max(high, 1)
        if ratio == 0: return self._result(a, b, "supports", "same reported value", .95)
        if ratio <= .1: return self._result(a, b, "corroborates", "values are close", .85)
        if ratio <= .35: return self._result(a, b, "partially_agrees", "values overlap in scale but differ", .75)
        return self._result(a, b, "contradicts", "materially different reported values", .85)

    def _result(self, a, b, relationship, details, confidence):
        return ClaimComparison(claim_id_a=a.id, claim_id_b=b.id, relationship=relationship, details=details, confidence=confidence)
    @staticmethod
    def _value(claim): return claim.normalized_value if claim.normalized_value is not None else claim.value
    @staticmethod
    def _period_differs(a, b):
        return bool(a.reporting_period_start and b.reporting_period_start and (a.reporting_period_start != b.reporting_period_start or a.reporting_period_end != b.reporting_period_end))
    @staticmethod
    def _scope_differs(a, b):
        return bool(a.geography and b.geography and a.geography.casefold() != b.geography.casefold()) or a.subject.casefold() != b.subject.casefold()

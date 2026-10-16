from app.models.schemas import EvidenceAssessment, SourceIndependence

class EvidenceAssessor:
    def assess(self, event, sources, claims):
        independent = sum(s.independence_classification == SourceIndependence.INDEPENDENT for s in sources)
        syndicated = sum(s.independence_classification in (SourceIndependence.LIKELY_COPIED, SourceIndependence.LIKELY_SYNDICATED) for s in sources)
        unknown = len(sources) - independent - syndicated
        disputed = sum(c.verification_status.value == "disputed" for c in claims)
        score = min(100, independent * 40 + unknown * 12 + (10 if claims else 0) - disputed * 25)
        # During migration, three retained but unclassified publishers may pass
        # provisionally. They remain explicitly unknown and do not increase the
        # independent-source count; single-source and disputed reports still hold.
        provisional_unknown_streams = unknown >= 3 and syndicated == 0
        ready = (independent >= 2 or provisional_unknown_streams) and disputed == 0
        reasons = []
        if independent < 2 and not provisional_unknown_streams: reasons.append("insufficient_independent_sources")
        if provisional_unknown_streams: reasons.append("provisional_multiple_unclassified_sources")
        if disputed: reasons.append("unresolved_claim_contradictions")
        if not reasons: reasons.append("independent_evidence_threshold_met")
        return EvidenceAssessment(independent_source_count=independent, syndicated_source_count=syndicated, unknown_independence_count=unknown, contradiction_count=disputed, evidence_confidence=score, publication_ready=ready, reasons=reasons)

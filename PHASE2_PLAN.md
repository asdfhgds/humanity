# Phase 2 Implementation Plan

## Repository Reconnaissance Summary

### Current Architecture
- **Python 3.11+** with async/await throughout
- **Pydantic v2** for data validation
- **JSON file-based datastore** with locking (no database)
- **Source fetching** via `SourceFetcher` (RSS, HTML, generic)
- **Discovery**: GDELT (events + GKG), RSS feeds
- **Extraction**: Gemini LLM (structured JSON output, validated via Pydantic)
- **Clustering**: EventClusterer with fuzzy matching (RapidFuzz), spatial/temporal proximity
- **Scoring**: Weighted formula (importance 0.25, human_impact 0.20, novelty 0.15, corroboration 0.15, velocity 0.10, public_interest 0.10, unusualness 0.05)
- **Publication**: Three-tier (PUBLISH ≥70, PUBLISH_WARNING 55-70, HOLD <55), HELD events excluded from public artifacts
- **Frontend**: Astro + MapLibre GL JS, static JSON/GeoJSON generation

### Existing Tests (32 passing)
- Utils (5), Models (4), Datastore (3), Clustering (3), Scoring (3), Extraction (4), Integration (3)
- Publication Boundary (4): HELD exclusion, AUTO_PUBLISH inclusion, warning flag, counter integrity
- Idempotency & Revisions (2): canonical_id updates, HELD status respect, stats consistency

### Source Registry (sources.yaml)
- 12 global_news, 4 regional_news, 4 government, 4 ngo, 2 data_provider, 11 rss_feeds
- Only BBC RSS + GDELT are actively tested; others enabled but untested
- ACLED disabled (requires registration)

### Current Gaps for Phase 2
1. **Source adapter interface** - No common interface; SourceFetcher is monolithic
2. **ReliefWeb connector** - Not implemented (configured but not implemented)
3. **Syndication detection** - No detection of wire-service/copied content
4. **Claim comparison** - Claims stored but not compared across sources
5. **Event identity** - `canonical_id` is URL-hash based, not true event identity
6. **Claim model** - Missing fields: geography, date/period, extraction metadata, min/max for normalization
7. **Claim comparison** - No claim comparison logic
8. **Evidence assessment** - No explicit source independence tracking
8. **ReliefWeb adapter** - Not implemented

---

## Phase 2 Implementation Plan

### Milestone 1: Source Adapter Interface & RSS Generalization (Week 1)

**Objective**: Create a standard `SourceAdapter` interface and generalize the RSS fetcher.

**Tasks**:
1. Create `app/sources/adapter.py` with abstract `SourceAdapter` base class:
   - `fetch_new_records(since: datetime) -> List[NormalizedSourceRecord]`
   - `normalize(record) -> NormalizedSourceRecord`
   - `supports_pagination() -> bool`
   - `get_source_identity() -> SourceIdentity`
   - `handle_rate_limit() -> None`
   - `get_ingestion_stats() -> IngestionStats`

2. Define `NormalizedSourceRecord` dataclass (matching Phase 2 spec):
   - source_record_id, publisher, publisher_domain, original_url, canonical_url
   - article_title, publication_time, retrieval_time, language
   - available_text, original_source_type, content_hash
   - syndication_hints, origin_hints

3. Refactor `RSSFetcher` to implement `SourceAdapter` interface
4. Update `sources.yaml` to include `role` field (DISCOVERY/EVIDENCE/STRUCTURED_DATA)
5. Update `DiscoveryManager` to use adapter interface

**Tests**: Add tests for RSS adapter, pagination, rate limiting

---

### Milestone 2: ReliefWeb API Adapter (Week 2)

**Objective**: Implement ReliefWeb API connector for humanitarian reports.

**Tasks**:
1. Create `app/sources/reliefweb.py` implementing `SourceAdapter`:
   - Use documented API: `https://api.reliefweb.int/v1/reports`
   - Required params: `appname`, `profile`, `limit`, `offset`, `fields`, `filter`
   - OAuth not required but `appname` parameter required
   - Implement bounded pagination with `limit`/`offset`
   - Request caching (ETag/Last-Modified)
   - Rate limiting per ReliefWeb guidelines (1 req/sec)

2. Normalize ReliefWeb reports to `NormalizedSourceRecord`:
   - Extract: title, body, date, countries, disasters, sources, URL
   - Preserve original URLs and attribution
   - Record API metadata in source record

3. Add to `sources.yaml`:
   ```yaml
   structured_data:
     - name: "ReliefWeb API"
       url: "https://api.reliefweb.int/v1/reports"
       type: "api"
       role: "STRUCTURED_DATA"
       country: "global"
       poll_interval_hours: 2
       enabled: true
       api_params:
         appname: "humanity"
         profile: "full"
         limit: 100
   ```

3. Add network test fixture (recorded API response)
4. Unit tests with mocked API response

**Tests**: Unit tests with mocked API, integration test (optional, network-dependent)

---

### Milestone 3: GDELT Connector Hardening (Week 2-3)

**Objective**: Make GDELT connector production-ready with proper diagnostics.

**Tasks**:
1. Add comprehensive logging to `GDELTDiscovery`:
   - Request URL, response status, content-type, size
   - Redirect chain logging
   - Parse errors with row context
   - Retry attempt logging

2. Fix known issues:
   - Master file URL uses HTTP (not HTTPS) - verify if HTTPS works
   - ZIP/GZIP decompression error handling
   - CSV parsing error recovery (skip bad rows, log errors)
   - Master file parsing: handle multiple file formats

3. Add connector health check:
   - `check_health() -> ConnectorHealth` method
   - Returns: reachable, last_success, last_error, avg_latency_ms

3. Ensure GDELT failure doesn't block RSS/ReliefWeb:
   - Wrap GDELT in try/catch in `DiscoveryManager`
   - Continue other sources on GDELT failure

4. Add diagnostics to `IngestionRun`:
   - gdelt_requests, gdelt_success, gdelt_errors, gdelt_latency_ms

**Tests**: Network test with real GDELT (optional), unit tests with mocked responses

---

### Milestone 4: Event Identity & Canonical Event Model (Week 3)

**Objective**: Replace URL-hash-based `canonical_id` with true event identity.

**Tasks**:
1. Add to `Event` schema (`schemas.py`):
   ```python
   event_identity: EventIdentity = Field(default_factory=EventIdentity)
   
   class EventIdentity(BaseModel):
       canonical_id: str  # stable hash of (type, location, date, actors)
       candidate_ids: List[str] = []  # source record IDs that contributed
       match_confidence: float = 0.0  # confidence in this identity
       match_method: str = "deterministic"  # deterministic|model|hybrid
       matched_with: List[str] = []  # other event IDs this merged with
   ```

2. Create `EventIdentityResolver` service:
   - Deterministic matching: type + location (geohash) + date window + actor name similarity
   - Model-assisted for ambiguous cases (title + content similarity via embeddings)
   - Configurable thresholds per event type

3. Update `EventClusterer` to use `EventIdentityResolver`:
   - Replace `_compute_candidate_similarity` with resolver
   - Output: merged event + match confidence + match method

3. Update `canonical_id` generation:
   - Assign an opaque, persisted ID for each newly identified occurrence.
   - Reuse an existing canonical ID only after conservative matching on event
     type, time, geography, and text evidence.
   - Do not make a hash of location, date, and event type the permanent
     identity; keep legacy IDs readable without rewriting stored records.

**Tests**: 
- Deterministic: same type/location/date/actors → same canonical_id
- Model-assisted: ambiguous cases resolved
- Different events same city/date → separate canonical_ids

---

### Milestone 5: Syndication & Source Independence Detection (Week 4)

**Objective**: Detect wire-service copies and prevent false corroboration.

**Tasks**:
1. Add `SourceRelationship` model:
   ```python
   class SourceRelationship(BaseModel):
       source_id_a: str
       source_id_b: str
       relationship: Literal["independent", "likely_syndicated", "likely_copied", "unknown"]
       evidence: List[str]  # e.g., ["exact_text_match_95%", "same_wire_service:reuters"]
       confidence: float
       detected_at: datetime
   ```

2. Implement `SyndicationDetector`:
   - Text similarity (MinHash/SimHash for near-duplicate detection)
   - Wire service detection: check for "Reuters", "AP", "AFP" in byline/source
   - Origin URL tracking: track `original_source_url` if present
   - Quote fingerprinting: hash distinctive quotes (50+ chars)
   - Publisher domain relationships (known syndicates)

2. Update `Source` schema:
   ```python
   source_relationships: List[SourceRelationship] = []
   independence_classification: Literal["independent", "likely_syndicated", "likely_copied", "unknown"] = "unknown"
   original_report_id: Optional[str] = None  # ID of originating report if syndicated
   ```

3. Update scoring:
   - `_score_corroboration` uses independence classification
   - Syndicated sources count as 0.25 weight vs 1.0 for independent
   - Unknown = 0.5 (conservative)

**Tests**:
- 5 identical copies from different domains → 1 independent, 4 syndicated
- Same wire service attribution → likely_syndicated
- Different domains, same quotes → likely_copied

---

### Milestone 6: Claim Model Extension & Comparison (Week 5)

**Objective**: Extend claim model and implement cross-source claim comparison.

**Tasks**:
1. Extend `Claim` schema (`schemas.py`):
   ```python
   class Claim(BaseModel):
       # ... existing fields ...
       geography: Optional[str] = None  # e.g., "Gaza City", "Donetsk region"
       relevant_date: Optional[datetime] = None
       relevant_period_start: Optional[datetime] = None
       relevant_period_end: Optional[datetime] = None
       extraction_method: str = "gemini"  # gemini|manual|rule
       extraction_model_version: Optional[str] = None
       normalized_value: Optional[float] = None  # after unit conversion
       normalized_unit: Optional[str] = None
   ```

2. Create `ClaimComparator` service:
   - Compare claims of same type for same event
   - Classification: supports, corroborates, partially_agrees, contradicts, different_scope, different_period, cannot_compare
   - Unit normalization (people/persons, km/miles, USD/EUR)
   - Range overlap analysis for min/max values
   - Scope analysis (civilians vs combatants, killed vs injured)

2. Update `Claim` schema with `comparison_results: List[ClaimComparison]`:
   ```python
   class ClaimComparison(BaseModel):
       claim_id_a: str
       claim_id_b: str
       relationship: Literal["supports", "corroborates", "partially_agrees", "contradicts", "different_scope", "different_period", "cannot_compare"]
       overlap_details: Optional[str] = None
       confidence: float
   ```

3. Update `build_event_page_data` to include claim comparisons in output

**Tests**:
- "23 killed" vs "at least 21 killed" → supports
- "23 civilians killed" vs "23 people killed (including combatants)" → partially_agrees
- "23 killed this week" vs "23 cumulative" → different_period

---

### Milestone 7: Evidence & Confidence Assessment (Week 5)

**Objective**: Transparent evidence assessment replacing single confidence score.

**Tasks**:
1. Create `EvidenceAssessment` model:
   ```python
   class EvidenceAssessment(BaseModel):
       event_id: str
       independent_source_count: int
       syndicated_source_count: int
       unknown_independence_count: int
       source_provenance_score: float  # 0-1: primary vs secondary
       specificity_score: float  # specificity of evidence
       recency_score: float  # hours since event
       agreement_score: float  # cross-source agreement
       contradiction_penalty: float
       location_certainty: float
       date_certainty: float
       structured_data_corroboration: float = 0.0
       independence_established: bool = False
       
       # Derived
       evidence_confidence: float  # 0-100
       publication_ready: bool
   ```

2. Create `EvidenceAssessor` service:
   - Computes all factors above
   - Produces machine-readable publication reason

2. Update `decide_publish` to use `EvidenceAssessment`:
   - HOLD if evidence_confidence < threshold OR high_impact_claims_unverified
   - Require minimum independent sources for high-impact claims
   - Document formula in `scoring.yaml`

3. Update `Event` schema:
   ```python
   evidence_assessment: Optional[EvidenceAssessment] = None
   publication_reason: Optional[str] = None  # machine-readable reason
   ```

**Tests**:
- High importance + single source → HOLD with reason
- Multiple independent sources + agreement → PUBLISH
- Contradictory high-impact claims → HOLD with reason

---

### Milestone 8: Pipeline Optimization & Mock Isolation (Week 5-6)

**Objective**: Efficient Gemini usage and strict mock isolation.

**Tasks**:
1. Optimize extraction pipeline in `DiscoveryManager`:
   - Fetch metadata only (titles, URLs, dates)
   - Canonicalize URLs, deduplicate by `canonicalize_url` + `article_hash`
   - Deterministic relevance filter (keyword + severity + location)
   - Group likely-related reports before extraction
   - Batch extraction per group

2. Mock isolation:
   - `GeminiExtractor` has `mock_mode: bool` (default from env)
   - Mock provider in `tests/fixtures/mock_gemini.py`
   - Add `is_mock: bool` field to `ExtractionResult`
   - Publisher rejects events with `is_mock=True` in production mode
   - CLI flag `--mock` for development only

3. Add per-run budgets:
   - `max_llm_calls_per_run` enforced in `DiscoveryManager`
   - `max_articles_per_run` enforced
   - Request timeouts and retries with backoff

**Tests**: 
- Budget enforcement (exceed max_llm_calls → stop)
- Mock rejection in production mode

---

### Milestone 9: Source Registry Expansion & Diagnostics (Week 6)

**Objective**: Complete source registry and add ingestion diagnostics.

**Tasks**:
1. Update `sources.yaml` with new fields:
   ```yaml
   - name: "ReliefWeb API"
     url: "https://api.reliefweb.int/v1/reports"
     type: "api"
     role: "STRUCTURED_DATA"  # DISCOVERY|EVIDENCE|STRUCTURED_DATA
     connector: "reliefweb"  # connector class name
     enabled: true
     auth_required: false
     license_notes: "Open data, attribution required"
     rate_limit: 1  # req/sec
     source_independence_group: "un"  # for independence tracking
   ```

2. Add ingestion diagnostics to `IngestionRun` and CLI:
   - Run ID, connector, start/end time
   - Request count, success/failure, records fetched
   - New/duplicate records, candidate events
   - Extraction calls, events created/updated
   - Claims created/compared, events published/held
   - Errors with retry counts

3. Add CLI command: `python -m app.cli.main stats --detailed`

**Tests**: CLI stats output verification

---

### Milestone 10: Testing & Documentation (Week 6-7)

**Objective**: Comprehensive test coverage and documentation.

**Tests to Add** (per Phase 2 spec):
- Test A: Multiple independent reports (3 independent + 2 syndicated → 1 event, independence tracking)
- Test B: Conflicting casualty counts (3 sources, different counts → separate claims, no averaging)
- Test C: Nearby events same city, different dates (separate events)
- Test D: Same city, same day, different incidents (no merge)
- Test E: Syndicated story (shared origin recorded, no false independence)
- Test F: Missing location/date (unknown fields preserved)
- Test G: Source failure (one timeout, others continue)
- Test H: Idempotency (re-process same data → no duplicates)
- Test I: Publication boundary (high-interest HELD → no public artifacts)

**Documentation**:
- Update `PROJECT_STATUS.md` with Phase 2 status
- Document: implemented connectors, live-tested providers, claim comparison logic, publication safeguards
- Add reproduction commands

---

## Dependencies & Risks

### New Dependencies (if needed)
- `minhash`/`datasketch` for syndication detection (or implement simple SimHash)
- `sentence-transformers` for model-assisted matching (optional, can be lazy-loaded)

### Risks
1. **GDELT network unreliability** → Mitigated by retry/backoff, isolation from other sources
2. **Gemini quota/cost** → Mitigated by deterministic filtering, batching, per-run budgets
3. **ReliefWeb API changes** → Version detection, graceful degradation
4. **Model-assisted matching quality** → Conservative thresholds, human review queue for ambiguous

---

## Timeline Summary

| Week | Milestone | Deliverable |
|------|-----------|-------------|
| 1 | Source Adapter + RSS | `SourceAdapter` interface, generalized RSS |
| 2 | ReliefWeb + GDELT | ReliefWeb adapter, GDELT hardening |
| 3 | Event Identity | `EventIdentityResolver`, stable canonical IDs |
| 4 | Syndication Detection | `SyndicationDetector`, independence tracking |
| 5 | Claim Model + Comparison | Extended `Claim`, `ClaimComparator` |
| 6 | Evidence Assessment | `EvidenceAssessor`, publication reasons |
| 6 | Pipeline Optimization | Efficient Gemini, mock isolation |
| 6 | Source Registry + Diagnostics | Enhanced config, diagnostics CLI |
| 7 | Testing + Docs | 9 new tests, updated PROJECT_STATUS.md |

---

## Next Steps

1. **Start Milestone 1**: Create `SourceAdapter` interface and refactor RSS fetcher
2. **Validate approach**: Run existing tests to ensure no regressions
3. **Iterate**: Each milestone builds on previous, test continuously

---

**Ready to begin implementation. Shall I start with Milestone 1 (Source Adapter Interface)?**

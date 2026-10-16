# HUMANITY Project Status

## Current Status

Phase 2, Milestone 2.9 — Phase 2 Integration Gate.

## Status

PASS WITH LIMITATIONS

## Completed

- Replaced the duplicated and non-functional identity resolver with deterministic, conservative matching.
- Separated source-report identity from real-world event identity: new occurrences receive opaque persistent `evt_` IDs rather than location/date/type hashes or article URLs.
- Added explicit `SAME_EVENT`, `DISTINCT_EVENT`, and `UNCERTAIN` outcomes with reproducible reasons and confidence.
- Integrated resolution with clustering and canonical-ID assignment during publishing.
- Added additive identity audit fields (`decision`, `decision_reasons`, aliases, merge history) without breaking legacy event records.
- Added regression tests for same-event reports, nearby distinct incidents, idempotent re-ingestion, and legacy records.
- Added source relationship and independence models plus a syndication detector for shared origins, duplicate content, near-identical reporting, and wire attribution.
- Updated corroboration scoring so suspected copied/syndicated reports do not inflate independent-source counts.
- Extended claims with scope, period, normalized-value and extraction provenance fields; added conservative claim comparison.
- Added a separate evidence assessor; evidence qualification remains distinct from importance and produces explainable readiness reasons.
- Integrated evidence eligibility into publishing: insufficient evidence becomes HELD, is non-visible, records machine-readable reasons, and removes an old event-page artifact.
- Added mock/production isolation, extraction budgets, ingestion diagnostics, and sanitised connector failure reporting.
- Integrated source-independence detection before evidence assessment; headline-only similarity cannot label sources as syndicated.

## Files Changed

- `app/clustering/identity_resolver.py`
- `app/clustering/cluster.py`
- `app/models/schemas.py`
- `app/publishing/publisher.py`
- `tests/test_event_identity.py`
- `app/verification/syndication.py`
- `tests/test_syndication.py`
- `app/verification/claims.py`
- `tests/test_claim_comparison.py`
- `app/verification/evidence.py`
- `tests/test_evidence.py`
- `tests/test_publication_policy.py`
- `tests/test_pipeline_safety.py`
- `PROJECT_STATUS.md`
- `PHASE2_PLAN.md`

## Tests

- Baseline: `python -m pytest tests -q` — 51 passed.
- Focused Phase 2.4 tests: 7 passed.
- Focused Phase 2.3–2.5 tests: 10 passed.
- Focused Phase 2.3–2.6 tests: 13 passed.
- Focused evidence/publication-policy tests: 4 passed.
- Full available suite: `python -m pytest tests -q` — 63 passed (warnings remain from legacy `utcnow()` use).
- `git diff --check` was attempted, but the local Git command did not return within the command window; no source-file formatting issue was observed in the test run.

## Live Verification

- Live BBC RSS retrieval succeeded.
- Live Gemini authentication and structured extraction succeeded using a
  supplied key, with `gemini-flash-latest`. The key was used only in-process
  and was not written to this repository.
- The previous `gemini-1.5-flash` default was unavailable; the application and
  workflow defaults now use the verified rolling Flash model alias.
- Cloudflare deployment was not verified: none of the supplied candidate
  values validated as a bearer API token, so no deployment was attempted.

## Data Quality

- A matching URL remains only a source-record deduplication signal.
- Event merges require compatible type, close time and geography, plus strong text similarity.
- Ambiguous candidates remain unmerged.
- Existing canonical IDs and legacy records remain readable; no stored source or claim records were deleted.

## Deployment

Not evaluated for this milestone.

## Known Limitations

- Actor extraction is not yet available as a structured matching signal.
- The ingestion CLI still creates events directly and needs a later pipeline integration pass to route every extracted event through the clusterer.
- No migration rewrites historical URL-based canonical IDs; they are retained as legacy IDs to prevent unsafe data changes.
- Production launch remains blocked on real Gemini credentials, persistent workflow state, a repaired frontend dependency/lockfile installation, configured deployment credentials, and successful browser verification of a deployed site.
- `web/package-lock.json` is now present, but the local `node_modules` tree is
  corrupt (`astro/astro.js` is missing); frontend check/build remains unverified.

### Launch-preparation update (2026-10-16)

- A clean `npm ci` repair was started after moving the prior `node_modules` to
  `node_modules.corrupt-backup`. It restored Astro runtime files that had been
  missing, but local Node/Astro verification commands did not complete in the
  available command window. No frontend build is claimed as passing.
- The supplied Cloudflare bearer-token candidates and global-key/email pair
  both failed read-only Cloudflare authentication. No deployment was attempted.
- Regenerating the lockfile completed, but `npm ci` stalled before restoring
  Astro and was stopped. The dependency tree remains unverified.
- Supplied Cloudflare values were tested as both bearer API tokens and global
  API keys (when an email value was available); none authenticated, so no
  deployment request was made.

## Next Milestone

Phase 3, Milestone 3.1 — Live Source Verification.

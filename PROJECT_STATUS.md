# HUMANITY Project Status

## Current Status

Editorial redesign release verification — static site release gates complete.

## Status

READY TO PUBLISH — live ingestion and deployment have not been verified.

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
- Production launch remains blocked on real Gemini credentials, persistent workflow state, configured deployment credentials, and successful browser verification of a deployed site.
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

### Frontend reliability and editorial redesign (2026-10-16)

- Added statically generated event detail routes from published event indexes and fixed event links to use available slugs.
- Added base-path-aware client links and fetches for GitHub Pages, plus a missing favicon and shared design stylesheet.
- Redesigned the homepage and key topic-page headers with original SVG illustrations labeled as illustrative, not event photography; added an image-credits page.
- Removed rendering of unverified source-page images and replaced invented country, conflict, and human-cost fallbacks with explicit unavailable/empty states.
- Updated feed refresh indicators and client refreshes to run every minute. The ingestion workflow runs every five minutes on GitHub Actions’ best-effort schedule and deploys its generated Pages artifact; neither interval guarantees fresh source data.
- Validation: `npm run build` passed with the GitHub Pages base path; 32 publisher regression tests and two source-image metadata tests passed; a built-site audit found no missing local links or assets among 314 references; local browser review found no horizontal overflow at a 216px viewport.
- Live source data was not available in this checkout for end-to-end feed verification. The deployed GitHub Pages site has not yet been checked, and this worktree has not been deployed.

## Next Milestone

Phase 3, Milestone 3.1 — Live Source Verification.

### Editorial redesign follow-up (2026-10-17)

- Replaced the dashboard-style home hero with an editorial masthead, photo-led introduction, published-record lead, attributed latest-report imagery, global map, topic index, daily-brief entry point, and evidence-methodology note. Updated the site palette, typography, badges, map styling, and key topic-page headers to a restrained paper-and-ink design.
- Added seven locally stored Wikimedia Commons images with alt text, archival/context captions, creator, source page, and license links. Licenses: Kefr4000 protest photo (CC0 1.0); Yan Boechat / Voice of America damaged housing (public domain, PD-VOA); Julien Harneis Congo water distribution (CC BY-SA 2.0); Mark Knobil Darfur camp (CC BY 2.0); Jessica Lea / UK Department for International Development Nepal response (CC BY 2.0); Chuck Kennedy / U.S. Department of State Security Council meeting (public domain); Kingfiser Equal Earth reference map (CC0 1.0). The image-credits page now displays each local image and its attribution.
- Event-detail routes now come only from the publisher’s public event index and published statuses. Removed a synthetic “Test Conflict” and its stale map, country, timeline, and detail-data artifacts; the current published index is empty, so no event page or lead report is generated.
- `npm ci` completed successfully (529 packages installed). npm reported 22 dependency vulnerabilities (11 moderate, 9 high, 2 critical); no dependency upgrade was attempted.
- `npm run build` with `GITHUB_ACTIONS=true` passed and generated 13 static pages. The existing MapLibre bundle-size warning remains (about 802 kB minified).
- `python -m pytest tests -q` passed: 65 tests (448 existing deprecation warnings).
- Production-output audit passed: 13 HTML pages, 314 local link/asset references, zero missing references, zero broken local fragments, seven copied editorial images (2,381,169 bytes), and no remaining test-story references.
- The first `astro check` attempt exhausted a 1.5 GB Node heap while TypeScript traversed generated `dist` bundles. `web/tsconfig.json` now excludes generated output and dependencies; the checker then completed without an OOM, but exited with 251 existing source diagnostics across legacy inline scripts and DOM code. The new homepage, image-credit, image, and news components had no reported diagnostics. Type-checking is therefore not passing.
- Browser-canvas DOM inspection at its 216 px viewport found no horizontal overflow (201 px document width). Screenshot capture timed out, the canvas fell back to a local-file preview that cannot validate HTTP feed/image requests, and no event detail can be checked while the published event index is empty. Desktop/browser-console verification remains incomplete.
- No commit or deployment was made. The redesigned output is not ready to publish until the remaining Astro diagnostics and browser verification are resolved.

### Release hardening follow-up (2026-10-17)

- Reconciled public event detail files, map features, country records, timelines, and aggregate feeds from the currently visible published-event set. A transition to HELD/REJECTED now removes stale public artifacts rather than leaving orphaned routes or map links.
- Isolated publisher integration tests with a temporary working directory and web output path while retaining the repository source configuration. The full test suite now leaves checkout data and public outputs untouched.
- Removed the confirmed synthetic “Test Conflict” detail, country, timeline, and map artifacts. The generated map is an empty FeatureCollection, matching the empty published event feeds; no test-story text remains in the production output.
- Clean `npm ci` completed with 511 packages. npm reported 20 total advisories; the production dependency audit reports 15 (7 moderate, 7 high, 1 critical). All production findings currently resolve only through a major Astro 7.4.0 upgrade, which is incompatible with the current Astro Tailwind integration without a broader migration. Do not publish until this critical advisory is addressed or formally accepted.
- `npm run check` passed: 0 errors, 0 warnings, 0 hints.
- `GITHUB_ACTIONS=true npm run build` passed and generated 13 static pages. MapLibre remains deferred until the map is approached, but its chunk is 1,076 kB minified (292 kB gzip) and triggers Vite's 500 kB chunk warning.
- `python -m pytest tests -q` passed: 66 tests; 493 existing deprecation warnings remain. The published-to-HELD and published-to-REJECTED transitions are both covered.
- Production audit passed: 13 HTML pages and 314 local references, with zero missing routes/assets, zero broken fragments, and no synthetic test-story references. All seven locally stored editorial images (2,381,169 bytes total) returned HTTP 200; credits retain creator, source, license, and usage context.
- Local HTTP/browser QA passed for the homepage and topic routes. At 1280×900 desktop and 375×812 mobile widths there was no horizontal overflow; the lead image loaded, the empty map feed returned HTTP 200, MapLibre rendered and fetched OpenStreetMap tiles on scroll, and the map displayed its explicit no-published-locations message. No failed browser requests or HTTP error responses were observed.
- The current public event feed is empty, so the site correctly shows editorial empty states and generates no event-detail page. Browser inspection of a real published event page remains unavailable until eligible published data exists; publisher behavior is covered by regression tests.
- No commit or deployment was made. **Not ready to publish** while the critical production dependency advisory remains unresolved.

### Framework security and release verification (2026-10-17)

- Upgraded Astro to 7.4.0 and Tailwind CSS to 4.3.3, replacing the incompatible/deprecated `@astrojs/tailwind` integration with `@tailwindcss/vite`. Updated the CSS entry point and utilities for Tailwind 4 and moved both GitHub Actions workflows to Node 24 (Astro 7.4.0 requires Node 22.22 or later).
- Replaced build-time public-data path resolution based on bundled `import.meta.url` with explicit project-root resolution. The previous implementation caused the upgraded build to treat existing event and stats feeds as missing. A temporary published-event fixture successfully generated `/event/2026/10/17/release-check/`; fixture feeds were restored and the final empty-feed build generates no event detail routes.
- `npm ci` completed successfully with 316 packages. `npm audit` reports **0 vulnerabilities**, including production dependencies.
- `npm run check` passed: 0 errors, 0 warnings, 0 hints. `GITHUB_ACTIONS=true npm run build` passed and generated 13 pages. The deferred MapLibre chunk remains about 1.07 MB minified and triggers a >500 kB advisory warning.
- After the final framework/CSS migration, `python -m pytest tests -q` passed: 66 tests; 493 existing deprecation warnings remain.
- Re-audited final output: 13 pages, 314 local references, no missing local routes/assets/fragments, and no synthetic test story. The current published event feeds are empty and map GeoJSON is an empty FeatureCollection.
- Local browser preview after framework migration: the homepage rendered with its licensed archival lead image and correct editorial background/type. Responsive checks at 375x812 and 1280x900 found no horizontal overflow. All expected routes and local assets returned HTTP 200, the map initialized on scroll and fetched OpenStreetMap tiles, and there were no HTTP error responses or JavaScript exceptions. Browser viewport changes cancelled two in-flight requests; no assets failed.
- All requested local validation gates now pass. This supersedes the prior Astro-advisory blocker. The static site is ready to publish; deployment was not requested or attempted. Live ingestion still depends on configured external credentials and the scheduled workflow, and the published feed is currently empty.

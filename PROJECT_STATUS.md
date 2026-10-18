# HUMANITY Project Status

## Current Status

Release verification — code deployed; live extraction is rate-limited; publishing remains gated.

## Status

NOT READY FOR PRODUCTION PUBLISHING. PR #2 was merged at
`f623a3f2c36c87975a3113038e0ae64c51d08ff2`; smoke diagnostics and a single-loop
Gemini client fix were merged in PR [#3](https://github.com/asdfhgds/humanity/pull/3)
at `98652d744a1168b5991410cdf5f6d0709b960de6`. A follow-up isolated live run
on `master` successfully extracted 3 of 10 articles and validated 2 events,
but quota/rate limiting stopped the other 7 and no candidate met publication
policy. The code-only Pages deployment now serves the current site; public
feeds and statistics report zero published events. Production ingestion,
auto-publication, and recurring schedules remain disabled.

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

### Post-merge workflow failure investigation (2026-10-17)

- Deployment run [39033430844](https://github.com/asdfhgds/humanity/actions/runs/39033430844) failed at `npm run build`: `daily-brief.astro` and other pages imported `web/src/lib/client-utils.ts`, but the generic `.gitignore` `lib/` rule had excluded that source file from the merged commit. The deploy job was skipped.
- Autonomous pipeline run [39029658531](https://github.com/asdfhgds/humanity/actions/runs/39029658531) successfully fetched content from 197 RSS articles and created 197 source records; GDELT returned zero articles. Gemini was configured in the workflow. Extraction began on the first of 50 sources and returned a result, but the CLI crashed with `NameError: validate_extraction_result is not defined` before validation, event creation, claim creation, or publication. No event was published or held, and the remaining extraction, state-persistence, and site-build steps were skipped. Tavily was disabled and had no key; this was not the cause of the run failure.
- Corrective local changes: scope the Python `lib/` ignore pattern to `/lib/` so the frontend helper is included; restore event parsing, path, escaping and element helpers; import the already-existing extraction validator into the CLI; and include each event's publication status in the public index. The latter is required by Astro's static event-route filter and prevents published event links from resolving to absent pages. A regression assertion verifies the indexed status is a published status.

- Validation after these changes: `npm run check` passed with 0 errors, warnings, or hints; `GITHUB_ACTIONS=true npm run build` passed and generated 13 pages with the current empty feed; `python -m pytest tests -q` passed (66 tests, 493 existing deprecation warnings); `python -m app.cli --help` passed. The known MapLibre chunk-size warning remains. The production run's mock-free live extraction has not been rerun.
- The fixes were committed as `8918e9c` and pushed to `asdfhgds-fix-internal-links`; commit `05b50e0` documents that the app-native PR operation was blocked by the project's GitHub account link. PR [#2](https://github.com/asdfhgds/humanity/pull/2) was subsequently opened using the authenticated GitHub CLI. It initially reported no checks because no workflow was configured for `pull_request`.
- Added `.github/workflows/pull-request-checks.yml` to run the Python tests and frontend check/build/link audit on PRs targeting `master`, plus `scripts/audit_static_site.py` to detect missing built local links, fragments, and assets. Local verification: 66 Python tests passed; Astro check passed with 0 diagnostics; the GitHub Pages build generated 13 pages with the known MapLibre chunk warning; the static audit found no missing references.
- GitHub Actions run [39105995298](https://github.com/asdfhgds/humanity/actions/runs/39105995298) passed both Python tests and frontend check/build/link audit. Actions emitted non-blocking Node.js 20 runtime deprecation and `ubuntu-latest` migration warnings.
- No workflow was manually triggered and no deployment occurred. The deployment gate remains closed until PR #2's checks pass, the fix is reviewed and merged, and live publication is verified without bypassing evidence requirements.
- Final review found that the autonomous CLI reused the discovery result variable for each extraction response and also referenced the old variable for the extracted summary. This could crash after extraction even after the missing validator import was fixed. The CLI now keeps discovery and extraction results separate; a parameterized regression test verifies that invalid extraction creates and publishes no event, while a valid extraction uses its own summary and preserves the discovery run ID. The full Python suite passes locally (68 tests).
- The follow-up is committed as `a83f492`. GitHub Actions run [39107304890](https://github.com/asdfhgds/humanity/actions/runs/39107304890) passed both PR checks: Python tests and frontend check/build/link audit. GitHub CI validates the build and regression behavior; it does not establish that external live ingestion succeeds. No merge, autonomous pipeline run, or deployment has occurred.

### Production release safety gate (2026-10-18)

- `autonomous-pipeline.yml` now requires repository variable
  `PRODUCTION_RELEASE_ENABLED` to equal `true` before the scheduled or
  manually dispatched production job can run. The workflow token defaults
  to read-only; only the gated production/deploy jobs receive write
  permissions. The `force_publish` input was removed. `ENABLE_AUTOPUBLISH`
  defaults to `false`, and both the main CLI pipeline and `EventPublisher`
  refuse new publication unless the existing setting is explicitly true.
- `deploy-pages.yml` no longer deploys on pushes to `master`. It requires a
  manual dispatch with `confirm_deployment=yes` (default `no`). PR validation
  remains separate, so merging alone does not deploy.
- Added manual-only `live-source-smoke.yml` and
  `python -m app.cli smoke-test`. They use configured real source connectors
  and Gemini, cap extraction attempts, suppress provider/connector output,
  use temporary data and web directories, and never call the publisher. The
  workflow is read-only and has no commit, push, artifact upload, or deploy
  step. The CLI reports aggregate discovery, extraction, validation, and
  publication-eligibility counts. The smoke workflow has not been dispatched;
  live source/Gemini behavior and credential availability remain unverified.
- Added regression tests for workflow gates, no push-triggered deployment,
  absence of force-publish, smoke isolation/output suppression, disabled
  publication, and distinction between discovery and validated events.
  Existing tests cover HELD/REJECTED removal from public artifacts.
- Local checks after the latest code changes: `python -m pytest tests -q`
  passed (**74 passed**, 508 existing deprecation warnings); `npm run check`
  passed with zero diagnostics; the production build generated 13 pages; the
  built-site audit found no missing local links, fragments, or assets; all
  four workflow YAML files parsed; and `git diff --check` passed. The known
  MapLibre bundle-size warning remains. These checks do not establish live
  ingestion behavior.
- PR #2 remains open against `master`. Commit `bf70f4b` is pushed, and both
  PR checks passed in [run 39111389670](https://github.com/asdfhgds/humanity/actions/runs/39111389670):
  Python tests and frontend check/build/link audit. GitHub reported only
  non-blocking Node.js runtime and `ubuntu-latest` migration warnings. No
  production workflow was dispatched, no production data was changed, no
  secrets or thresholds were modified, and the public feed remains empty.
- Remaining release actions: dispatch the isolated smoke workflow with
  configured credentials and inspect its aggregate counts; obtain human
  review before deliberate production enablement or deployment. Do not run
  production ingestion or deploy until smoke results and the release are
  approved.

### Release mandate review (2026-10-18)

- Fresh GitHub inspection found PR #2 open against `master` at `cf66a4d`,
  with both Python and frontend/link-audit checks green. The review confirmed
  the narrow `/lib/` ignore rule, restored frontend utility, extraction
  validator, published-only index generation, HELD/REJECTED filtering, and
  the release-safety regression coverage.
- Repository secret listing confirms a `GEMINI_API_KEY` secret exists;
  GitHub does not expose its value, and it was not read or logged. No
  repository variables are configured, so
  `PRODUCTION_RELEASE_ENABLED == 'true'` evaluates false and production
  pipeline jobs remain skipped. The existing schedules are every five
  minutes and daily at 02:00 UTC; workflow defaults are 1,000 articles,
  100 LLM calls, 50 search calls, score 70, confidence 0.6, and
  auto-publication disabled. Pages deploy is manual-only with explicit
  confirmation.
- Final local review found that smoke output did not distinguish feed
  failures, and RSS page fetches could exceed the configured article budget.
  The isolated path now reports RSS and GDELT event/GKG results and failures
  separately, applies the global `MAX_ARTICLES_PER_RUN` limit across RSS
  feeds, counts invalid extraction responses, checks source-quote provenance,
  and evaluates eligibility using the same event construction, clustering,
  syndication, evidence, and score policy as production. It still uses only
  temporary storage and never calls the publisher. Regression tests cover
  these safeguards.
- Local validation after these changes: **77 Python tests passed**; Astro
  check passed with zero diagnostics; static build produced 13 pages; link
  and asset audit passed; all four workflow files parsed; Python compilation
  and `git diff --check` passed. The existing MapLibre bundle-size advisory
  and legacy datetime deprecation warnings remain.
- These final review fixes are pushed at `962b45f`. Both PR checks passed on
  that exact head in [run 39133892704](https://github.com/asdfhgds/humanity/actions/runs/39133892704),
  and PR #2 is mergeable. No live source/Gemini smoke, production pipeline,
  merge, or deployment has occurred yet. The recurring pipeline must remain
  disabled even if the smoke succeeds.

### Post-merge live verification and current blockers (2026-10-18)

- PR #2 is merged. The resulting `master` merge commit is
  `f623a3f2c36c87975a3113038e0ae64c51d08ff2`. The latest pre-merge Python and
  frontend/link-audit checks passed in
  [run 39134116676](https://github.com/asdfhgds/humanity/actions/runs/39134116676).
- The isolated live-source smoke workflow completed successfully in
  [run 39134727684](https://github.com/asdfhgds/humanity/actions/runs/39134727684)
  using its 6-hour discovery window and 25-extraction-call cap. It discovered
  203 articles, attempted 25 Gemini extractions, had 1 successful extraction
  and 24 failures, validated 1 event, created 6 source-backed claims, and
  found 0 publication-eligible candidates. The source attribution check found
  provenance for all 6 claims. RSS completed 12/12 feeds, with 6 feeds
  reporting errors (203 successful and 6 failed fetch results); GDELT events
  returned 0 records without errors, and GDELT GKG reported 1 error and
  returned 0 articles. These counts are discovery/extraction diagnostics,
  not published stories. The smoke used temporary storage, disabled
  auto-publication, and did not alter production data, commit, push, or deploy.
- As of the live check, the public homepage returned HTTP 200 but was built
  from an older deployment (SHA `13eb00081d61d3f6d62f20cfe4ec78bb0c45de8d`,
  predating the merge). The map endpoint returned an empty FeatureCollection;
  current-code event, recent-event, global-statistics, and country data
  endpoints returned HTTP 404. The repository's tracked public event feeds
  and map are empty, so a code-only build should report an empty feed rather
  than inventing events. No Pages deployment has been run.
- The follow-up diagnostic build completed a second isolated live smoke in
  [run 39137753803](https://github.com/asdfhgds/humanity/actions/runs/39137753803).
  It discovered 202 articles, attempted 25 Gemini extractions, and again
  produced 1 successful extraction, 24 failures, 1 validated event, and 0
  publication-eligible candidates. All 24 Gemini failures were categorized
  as `RuntimeError`; this safe category does not identify whether the cause is
  quota, transport, or provider behavior, and the provider message remains
  intentionally unlogged. GDELT GKG's single error was `HTTPStatusError`;
  GDELT events completed with no errors. RSS completed 12/12 feeds with 6
  feed errors. This confirms external source and Gemini calls were attempted
  but exposed a client lifecycle bug: reusing one Gemini async client across
  repeated `asyncio.run()` calls bound the client to different loops.
- PR #3 fixed that smoke-only lifecycle bug, added safe categorized diagnostics
  and regression coverage, and was merged at
  `98652d744a1168b5991410cdf5f6d0709b960de6`. PR checks passed in
  [run 39140149393](https://github.com/asdfhgds/humanity/actions/runs/39140149393).
  The next isolated run,
  [39140309227](https://github.com/asdfhgds/humanity/actions/runs/39140309227),
  confirmed 12 successful extractions, 13 quota/rate-limited failures, 9
  validated events, and 0 publication-eligible candidates from 203 articles.
- A final default-branch smoke,
  [run 39141724221](https://github.com/asdfhgds/humanity/actions/runs/39141724221),
  used a 10-call cap because the 25-call run hit rate limiting. It discovered
  203 articles, completed 10 Gemini attempts (3 successes, 7 quota/rate-limit
  failures), validated 2 events, and found 0 publication-eligible candidates.
  All 12 claims had source attribution. Both GDELT stages succeeded with no
  errors; RSS completed 12/12 feeds with 6 reporting errors. It was an
  isolated smoke only: no event publication, production data write, commit,
  push, or deploy occurred in the run.
- A code-only Pages deployment was then explicitly confirmed and completed
  successfully from `master` at `98652d744a1168b5991410cdf5f6d0709b960de6`:
  [run 39142351256](https://github.com/asdfhgds/humanity/actions/runs/39142351256).
  The live site at https://asdfhgds.github.io/humanity/ and checked
  `/data/events/all.json`, `/data/events/recent.json`, `/data/stats/global.json`,
  `/data/countries/all.json`, `/data/map/events.geojson`, `/image-credits/`,
  and `/events/` routes all returned HTTP 200 after deployment. The public
  event index is `[]`, global event and casualty counts remain zero, and the
  map is empty; there are no fabricated stories. The latest static build
  contains 13 pages and passed the local link/asset audit.
- The `GEMINI_API_KEY` secret exists by name; its value was never accessed.
  No repository variables are configured, so `PRODUCTION_RELEASE_ENABLED`
  remains false. Auto-publication is still false. No production ingestion or
  recurring production job ran after the PR merges, and no publication
  threshold was changed. Production publishing remains blocked by quota/rate
  limiting and the absence of eligible candidates; do not activate the
  scheduled pipeline.

### Phase 3 extraction reliability and evidence diagnostics (2026-10-18)

- Replaced the deprecated `google-generativeai` dependency with Google's
  supported `google-genai` SDK and its async `client.aio.models.generate_content`
  interface. Structured JSON and the existing Pydantic validation remain in
  place.
- Gemini extraction now records sanitized categories on each attempt, stops
  the batch on quota exhaustion, retries transient rate limits and provider
  server errors at most twice per article, honors provider retry delays up to
  60 seconds, and charges every retry against the configured API-call budget.
  It never retries authentication, permission, quota, or unclassified provider
  failures. No live Gemini calls were made for this work.
- Both production CLI extraction and isolated smoke now use one async batch,
  persist matching reports through conservative event identity resolution,
  and attach claims to the canonical event while retaining each source ID.
  Claims are retained only when their quoted evidence appears in that source's
  article text. The heuristic post-hoc cluster command no longer deletes events
  or strands claims; uncertain or distinct incidents remain separate.
- Smoke output now reports invalid/non-event results, insufficient independent
  sources, suspected syndication, evidence-assessment failures and
  contradictions, publication-score/confidence holds, and eligible candidates
  intentionally retained as unpublished. Existing policy thresholds are
  unchanged.
- Historical smoke evidence remains aggregate-only: the isolated store was
  deleted and the recorded run retained no per-event source assessments.
  Therefore the exact reason for each of the two previously validated events
  cannot be verified from that run. Under the unchanged assessor, a one-source
  event with an unknown source classification is held for
  `insufficient_independent_sources`; the prior aggregate report did not prove
  how many sources were attached to each event. A later smoke must preserve
  and report the per-event aggregate reasons to confirm this against real
  extraction results.
- Verification on the focused local branch: `pytest -q` passed (93 tests),
  `npm run check` passed (30 Astro files, zero diagnostics), `npm run build`
  passed (13 static pages), all four GitHub Actions YAML files parsed, and
  `python scripts/audit_static_site.py web/dist` passed with no missing local
  links, fragments, or assets. The build emitted only its existing large-chunk
  advisory. CI and code review have not yet run on this branch.
- Production publishing, scheduled runs, and deployment remain disabled. No
  smoke workflow was dispatched and no live source/API quota was spent. This
  local work is not yet pushed or submitted as a PR; do not treat it as
  release-ready until remote CI/review pass and a later isolated smoke verifies
  real independent evidence.

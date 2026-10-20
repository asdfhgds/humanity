# System architecture and data flow

## Current implementation

HUMANITY is a Python ingestion and publishing pipeline with an Astro static
frontend. The implementation is organized around configured sources, source
records, canonical event candidates, claims, evidence assessment, publication
policy, and generated JSON/HTML. Local operational state is stored in JSON.

1. Configured source connectors and discovery adapters retrieve source
   metadata and, where their connector does so, article content.
2. Source records are normalized and deduplicated independently of event
   identity. Repeated ingestion updates an existing canonical source record.
3. The Gemini adapter can produce schema-validated candidate event data.
   Provider errors and exhausted credentials can defer extraction; provider
   success is not evidence verification.
4. Event matching is conservative: ambiguous records are kept separate,
   suspected syndicated reports do not count as independent corroboration, and
   claim citations remain attached to originating sources.
5. Evidence assessment and scoring feed the publication policy. HELD and
   REJECTED records are excluded from public artifacts. Automatic publication
   is disabled by default.
6. The publisher writes public feeds, pages, and map data; Astro builds a
   static site from those generated files.

The UCDP GED importer is a distinct structured-dataset path. An explicit
`python -m app.cli import-ucdp` command downloads a bounded, versioned public CSV
archive, validates its schema and records, and stores selected source-native
observations and import health in separate JSON collections. It never creates
`Event`, `Source`, or `Claim` records and does not enter discovery, extraction,
evidence assessment, scoring, or publication. The generated
`data/research/ucdp-ged.json` index and versioned year partitions power
`/research`; the browser fetches only one year at a time. These historical dataset
observations are not live alerts, independent corroboration, or HUMANITY event
candidates. Re-imports update versioned stable record IDs rather than
duplicating rows. Last successful retrieval metadata is preserved separately
from a failed refresh. The importer excludes source article text and publisher
metadata, reports missing/invalid dates and coordinates without inventing
values, and applies a hard archive/CSV/row bound.

## Operational boundaries

GitHub Actions provides workflow execution and repository-backed state for the
configured pipeline. Workflows are not guarantees of uninterrupted scheduling.
The static frontend reads generated, published data; a browser refresh cannot
create new source records. Production ingestion, recurring execution, automatic
publication, and deployment must remain explicitly gated until their operational
readiness has been verified.

The `health` CLI command reads persisted ingestion-run outcomes, per-source
status, and the separate UCDP import's last attempt and last success; it
performs no probe and reports no synthetic "healthy" result. The
events page can keep saved filters and a review snapshot in browser-local
storage, compare changes in public records/source evidence, and create a
source-linked citation brief. This state is device-local, can be cleared by the
browser, is not synchronized, and does not cause ingestion.

The latest verified public event feed in the project status is empty. A blank
feed means there are currently no records approved for public display, not that
no relevant events occurred or that source monitoring is complete.

## External dependencies and failure domains

The pipeline depends on upstream feeds/APIs and Gemini availability and quota.
The frontend depends on its static host and OpenStreetMap tile services for the
interactive map. These providers receive requests from the relevant runtime or
visitor browser and have their own terms and operational logs. No first-party
account system or paid access flow is currently part of this project.

## Known architecture limits

- Source coverage and language coverage are incomplete and not measured as
  global completeness.
- Some connectors persist fetched text for extraction; its source-specific
  retention basis still needs review.
- The event index is not an exhaustive incident database.
- There is no user authentication, shared/synchronized saved workspace,
  guaranteed update cadence, or public API service-level commitment.
  Browser-local monitoring is a single-device prototype, not a shared
  workspace.
- The JSON datastore and repository-backed workflow state make persistence
  dependent on workflow concurrency and commit/restore behavior; larger
  workloads (including large dataset releases) would need transactional or
  columnar storage and background workers before parallel ingestion,
  user-triggered updates, or scalable per-record filtering. The current
  Research Explorer downloads its generated dataset feed into each visitor's
  browser, so the feed's file size and memory cost are a current scaling limit.
- No enquiry form is exposed: a secure delivery destination, access policy,
  consent/retention terms, and an operational response owner are not configured.
- Deployment and workflow changes require explicit authorization and release
  checks; passing code tests does not establish real-provider reliability.

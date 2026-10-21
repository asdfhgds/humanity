# HUMANITY

> **The world, measured through human events.**

A developing, source-linked public record of selected events affecting people. HUMANITY is designed to make provenance and uncertainty visible; it is not a complete or continuously current account of world events.

## 🎯 Vision

The project explores whether a structured public record can help readers:

> follow selected reporting geographically and chronologically, and inspect the sources behind reported claims.

## ✨ Features

- **Published-record map and browse pages** - Show only records included in the generated public dataset; an empty feed is possible.
- **Searchable event index** - Search and filter public records; filters can be shared by URL.
- **Device-local monitoring** - Save searches, compare record and source-evidence changes on later visits, and download a source-linked citation brief. This data stays in the current browser and is not synced.
- **Source-linked event pages** - Inspect source metadata and attributed claim excerpts where available.
- **Structured-data Research Explorer** - Filter source-native UCDP GED historical observations by year, place, date, violence category, and fatality estimate; inspect source uncertainty, map valid coordinates, and export attributed CSV/JSON. This is a separate dataset view, not the published-event feed. See the [feed and export contract](docs/RESEARCH_EXPLORER.md).
- **HUMANITY Research reports** - Read a small set of source-linked, reproducible analyses computed from the real UCDP GED 26.1 1989, 2024, and 2025 partitions. Counts describe dataset records, not casualty totals or current alerts; see [editorial report boundaries](docs/EDITORIAL_REPORTS.md).
- **Transparency pages** - Read the methodology, configured source list, rights status, privacy draft, and use terms draft.
- **Evidence safeguards** - Suspected syndication does not count as independent corroboration, and held or rejected events do not enter public artifacts.

This is an early-stage research prototype. The historical research reports are generated deterministically from the included UCDP release during the static build; no generative AI is used for their calculations or prose. They are not current-event reporting. Current-event pages continue to use the existing extraction, provenance, evidence, and publication safeguards; the public current-event feed may be empty. The product does not provide complete global coverage, guaranteed live ingestion, synchronized accounts, notifications, or a paid research service. A browser refresh reloads published data; it does not run ingestion.

## 🏗️ Architecture

```
humanity/
├── app/                    # Python backend
│   ├── discovery/          # GDELT, RSS, dataset ingestion
│   ├── extraction/         # Gemini LLM structured extraction
│   ├── clustering/         # Multi-source event deduplication
│   ├── scoring/            # Interestingness engine
│   ├── publishing/         # Static page generation
│   └── cli/                # Command-line interface
├── web/                    # Astro frontend
│   ├── src/pages/          # Routes (/, /map, /events, /event/...)
│   ├── src/components/     # MapGlobe, LiveCounters, EventCard, etc.
│   └── public/data/        # Generated JSON for maps, timelines, stats
├── data/                   # Local JSON datastore (MVP)
├── config/                 # YAML configuration
│   ├── sources.yaml        # Connector/source configuration
│   ├── source_rights.yaml  # Per-source rights review register
│   ├── topics.yaml         # Event taxonomy
│   ├── countries.yaml      # Country metadata
│   └── scoring.yaml        # Scoring weights
└── .github/workflows/      # Autonomous pipeline
docs/                       # Product, architecture, rights, privacy, terms
```

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Node.js 20+
- Git

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/humanity.git
cd humanity

# Python setup
pip install -e .
pip install -e ".[dev]"

# Frontend setup
cd web
npm ci
cd ..

# Copy environment template
cp .env.example .env
# Edit .env only if you will run a provider-backed local pipeline
```

### Optional provider credentials

1. **Gemini API key(s)** (required only for provider-backed extraction): Get from [Google AI Studio](https://makersuite.google.com/app/apikey)
2. **Tavily API Key** (optional): Get from [Tavily](https://tavily.com)
3. **Cloudflare** (optional): For deployment

For local provider-backed extraction, add to `.env`:

```env
# Store real credentials locally and never commit .env
GEMINI_API_KEYS="one-key-per-line"
GEMINI_MODEL=gemini-flash-latest
```

For GitHub Actions, store the pool as the repository Actions secret
`GEMINI_API_KEYS`, with one key per line. Keep its existing line order and append
new credentials; blank lines and duplicate values are ignored. The legacy
`GEMINI_API_KEY` secret remains supported and is appended after the pool for
backward compatibility. Never put credentials in workflow YAML, repository
variables, source files, issues, pull requests, or logs. Runtime rotation state
stores hashed slot identifiers, cooldowns, sanitized error categories, and the
last-used slot only; adding keys appends slots, while removed keys' state is
ignored.

Live ingestion and production publication remain gated; do not treat a local
build or successful extraction as evidence that a public event qualifies.

### Run Locally

```bash
# Terminal 1: Run autonomous pipeline
python -m app.cli run

# Terminal 2: Start frontend dev server
cd web && npm run dev
```

Visit `http://localhost:4321` to see the site.

## 📊 Pipeline Commands

```bash
# Pipeline command (may contact configured external sources/providers; production
# release and publication remain gated by application settings)
python -m app.cli run

# Individual stages
python -m app.cli discover --hours 6
python -m app.cli extract --limit 50
python -m app.cli cluster
python -m app.cli score
python -m app.cli publish

# Explicitly import the public UCDP GED dataset into separate dataset storage
python -m app.cli import-ucdp
# Inspect persisted ingestion and structured-dataset freshness (read-only)
python -m app.cli health

# Database stats
python -m app.cli stats

# Build frontend
cd web && npm run build
```

## 🔧 Configuration

All configuration is in `config/`:

- **sources.yaml** - News sources, datasets, APIs with priorities and intervals
- **topics.yaml** - Event types, severity scale, claim types
- **countries.yaml** - All 195+ countries with regions, conflict zones
- **scoring.yaml** - Weights for interestingness engine, publishing thresholds

## 📈 Data Model

Core entities:

- **Event** - Central object with type, location, time, severity, claims
- **Source** - Publisher, URL, metadata, reliability
- **Claim** - Extracted fact: subject, predicate, value, source quote, confidence
- **DatasetRecord** - Versioned, source-native structured observation, stored separately from events, evidence, and publication
- **Location** - Cached geocoding with precision levels
- **Country/Conflict** - Aggregation entities

## 🤖 AI Usage

- Gemini is used for schema-validated candidate extraction when configured and available.
- Model output is not an independent source and does not by itself verify a claim or qualify an event for publication.
- Provider errors, quota limits, or unavailable credentials can defer extraction.
- The application must not invent facts, fill missing numbers, or create citations.

## 🌍 Deployment

The Astro frontend is a static site. Browser refreshes can reload the latest
deployed feeds, but do not run source ingestion. Workflow schedules are
best-effort and production ingestion, recurring execution, deployment, and
automatic publication are gated. See [PROJECT_STATUS.md](PROJECT_STATUS.md)
before assuming any live capability is active.

## 📝 Event Page Structure

Eligible published event records can have a static URL such as
`/event/2026/10/08/event-slug`. URLs are generated only for records included in
the public event index.

```
Title | Event Type | Location | Date | Confidence
Summary and reported impact, where available
Map location, where available
Source links and attributed claims, where available
Uncertainty and publication status
```

## 🔬 Methodology Highlights

- Preserve attribution and avoid treating extraction as verification.
- Detect suspected copied or syndicated reporting before counting independent sources.
- Hold candidates when evidence or source independence is insufficient.
- Keep reported figures and conflicts contextualized; do not create unsupported aggregate totals.
- Review the current [methodology](web/src/pages/methodology.astro) and [source-rights status](docs/SOURCE_RIGHTS.md).

## Current limitations and next work

- Real-provider reliability and persistent live ingestion need continued verification.
- The CLI does not yet route every extracted candidate through the complete event-resolution handoff.
- News publishers, feeds, and ReliefWeb are not cleared for commercial reuse or redistribution; source-specific storage and display terms also remain under review.
- GDELT dataset terms permit commercial use and redistribution with attribution, but do not grant rights to linked publisher articles or media. UCDP GED 26.1 is available through a public CC BY 4.0 CSV download; the explicit bounded importer and separate historical-data Research Explorer omit publisher article text and remain separate from news publication. The UCDP API is distinct and still requires a token.
- The current JSON store and generated Research Explorer feed load full filtered dataset records in the browser; this is an initial bounded research slice, not a scalable query API or a live event-alert service.
- Coverage completeness is not measured, and a blank public feed must not be read as evidence that no events occurred.
- Potential research-team services and pricing are unvalidated hypotheses only; there are no paid plans or checkout.
- Accounts, alerts, paid APIs, and dynamic database services are not currently offered.

See [the product hypotheses](docs/PRODUCT_STRATEGY.md), [architecture](docs/ARCHITECTURE.md), [privacy draft](docs/PRIVACY.md), and [terms draft](docs/TERMS.md).

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests: `pytest` and `cd web && npm run check`
5. Submit a PR

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

## 🙏 Acknowledgments

- **GDELT** - Global event discovery
- **ReliefWeb/UN OCHA** - Humanitarian data
- **UCDP** - Conflict data
- **MapLibre** - Open source mapping
- **Astro** - Static site framework
- **Gemini** - Free tier LLM

---

**HUMANITY** — See what is happening to people around the world.

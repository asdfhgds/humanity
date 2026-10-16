# HUMANITY

> **The world, measured through human events.**

An autonomous global human events intelligence platform that continuously discovers, verifies, structures, analyzes, and publishes significant human events happening around the world.

## 🎯 Vision

Not a traditional news aggregator. A **structured global event intelligence platform** whose purpose is:

> Understand what is happening to people around the world right now, show it geographically and chronologically, and make every important claim traceable to its sources.

## ✨ Features

- **Live Global Map** - Interactive MapLibre globe showing protests, conflicts, attacks, casualties, displacement, disasters, repression, and humanitarian events
- **Event Database** - Every event has a permanent URL with full source transparency
- **Human Cost Pages** - Visual metrics for deaths, injuries, displacement with full provenance
- **Country & Conflict Pages** - Dedicated pages with timelines, statistics, and context
- **Daily Briefings** - Automated "What changed in the last 24 hours?" summaries
- **Overlooked Events** - Significant events receiving little international coverage
- **Share Cards** - Auto-generated social media cards for every event
- **Full Methodology Transparency** - Every number traced to its source

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
│   ├── sources.yaml        # Source registry
│   ├── topics.yaml         # Event taxonomy
│   ├── countries.yaml      # Country metadata
│   └── scoring.yaml        # Scoring weights
└── .github/workflows/      # Autonomous pipeline
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
npm install
cd ..

# Copy environment template
cp .env.example .env
# Edit .env with your API keys
```

### Required API Keys

1. **Gemini API Key** (required): Get from [Google AI Studio](https://makersuite.google.com/app/apikey)
2. **Tavily API Key** (optional): Get from [Tavily](https://tavily.com)
3. **Cloudflare** (optional): For deployment

Add to `.env`:
```env
GEMINI_API_KEY=your_gemini_key
GEMINI_MODEL=gemini-1.5-flash
TAVILY_API_KEY=your_tavily_key  # optional
```

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
# Full autonomous pipeline (discover → extract → cluster → score → publish)
python -m app.cli run

# Individual stages
python -m app.cli discover --hours 6
python -m app.cli extract --limit 50
python -m app.cli cluster
python -m app.cli score
python -m app.cli publish

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
- **Location** - Cached geocoding with precision levels
- **Country/Conflict** - Aggregation entities

## 🤖 AI Usage

- **Gemini 1.5 Flash** (free tier): Classification, extraction, clustering assistance, scoring
- **Structured JSON output only** - Validated with Pydantic schemas
- **Two-model strategy**: Cheap model for high-volume, strong model for synthesis
- **Never**: Invent facts, fill missing numbers, create fake citations

## 🌍 Deployment

### Free Tier (MVP)
```bash
# GitHub Actions runs pipeline every 30 minutes
# Generates static site to web/dist/
# Deploys to Cloudflare Pages (free)
```

### Production (Phase 2+)
- Cloudflare D1 for database
- Cloudflare Workers for API
- Custom domain

## 📝 Event Page Structure

Every event gets a permanent URL: `/event/2026/10/08/event-slug`

```
Title | Event Type | Location | Date | Severity | Status | Confidence
Summary
Map
Key Facts (deaths, injuries, arrests, displaced, participants)
What Is Known (corroborated claims)
What Is Uncertain (reported/disputed claims)
Sources (with links, dates, types)
Claims/Evidence (structured with quotes)
Related Events / Conflict / Country
Last Updated | Confidence Score
```

## 🔬 Methodology Highlights

- **No false precision**: Ranges (23–31) not points (27)
- **Source transparency**: Every claim → source quote → source URL
- **Uncertainty taxonomy**: reported / corroborated / disputed / estimated / unknown
- **Revision history**: Changing figures stored, never overwritten
- **Contradiction engine**: Conflicting reports flagged explicitly

## 🛣️ Roadmap

- [x] Phase 1: MVP - Astro site, GDELT discovery, Gemini extraction, clustering, publishing
- [ ] Phase 2: Multi-source ingestion, country/conflict pages, timelines
- [ ] Phase 3: Casualty series, displacement series, human-cost pages, revision history
- [ ] Phase 4: Daily brief, overlooked engine, share cards, social content
- [ ] Phase 5: ACLED/UCDP integration, more local sources, language-aware ingestion
- [ ] Phase 6: D1 database, dynamic API, user accounts, alerts, public API

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
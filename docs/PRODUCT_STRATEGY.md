# Product strategy hypotheses

## Product in one sentence

HUMANITY is an open, source-linked public record of selected events affecting
people, designed to keep uncertainty and provenance visible rather than imply
complete global coverage.

## Initial audiences and jobs to be done

These are hypotheses for interviews, not validated customer segments:

- **Researchers and analysts** need a faster way to find source-linked event
  records and inspect what is reported, contested, and not known.
- **Editors and civil-society organizations** need a transparent monitoring
  view that can be checked against original reporting.
- **Educators and the public** need a comprehensible map and timeline without
  treating reported figures as settled facts.

The current public site supports browsing published records, event search and
filters, source links, a map, and methodology/transparency pages. The event
index now also supports browser-local saved searches, comparisons against a
review snapshot, and a source-linked citation brief. This is device-local only;
it is not an account, synchronized workspace, alerting service, or ingestion
trigger. The separate Research Explorer presents historical UCDP GED
source-native observations with version/coverage/freshness, source uncertainty,
filters, map, and attributed CSV/JSON exports. It is not the live news feed,
independent corroboration, or publication path; the empty published feed and
uneven source coverage remain material product limitations.

## Commercial packaging hypotheses

The public site should remain free and open. Possible paid research support is
only a discovery hypothesis; there is no checkout, subscription, account,
contract, service-level guarantee, or paid API.

| Hypothesis | Possible value | Indicative test range | Preconditions |
|---|---|---:|---|
| Team monitoring workspace | Saved topics, shared review lists, evidence-change alerts | $25–$100 per seat/month | User research, reliable ingestion, privacy and rights review |
| Research brief for organizations | Source-linked periodic brief and export | $250–$1,000 per organization/month | Editorial review capacity, licensed-source assessment, delivery support |
| Research data access | Documented export/API for eligible records | To be tested; no price set | Stable schema, redistribution rights, access controls, maintenance budget |

All amounts are unvalidated conversation prompts, not prices, offers, quotes,
revenue forecasts, or a commitment to build these services. No customer demand,
conversion, or willingness-to-pay has been demonstrated. Do not add a paid
feature until interviews, source-rights review, operational cost modeling, and
support obligations justify it.

## First pilot proposition to validate

**Customer problem hypothesis:** analysts spend time reconciling fragmented
human-impact reporting and need a traceable way to revisit relevant records,
sources, and changes.

**Smallest credible pilot:** a HUMANITY analyst prepares a periodic,
source-linked monitoring brief for one mutually agreed topic/geography and
review interval. The current product supplies searchable published records,
source/evidence inspection, device-local saved filters, and citation export;
it does not yet deliver a team workspace, alerts, private feeds, or a guaranteed
ingestion cadence. A human would have to review each brief before delivery.

**Source and service boundaries:** do not redistribute article text, images, or
claims beyond the rights granted by each source. GDELT dataset terms allow
commercial use with attribution but do not cover linked articles. The public
UCDP GED download is CC BY 4.0 with attribution; its separate API remains
token-gated. The Research Explorer uses selected dataset fields and excludes
publisher article text and metadata. Other listed sources remain not cleared
for commercial reuse/redistribution. Never describe coverage as comprehensive
or records as verified solely because extraction succeeded.

**Dataset workflow and scale:** the explicit UCDP import downloads and validates
a bounded release, stores versioned dataset rows outside the news event model,
and emits a source-attributed public feed for browser filtering and CSV/JSON
export. It does not guarantee continuous updates, provide per-filter server
queries, or produce event alerts. Full-release JSON download and client-side
filtering are adequate only for an initial bounded dataset slice; a larger
workload needs measured size/performance and likely columnar or transactional
storage with query endpoints before customer-specific access is offered.

**Delivery cost and success:** no measured per-brief labor, provider-call cost,
or infrastructure cost is available, so the pilot has no defensible price yet.
Before offering it, measure preparation/review time and variable provider cost.
Evaluate source-attribution completeness, evidence holds/corrections, update
freshness, analyst task completion, and delivery effort. There is no validated
customer interest or willingness-to-pay.

**Enquiry integration blocker:** no contact form is shown because no approved
private submission destination or responsible response owner is configured.
Before accepting prospective-customer information, configure a secure,
access-controlled destination, disclosure/consent wording, retention/deletion
rules, and a monitored response path; do not collect enquiries in a public
repository or generated site artifacts.

## Success measures

Measure source-attributed records that pass the existing evidence policy,
provenance completeness, correction turnaround, feed freshness, user task
completion, and source diversity. Keep extraction volume and page views
separate from evidence quality. Do not optimize toward publication count or
engagement at the expense of independence and uncertainty standards.

# Editorial reports and research pilot boundaries

## Public content types

- **Current-event reports** are the existing published event records at
  `/events` and their event pages. They can enter the public feed only through
  the configured source, extraction, clustering, provenance, evidence, and
  publication workflow. Source-specific rights remain conservative. The
  current public feed may be empty; that is not a claim that no events
  occurred.
- **Historical research reports** are the three `/reports/` articles generated
  from the separately imported UCDP GED 26.1 data. They compare source-native
  record counts and distributions for 1989, 2024, and 2025. A build validates
  that the selected partitions are present, parseable, and agree with the
  versioned index. Missing or mismatched data stops article generation.
- **HUMANITY Daily** is for a published current-event brief only. It is not
  assembled from historical observations. Research articles may be linked on
  that page, visibly separated and labeled as historical.

## Method and rights

The article calculations are deterministic code over the public year
partitions: row counts by UCDP violence category, country, and year. They do
not sum best-estimate fatalities. The prose uses fixed editorial templates;
no generative model writes or calculates these three reports. Every article
shows the dataset version, coverage, retrieval date, attribution, license,
codebook, and links to matching filtered records in the Research Explorer.
These observations are neither live alerts nor independently corroborated
HUMANITY news reports.

UCDP GED 26.1 is attributed under CC BY 4.0. The imported records omit source
article text, publisher metadata, and images; no editorial images are needed
or included in the analysis pages. The dataset license does not grant rights
to the linked publishers' content.

The current-event pipeline remains separate and is not enabled by this work.
Gemini extraction success is not evidence verification. If provider output is
unavailable or facts fail the existing validation and publication
requirements, no current-event report is created from it. This milestone does
not make another Gemini request, change model settings, alter thresholds, or
turn on recurring ingestion or automatic publication. Publisher/feed storage,
display, commercial-use, and redistribution rights are still pending or
uncleared unless the source-rights register says otherwise.

## Initial professional workflow to validate

The public Research Explorer and articles already support three bounded jobs:

1. Compare recorded event counts by violence type across adjacent annual
   partitions, then inspect the matching source-native rows.
2. Find the countries with the largest number of records in a selected year
   and create a reproducible, attributed export from the filtered data.
3. Compare two historical year partitions without treating endpoint counts as
   a continuous trend, fatality total, or live change.

These public static tools do not provide tailored monitoring, user accounts,
shared workspaces, alerts, private-source ingestion, guaranteed refreshes, or
human-reviewed bespoke briefs. A real research-brief pilot would still require
a human analyst to agree a question, verify the interpretation and rights,
prepare the extract, and review the deliverable. Preparation and review time
have not been measured; no price, customer demand, or willingness to pay has
been validated.

The smallest next validation step is product discovery using the published
reports: ask a small number of researchers or editors to complete one of the
three tasks using the public pages, then observe task completion, time,
attribution needs, and remaining manual work. Do not request personal data or
offer paid service until a secure intake destination, privacy notice,
retention/deletion procedure, response owner, cost model, and source-rights
review exist. No such enquiry flow is currently configured.

Before building accounts or billing, repeat the same analysis manually for a
single user-selected research question, measure analyst hours and compute
cost, and seek explicit permission and willingness-to-pay feedback. This is
validation work, not an available paid product.

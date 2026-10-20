# Research Explorer data contract

The `/research` page displays the Uppsala Conflict Data Program (UCDP)
Georeferenced Event Dataset (GED) as source-native historical observations. It
does not create or update HUMANITY `Event`, `Source`, or `Claim` objects and
cannot affect event evidence assessment or publication.

## Import and source boundaries

Run `python -m app.cli import-ucdp` explicitly to download the public
[GED 26.1 CSV archive](https://ucdp.uu.se/downloads/ged/ged261-csv.zip). The
importer applies a 50 MB compressed-download bound, a 350 MB CSV bound, a 30:1
archive expansion-ratio ceiling, a 500,000-row ceiling, a strict 30-second
request timeout, and at most two requests. A `Retry-After` longer than the
bounded five-second wait is honored by stopping rather than retrying early.
Failed attempts leave prior successful records and freshness intact, and store
only sanitized error categories. Operational partitions are kept under the
configured data directory, separate from generated public files. Import state
tracks cumulative failures and the latest failure timestamp/category even after
a later successful refresh.

Only selected event-level fields are retained: GED record IDs/version/year,
dates, country/region/place, administrative areas, source coordinates,
violence/conflict labels, fatality estimates and ranges, event/date/location
precision and clarity codes, and a usable non-negative number-of-sources value.
Publisher article text, headlines, source URLs, office, dates, and other source
metadata are excluded. Missing/invalid dates and coordinate pairs remain
missing; no date or location is imputed. The release uses a negative marker in
some `number_of_sources` values; those values are represented as unavailable,
not as negative source counts, and are included in the import quality count.

UCDP states its datasets are CC BY 4.0 and requires citing the relevant
publication. The index links to the [UCDP downloads center](https://ucdp.uu.se/downloads/),
[release codebook](https://ucdp.uu.se/downloads/ged/ged261.pdf), and
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Public CSV download
access is separate from the token-gated UCDP API. This operational rights review
is not legal advice or blanket clearance of linked source material.

## Public feed and exports

The small index is `web/public/data/research/ucdp-ged.json`. It reports the
release version, coverage, retrieval time, attempt/success state, record
quality counts, and record counts by year. Individual
`web/public/data/research/ucdp-ged/years/<year>.json` partitions hold that
year's records and stable release attribution/license/source metadata. The
small index is authoritative for current retrieval time and stale/failure
status; it is intentionally not duplicated into every large year partition.
The browser requests the index and one selected year at a time; changing the
year replaces the loaded partition. The explorer defaults to the latest
available year and never downloads all historical rows at once.

Internal record IDs are based on UCDP's source-native `relid`, not the dataset
release version. A new release therefore updates the matching record in place
when that identifier is retained by UCDP.

CSV columns are stable and ordered as follows:

```text
source_record_id,dataset_version,event_year,date_start,date_end,country,region,location,admin1,admin2,violence_type,conflict_name,deaths_a,deaths_b,deaths_civilians,deaths_unknown,deaths_best,deaths_low,deaths_high,date_precision,location_precision,event_clarity,number_of_sources,latitude,longitude
```

JSON exports include the release, selected year, retrieval time, license,
attribution, field order, and filtered records. Both exports include UCDP's
record identifier and dataset version; neither includes article text or
publisher source metadata. Filtering, map display, and downloads happen in the
browser and do not trigger ingestion.

The overview summarizes only the currently filtered records. It counts records
and reports how many include a best-estimate fatality value; it does not sum
fatality estimates across events. Country and violence-type summary links apply
that filter and point to the resulting underlying records. The year-over-year
view compares record counts for the selected calendar year and the immediately
preceding calendar year, using the same GED release index. It is a descriptive
comparison of source-record counts, not a casualty comparison, rate, current
alert, or measure of conflict severity. If the adjacent prior-year partition
is absent, the comparison is not shown. Year links open the corresponding
complete year partition.

## Isolated retrieval verification

One isolated public retrieval of UCDP GED 26.1 succeeded on 2026-10-20 in
temporary storage that was deleted after the check. It read 417,968 rows with
coverage 1989-01-01 through 2025-12-31, no missing/invalid dates or coordinates,
and 97,496 source-count values unavailable under the negative marker convention.
The importer produced no HUMANITY news events. An initial full JSON rendering
measured 275 MB, so the public representation was changed to a small year index
and per-year partitions. A subsequent isolated partition-generation check
measured a 2,517-byte index and 275,192,599 total partition bytes; the largest
year partition was 20,150,769 bytes (2024), with a median of 4,913,230 bytes.
The later Phase 3.1 production snapshot measured 278,579,190 bytes across the
index and 37 year partitions (uncompressed). In a local populated-browser
check, the smallest partition (1989, 1,735,599 bytes) loaded in 296 ms total
(26 ms request); the largest (2024, 20,414,564 bytes) loaded in 918 ms total
(487 ms request). These timings are local development-server measurements and
are not a guarantee of public-network performance.

This dataset is historical and versioned, not a live alert stream. A UCDP row is
not an independent source corroborating a HUMANITY news event and is never
automatically promoted to the event feed.

## Public snapshot operations

The Phase 3.1 public snapshot was imported with a unique temporary `DATA_DIR`.
After validating the result, only the public index and year partitions were
retained for the static-site release; operational record partitions and import
state were not committed. The one-time import state is therefore not available
to the next run. For future refreshes, use an ignored persistent `DATA_DIR`
outside the published static feed, run the bounded importer, and publish the
generated public files only after a successful complete import and count/rights
validation. Never replace a known-good public snapshot with partial output.

Year partition files contain source data and stable release metadata, not the
per-run retrieval timestamp, so identical re-imports do not rewrite all large
files merely because the index retrieval time advances. Operational and public
partition writers also leave byte-identical partition files untouched. The
index alone reflects refreshed retrieval/failure status.

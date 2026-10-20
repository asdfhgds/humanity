# Source rights and reuse register

The maintained connector inventory is [`config/sources.yaml`](../config/sources.yaml);
the companion [`config/source_rights.yaml`](../config/source_rights.yaml) records
each configured source and its review status. Tests require the two inventories
to stay aligned. A listed or enabled source is not an endorsement, a verification
of every report, or a permission to reuse its content.

## Current operational handling

Some connectors read feed or API metadata and fetch article-page text for
candidate extraction. Retrieved source records are persisted in the project's
JSON data store; the `fetch_full_content` flag is not a complete rights or
retention control. This processing and storage path has not been cleared for
every publisher. The registry's default is therefore conservative: terms,
retention, display, commercial use, and redistribution are not cleared unless
a source-specific entry says otherwise.

Two dataset entries have a narrow, documented license basis:

- **GDELT GKG and Events datasets**: GDELT's published terms allow unlimited
  academic, commercial, and governmental use and redistribution of GDELT
  datasets, with a citation to and link to the GDELT Project. This does not
  license the linked publisher's article text, images, or other media; those
  remain outside this dataset review.
- **UCDP GED dataset**: UCDP identifies the dataset as CC BY 4.0 and permits
  redistribution with attribution to the relevant UCDP publication. The
  public [GED 26.1 CSV archive](https://ucdp.uu.se/downloads/ged/ged261-csv.zip)
  and [codebook](https://ucdp.uu.se/downloads/ged/ged261.pdf) are used by a
  separate bounded importer. The versioned UCDP API remains token-gated and is
  not used. The importer displays selected event-level fields, dates,
  coordinates, uncertainty and fatality ranges, while discarding all publisher
  article text and source metadata. Public download access is distinct from API
  access approval; the review is an operational record, not legal advice.

Attribution does not imply endorsement. The downloadable browser-local research
brief contains published event labels, dates, locations, and source links, not
article bodies or claim quotations; linked sources' terms still govern any
subsequent reuse. The export is a citation aid, not a commercial redistribution
license.

## Review required before commercial use

For every connector, confirm source provenance and terms, API/feed terms,
robots and rate-limit obligations, content acquisition scope, storage and
retention permission, quotation and display permission, attribution format,
commercial-use permission, redistribution restrictions, and any applicable
privacy obligations. Review terms again when a provider changes them. Disable a
connector if its access or storage basis cannot be established. In particular,
the reviewed GDELT dataset terms do not clear the news feeds or articles that
GDELT links to. Legal review is still required; this register is operational
documentation, not legal advice.

The current source-status labels are deliberately conservative:

- **Pending**: no source-specific terms review has been recorded; reuse is not
  cleared.
- **Not cleared**: do not treat the content as licensed for commercial reuse or
  redistribution.
- **Reviewed**: the listed terms support only the source-specific scope and
  conditions recorded in that entry; this is not a blanket legal clearance.

News publishers, feeds, and ReliefWeb remain pending/not cleared. The UCDP
dataset download is reviewed with attribution; API access remains token-gated.
GDELT's dataset use is reviewed with attribution;
linked article content remains excluded.

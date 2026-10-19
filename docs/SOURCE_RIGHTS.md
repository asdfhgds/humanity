# Source rights and reuse register

The maintained connector inventory is [`config/sources.yaml`](../config/sources.yaml);
the companion [`config/source_rights.yaml`](../config/source_rights.yaml) records
each configured source and its review status. Tests require the two inventories
to stay aligned. A listed or enabled source is not an endorsement, a verification
of every report, or a permission to reuse its content.

## Current operational handling

Connectors may read feed or API metadata and may fetch article-page text for
candidate extraction. Retrieved source records are persisted in the project's
JSON data store for pipeline processing. Published event pages may display
source titles, links, metadata, and short claim excerpts; HUMANITY does not
intend to republish complete news articles. The source record's configured
`fetch_full_content` flag is not a complete rights or retention control, and
the current workflow has not been audited source-by-source for every network,
storage, and retention condition.

Attribution links point readers back to the originating publisher when a URL is
available. This does not assert that a publisher has granted a license, that
quotation or storage is permitted, or that attribution alone is sufficient.
No commercial reuse, bulk redistribution, or sublicensing right is currently
cleared by this register.

## Review required before commercial use

For every connector, confirm source provenance and terms, API/feed terms,
robots and rate-limit obligations, content acquisition scope, storage and
retention permission, quotation and display permission, attribution format,
commercial-use permission, redistribution restrictions, and any applicable
privacy obligations. Review terms again when a provider changes them. Disable a
connector if its access or storage basis cannot be established. Legal review is
still required; this register is operational documentation, not legal advice.

The current source-status labels are deliberately conservative:

- **Pending**: no source-specific legal approval has been recorded.
- **Not cleared**: do not treat the content as licensed for commercial reuse or
  redistribution.

No source currently has an approved commercial-use or redistribution status.

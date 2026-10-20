# Privacy notice draft

**Status:** Engineering disclosure draft; not legal advice or a substitute for
jurisdiction-specific review. The project owner should verify the actual host
and provider settings before relying on this notice.

## What the public site does

HUMANITY is currently a static website. The codebase contains no first-party
account, login, payment, advertising, or analytics feature. The site fetches
published JSON feeds and, when a map is opened, requests map tiles from
OpenStreetMap tile servers. The hosting provider and tile provider may receive
standard connection data such as IP address, user agent, request URL, and time
under their own policies. HUMANITY does not control their retention practices.

This description reflects the reviewed application code, not a guarantee that
the hosting platform or future changes use no logs, cookies, or other
processing. Verify those settings before publication.

## Source and event data

The ingestion pipeline processes public-source metadata and can retrieve text
from feeds, APIs, or linked article pages for extraction. Source records are
persisted in the project data store. Public event records may include event
locations, linked source metadata, attributed claims, and short excerpts. News
content may refer to identifiable people; do not treat source availability as
permission to retain or republish it. Source-specific retention and rights
review remains pending; see the [source-rights register](/source-rights).

## Requests and corrections

The site does not currently offer a private account or in-product request form.
Corrections and privacy questions can be sent through the public
[GitHub issue tracker](https://github.com/asdfhgds/humanity/issues); avoid
posting sensitive personal information in a public issue. This channel is not
an emergency contact.

## Future changes

If HUMANITY adds accounts, analytics, alerts, paid services, or additional
third-party providers, update this notice and review applicable consent,
retention, security, and data-subject requirements before enabling them.

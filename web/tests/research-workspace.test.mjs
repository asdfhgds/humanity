import assert from "node:assert/strict";
import test from "node:test";

import {
  compareSearch,
  createResearchBrief,
  loadSavedSearches,
  markSearchReviewed,
  saveSearch,
} from "../src/lib/research-workspace.mjs";

function storage(initial = null) {
  let value = initial;
  return {
    getItem() {
      return value;
    },
    setItem(_key, nextValue) {
      value = nextValue;
    },
  };
}

function event(overrides = {}) {
  return {
    id: "event-1",
    title: "Reported demonstration",
    summary: "A public, source-linked summary.",
    known: ["Organizers reported a daytime demonstration."],
    uncertain: ["The final participant count remains unclear."],
    event_type: "PROTEST",
    start_time: "2026-10-18T12:00:00Z",
    country_name: "Kenya",
    sources: [
      {
        title: "Original report",
        publisher: "Example News",
        url: "https://example.test/report",
        published_at: "2026-10-18T13:00:00Z",
      },
    ],
    claims: {
      participants: [
        {
          subject: "protesters",
          predicate: "attended",
          value: 100,
          source_quote: "A source quotation not included in citation exports.",
          verification_status: "reported",
        },
      ],
    },
    ...overrides,
  };
}

test("missing browser storage is an empty local workspace", () => {
  assert.deepEqual(loadSavedSearches(storage()), []);
});

test("saved searches compare new, changed, repeated, and absent records", () => {
  const localStorage = storage();
  const baseline = event();
  const search = saveSearch(localStorage, {
    id: "search-1",
    name: "  Kenya protests  ",
    filters: { country: "Kenya", type: "PROTEST", ignored: "not persisted" },
    events: [baseline],
  });

  assert.equal(search.name, "Kenya protests");
  assert.deepEqual(search.filters, {
    q: "",
    type: "PROTEST",
    country: "Kenya",
    severity: "",
    days: "",
  });
  assert.deepEqual(
    compareSearch(search, [
      event(),
      event({ id: "event-2", title: "New report" }),
    ]),
    { added: ["event-2"], changed: [], outsideCurrentResults: [] },
  );

  const evidenceChanged = event({
    sources: [
      ...baseline.sources,
      {
        title: "Independent report",
        publisher: "Another Newsroom",
        url: "https://another.test/report",
      },
    ],
  });
  assert.deepEqual(compareSearch(search, [evidenceChanged]), {
    added: [],
    changed: ["event-1"],
    outsideCurrentResults: [],
  });
  assert.deepEqual(compareSearch(search, []), {
    added: [],
    changed: [],
    outsideCurrentResults: ["event-1"],
  });
});

test("repeated ingestion of the same canonical record does not create a false change", () => {
  const original = event();
  const search = saveSearch(storage(), {
    id: "search-1",
    name: "Conflict monitoring",
    filters: {},
    events: [original],
  });

  assert.deepEqual(compareSearch(search, [event()]), {
    added: [],
    changed: [],
    outsideCurrentResults: [],
  });
  assert.deepEqual(compareSearch(search, []), {
    added: [],
    changed: [],
    outsideCurrentResults: ["event-1"],
  });
});

test("reviewing a search stores the current baseline and handles no matching records", () => {
  const localStorage = storage();
  saveSearch(localStorage, {
    id: "search-1",
    name: "Kenya protests",
    filters: { country: "Kenya" },
    events: [event()],
  });
  const updated = markSearchReviewed(localStorage, "search-1", []);

  assert.deepEqual(updated.snapshot, {});
  assert.deepEqual(loadSavedSearches(localStorage), [updated]);
  assert.deepEqual(compareSearch(updated, []), {
    added: [],
    changed: [],
    outsideCurrentResults: [],
  });
  assert.throws(
    () => markSearchReviewed(localStorage, "missing", []),
    /was not found/,
  );
});

test("malformed or unsupported browser storage is reported instead of overwritten", () => {
  assert.throws(() => loadSavedSearches(storage("{")), SyntaxError);
  assert.throws(
    () =>
      loadSavedSearches(storage(JSON.stringify({ version: 2, searches: [] }))),
    /unsupported format/,
  );
  const malformed = storage("{");
  assert.throws(
    () =>
      saveSearch(malformed, {
        id: "search-1",
        name: "Kenya",
        filters: {},
        events: [],
      }),
    SyntaxError,
  );
});

test("source-linked brief is deterministic and excludes source excerpts", () => {
  const events = [
    event({
      id: "second",
      title: "Second event",
      start_time: "2026-10-19T12:00:00Z",
    }),
    event({
      id: "first",
      title: "First event",
      sources: [
        {
          title: "Untrusted URL",
          publisher: "Unknown",
          url: "javascript:alert(1)",
        },
        {
          title: "Private URL",
          publisher: "Unknown",
          url: "https://private.test/report?access_token=private-secret",
        },
      ],
    }),
    event({
      id: "unlinked",
      title: "Record without source URLs",
      sources: [],
      start_time: "invalid-date",
    }),
  ];

  const firstExport = createResearchBrief(events);
  const secondExport = createResearchBrief(events);
  assert.equal(firstExport, secondExport);
  assert.ok(
    firstExport.indexOf("Second event") < firstExport.indexOf("First event"),
  );
  assert.ok(
    firstExport.indexOf("First event") <
      firstExport.indexOf("Record without source URLs"),
  );
  assert.match(
    firstExport,
    /\[Example News — Original report\]\(https:\/\/example\.test\/report\)/,
  );
  assert.match(firstExport, /no source links are available/);
  assert.match(firstExport, /Organizers reported a daytime demonstration/);
  assert.match(firstExport, /final participant count remains unclear/);
  assert.match(firstExport, /A public, source-linked summary/);
  assert.doesNotMatch(firstExport, /A source quotation/);
  assert.doesNotMatch(firstExport, /javascript:/);
  assert.match(firstExport, /https:\/\/private\.test\/report/);
  assert.doesNotMatch(firstExport, /private-secret/);
});

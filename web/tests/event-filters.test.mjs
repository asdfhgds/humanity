import assert from "node:assert/strict";
import test from "node:test";

import {
  filterPublishedEvents,
  parseEventFilters,
  serializeEventFilters,
} from "../src/lib/event-filters.mjs";

const validTypes = new Set(["PROTEST", "DISASTER"]);
const validCountries = new Set(["Kenya", "France"]);
const events = [
  {
    title: "Demonstration in Nairobi",
    summary: "Workers rally over wages",
    event_type: "PROTEST",
    start_time: "2026-10-18T12:00:00Z",
    city: "Nairobi",
    country_name: "Kenya",
    severity: 4,
  },
  {
    title: "Flooding reported in Lyon",
    description: "Emergency crews respond to rising water",
    event_type: "DISASTER",
    start_time: "2026-10-17T12:00:00Z",
    city: "Lyon",
    country_name: "France",
    severity: 7,
  },
];

test("parses only recognized shareable filters", () => {
  const filters = parseEventFilters(
    new URLSearchParams(
      "q=%20rising%20&type=UNKNOWN&country=Kenya&severity=7&days=30",
    ),
    validTypes,
    validCountries,
  );

  assert.deepEqual(filters, {
    q: "rising",
    type: "",
    country: "Kenya",
    severity: "7",
    days: "30",
  });
});

test("serializes filters while preserving unrelated query parameters", () => {
  const params = serializeEventFilters(
    {
      q: "Nairobi rally",
      type: "PROTEST",
      country: "",
      severity: "",
      days: "",
    },
    new URLSearchParams("campaign=reader"),
  );

  assert.equal(params.get("q"), "Nairobi rally");
  assert.equal(params.get("type"), "PROTEST");
  assert.equal(params.get("campaign"), "reader");
  assert.equal(params.has("country"), false);
});

test("search includes summary and description and filters by date and severity", () => {
  const filters = {
    q: "emergency crews",
    type: "DISASTER",
    country: "France",
    severity: "6",
    days: "7",
  };

  assert.deepEqual(
    filterPublishedEvents(events, filters, Date.parse("2026-10-18T12:00:00Z")),
    [events[1]],
  );
});

test("date filters exclude invalid, future, and out-of-range event dates", () => {
  const filters = { q: "", type: "", country: "", severity: "", days: "1" };
  const datedEvents = [
    events[0],
    { ...events[1], start_time: "2026-10-17T11:59:00Z" },
    { ...events[0], title: "Invalid date", start_time: "not-a-date" },
    { ...events[0], title: "Future event", start_time: "2026-10-20T12:00:00Z" },
  ];

  assert.deepEqual(
    filterPublishedEvents(
      datedEvents,
      filters,
      Date.parse("2026-10-18T12:00:00Z"),
    ),
    [events[0]],
  );
});

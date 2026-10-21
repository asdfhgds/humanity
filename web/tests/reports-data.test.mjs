import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { buildResearchReports } from "../src/lib/reports-data.mjs";

const webDirectory = dirname(dirname(fileURLToPath(import.meta.url)));
const researchDirectory = join(webDirectory, "public", "data", "research");

async function loadRealData() {
  const index = JSON.parse(
    await readFile(join(researchDirectory, "ucdp-ged.json"), "utf8"),
  );
  const feeds = new Map();
  for (const year of [1989, 2024, 2025]) {
    const path = join(researchDirectory, "ucdp-ged", "years", `${year}.json`);
    feeds.set(year, JSON.parse(await readFile(path, "utf8")));
  }
  return { index, feeds };
}

test("research report calculations match real GED 26.1 partitions", async () => {
  const { index, feeds } = await loadRealData();
  const reports = buildResearchReports(index, feeds);

  assert.equal(reports.length, 3);
  assert.deepEqual(
    reports.map(({ slug }) => slug),
    [
      "ged-recorded-events-2024-2025",
      "where-ged-records-events-in-2024",
      "ged-records-1989-and-2024",
    ],
  );
  const [yearComparison, countryReport, endpointComparison] = reports;

  assert.equal(yearComparison.data[2024].recordCount, 30066);
  assert.equal(yearComparison.data[2025].recordCount, 25770);
  assert.equal(
    yearComparison.data.comparison.reduce(
      (total, item) => total + item.earlierCount,
      0,
    ),
    30066,
  );
  assert.equal(
    yearComparison.data.comparison.reduce(
      (total, item) => total + item.laterCount,
      0,
    ),
    25770,
  );
  assert.equal(countryReport.data[2024].countries[0].country, "Ukraine");
  assert.equal(countryReport.data[2024].countries[0].count, 11102);
  assert.equal(endpointComparison.data[1989].recordCount, 2659);
  assert.equal(endpointComparison.data[2024].recordCount, 30066);
  assert.equal(index.dataset_version, "26.1");
});

test("report generation rejects unavailable, mismatched, or malformed partitions", async () => {
  const { index, feeds } = await loadRealData();
  const unavailableIndex = { ...index, status: "stale" };
  assert.throws(
    () => buildResearchReports(unavailableIndex, feeds),
    /available UCDP dataset index/,
  );

  const wrongYear = new Map(feeds);
  wrongYear.set(2024, { ...feeds.get(2024), year: 2023 });
  assert.throws(() => buildResearchReports(index, wrongYear), /2024 partition/);

  const stalePartition = new Map(feeds);
  stalePartition.set(2024, { ...feeds.get(2024), status: "stale" });
  assert.throws(
    () => buildResearchReports(index, stalePartition),
    /2024 partition/,
  );

  const malformed = new Map(feeds);
  malformed.set(1989, { ...feeds.get(1989), record_count: 0 });
  assert.throws(() => buildResearchReports(index, malformed), /invalid format/);

  const wrongRecordYear = new Map(feeds);
  const real1989 = feeds.get(1989);
  wrongRecordYear.set(1989, {
    ...real1989,
    records: [
      { ...real1989.records[0], event_year: 1990 },
      ...real1989.records.slice(1),
    ],
  });
  assert.throws(
    () => buildResearchReports(index, wrongRecordYear),
    /inconsistent or duplicate/,
  );

  const duplicateRecord = new Map(feeds);
  duplicateRecord.set(1989, {
    ...real1989,
    records: [
      real1989.records[0],
      real1989.records[0],
      ...real1989.records.slice(2),
    ],
  });
  assert.throws(
    () => buildResearchReports(index, duplicateRecord),
    /inconsistent or duplicate/,
  );

  const missing = new Map(feeds);
  missing.delete(2025);
  assert.throws(() => buildResearchReports(index, missing), /invalid format/);

  assert.throws(
    () => buildResearchReports({ ...index, attribution: "" }, feeds),
    /complete dataset attribution/,
  );
});

test("year-to-year comparisons report record counts, not fatality sums", async () => {
  const { index, feeds } = await loadRealData();
  const report = buildResearchReports(index, feeds)[0];
  assert.ok(
    report.data.comparison.every((item) => {
      const earlier = report.data[2024].violenceTypes.find(
        (type) => type.id === item.id,
      );
      const later = report.data[2025].violenceTypes.find(
        (type) => type.id === item.id,
      );
      return (
        item.earlierCount === earlier.count &&
        item.laterCount === later.count &&
        item.change === later.count - earlier.count
      );
    }),
  );
  assert.equal("fatalities" in report.data.comparison[0], false);
});

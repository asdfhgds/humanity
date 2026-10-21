import { readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { parseUcdpFeed, parseUcdpIndex } from "./research-data.mjs";

export const REPORT_CATALOG = [
  {
    slug: "ged-recorded-events-2024-2025",
    title: "How recorded event counts changed between 2024 and 2025",
    description:
      "A source-faithful comparison of UCDP GED 26.1 event-record counts by violence category in 2024 and 2025.",
    publishedAt: "2026-10-21",
    years: [2024, 2025],
    kind: "year-comparison",
  },
  {
    slug: "where-ged-records-events-in-2024",
    title: "Where GED 26.1 recorded events in 2024",
    description:
      "A country-level view of records in the 2024 UCDP Georeferenced Event Dataset partition, with coverage and classification caveats.",
    publishedAt: "2026-10-21",
    years: [2024],
    kind: "country-distribution",
  },
  {
    slug: "ged-records-1989-and-2024",
    title: "A two-year comparison: 1989 and 2024 in GED 26.1",
    description:
      "A comparison of source-native UCDP GED record counts at two points 35 years apart, without implying a continuous trend.",
    publishedAt: "2026-10-21",
    years: [1989, 2024],
    kind: "endpoint-comparison",
  },
];

const VIOLENCE_TYPES = [
  { id: 1, label: "State-based conflict" },
  { id: 2, label: "Non-state conflict" },
  { id: 3, label: "One-sided violence" },
];
const reportCache = new Map();

export function reportCanonicalUrl(pathname, basePath, site) {
  const normalizedBase = basePath.replace(/\/$/, "");
  const routePath =
    normalizedBase &&
    (pathname === normalizedBase || pathname.startsWith(`${normalizedBase}/`))
      ? pathname.slice(normalizedBase.length) || "/"
      : pathname;
  return new URL(`${normalizedBase}${routePath}`, site).href;
}

function percent(part, total) {
  return total === 0 ? null : Number(((part / total) * 100).toFixed(1));
}

function summarizeYear(feed) {
  const violenceCounts = new Map(VIOLENCE_TYPES.map(({ id }) => [id, 0]));
  const countryCounts = new Map();
  let recordsWithBestEstimate = 0;

  for (const record of feed.records) {
    violenceCounts.set(
      record.violence_type,
      (violenceCounts.get(record.violence_type) || 0) + 1,
    );
    if (record.country) {
      countryCounts.set(
        record.country,
        (countryCounts.get(record.country) || 0) + 1,
      );
    }
    if (Number.isFinite(record.deaths_best)) recordsWithBestEstimate += 1;
  }

  return {
    year: feed.year,
    recordCount: feed.record_count,
    recordsWithBestEstimate,
    bestEstimateCoverage: percent(recordsWithBestEstimate, feed.record_count),
    violenceTypes: VIOLENCE_TYPES.map(({ id, label }) => ({
      id,
      label,
      count: violenceCounts.get(id) || 0,
      share: percent(violenceCounts.get(id) || 0, feed.record_count),
    })),
    countries: [...countryCounts.entries()]
      .map(([country, count]) => ({
        country,
        count,
        share: percent(count, feed.record_count),
      }))
      .sort(
        (left, right) =>
          right.count - left.count || left.country.localeCompare(right.country),
      ),
  };
}

function validateFeeds(index, feedsByYear, requiredYears) {
  if (index.status !== "available" || !index.dataset_version) {
    throw new Error("Research reports require an available UCDP dataset index");
  }
  if (
    !index.attribution ||
    !index.dataset_url ||
    !index.codebook_url ||
    !index.license_url
  ) {
    throw new Error(
      "Research reports require complete dataset attribution and source links",
    );
  }

  const declaredYears = new Map(
    index.years.map(({ year, record_count }) => [year, record_count]),
  );
  const summaries = new Map();
  for (const year of requiredYears) {
    const feed = parseUcdpFeed(feedsByYear.get(year));
    if (
      feed.year !== year ||
      feed.dataset_version !== index.dataset_version ||
      feed.status !== "available" ||
      declaredYears.get(year) !== feed.record_count
    ) {
      throw new Error(
        `UCDP ${year} partition does not match the available index`,
      );
    }
    const recordIds = new Set();
    for (const record of feed.records) {
      if (
        record.event_year !== year ||
        record.dataset_version !== index.dataset_version ||
        recordIds.has(record.id)
      ) {
        throw new Error(
          `UCDP ${year} partition contains inconsistent or duplicate records`,
        );
      }
      recordIds.add(record.id);
    }
    summaries.set(year, summarizeYear(feed));
  }
  return summaries;
}

export function buildResearchReports(indexValue, feedValues) {
  const index = parseUcdpIndex(indexValue);
  const feedsByYear =
    feedValues instanceof Map
      ? feedValues
      : new Map(
          Object.entries(feedValues || {}).map(([year, feed]) => [
            Number(year),
            feed,
          ]),
        );
  const requiredYears = [
    ...new Set(REPORT_CATALOG.flatMap(({ years }) => years)),
  ];
  const summaries = validateFeeds(index, feedsByYear, requiredYears);

  return REPORT_CATALOG.map((catalog) => {
    const data = Object.fromEntries(
      catalog.years.map((year) => [year, summaries.get(year)]),
    );
    if (catalog.kind === "year-comparison") {
      const [earlier, later] = catalog.years.map((year) => data[year]);
      data.totalChange = later.recordCount - earlier.recordCount;
      data.totalChangePercent = percent(data.totalChange, earlier.recordCount);
      data.comparison = VIOLENCE_TYPES.map(({ id, label }) => {
        const earlierCount =
          earlier.violenceTypes.find((item) => item.id === id)?.count || 0;
        const laterCount =
          later.violenceTypes.find((item) => item.id === id)?.count || 0;
        return {
          id,
          label,
          earlierCount,
          laterCount,
          change: laterCount - earlierCount,
          changePercent: percent(laterCount - earlierCount, earlierCount),
        };
      });
    }
    if (catalog.kind === "endpoint-comparison") {
      const [earlier, later] = catalog.years.map((year) => data[year]);
      data.elapsedYears = later.year - earlier.year;
      data.comparison = VIOLENCE_TYPES.map(({ id, label }) => {
        const earlierCount =
          earlier.violenceTypes.find((item) => item.id === id)?.count || 0;
        const laterCount =
          later.violenceTypes.find((item) => item.id === id)?.count || 0;
        return {
          id,
          label,
          earlierCount,
          laterCount,
          change: laterCount - earlierCount,
        };
      });
    }
    return { ...catalog, datasetVersion: index.dataset_version, data, index };
  });
}

export async function loadResearchReports(publicDirectory) {
  const root = resolve(publicDirectory);
  if (!reportCache.has(root)) {
    reportCache.set(
      root,
      (async () => {
        const researchDirectory = join(root, "data", "research");
        const index = JSON.parse(
          await readFile(join(researchDirectory, "ucdp-ged.json"), "utf8"),
        );
        const requiredYears = [
          ...new Set(REPORT_CATALOG.flatMap(({ years }) => years)),
        ];
        const feeds = await Promise.all(
          requiredYears.map(async (year) => {
            const path = join(
              researchDirectory,
              "ucdp-ged",
              "years",
              `${year}.json`,
            );
            return [year, JSON.parse(await readFile(path, "utf8"))];
          }),
        );
        return buildResearchReports(index, new Map(feeds));
      })(),
    );
  }
  return reportCache.get(root);
}

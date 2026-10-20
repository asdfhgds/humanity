const STORAGE_KEY = "humanity.research-workspace.v1";
const FILTER_KEYS = ["q", "type", "country", "severity", "days"];

function compareStrings(left, right) {
  return left < right ? -1 : left > right ? 1 : 0;
}

function normalizeFilters(filters = {}) {
  return Object.fromEntries(
    FILTER_KEYS.map((key) => [
      key,
      typeof filters[key] === "string" ? filters[key] : "",
    ]),
  );
}

function eventKey(event) {
  return typeof event.id === "string" && event.id
    ? event.id
    : typeof event.slug === "string" && event.slug
      ? event.slug
      : null;
}

function stableEventFingerprint(event) {
  const sources = (Array.isArray(event.sources) ? event.sources : [])
    .map((source) => ({
      title: source.title || "",
      publisher: source.publisher || "",
      url: source.url || "",
      published_at: source.published_at || "",
    }))
    .sort((left, right) =>
      compareStrings(JSON.stringify(left), JSON.stringify(right)),
    );
  const claims = Object.entries(event.claims || {})
    .sort(([left], [right]) => compareStrings(left, right))
    .flatMap(([type, values]) =>
      (Array.isArray(values) ? values : [])
        .map((claim) => ({
          type,
          subject: claim.subject || "",
          predicate: claim.predicate || "",
          value: claim.value ?? null,
          min_value: claim.min_value ?? null,
          max_value: claim.max_value ?? null,
          unit: claim.unit || "",
          source_quote: claim.source_quote || "",
          confidence: claim.confidence ?? null,
          verification_status: claim.verification_status || "",
        }))
        .sort((left, right) =>
          compareStrings(JSON.stringify(left), JSON.stringify(right)),
        ),
    );
  const relevantRecord = JSON.stringify({
    title: event.title || "",
    summary: event.summary || "",
    description: event.description || "",
    event_type: event.event_type || "",
    start_time: event.start_time || "",
    city: event.city || "",
    country_name: event.country_name || "",
    severity: event.severity ?? null,
    confidence: event.confidence || "",
    confidence_label: event.confidence_label || "",
    known: Array.isArray(event.known) ? event.known : [],
    uncertain: Array.isArray(event.uncertain) ? event.uncertain : [],
    issue: event.issue || "",
    last_updated_at: event.last_updated_at || "",
    sources,
    claims,
  });

  let fingerprint = 14695981039346656037n;
  for (let index = 0; index < relevantRecord.length; index += 1) {
    fingerprint ^= BigInt(relevantRecord.charCodeAt(index));
    fingerprint = BigInt.asUintN(64, fingerprint * 1099511628211n);
  }
  return fingerprint.toString(16);
}

function snapshotEvents(events) {
  return Object.fromEntries(
    events.flatMap((event) => {
      const key = eventKey(event);
      return key ? [[key, stableEventFingerprint(event)]] : [];
    }),
  );
}

function readWorkspace(storage) {
  const raw = storage.getItem(STORAGE_KEY);
  if (raw === null) return { searches: [] };

  const workspace = JSON.parse(raw);
  if (
    !workspace ||
    workspace.version !== 1 ||
    !Array.isArray(workspace.searches) ||
    !workspace.searches.every(
      (search) =>
        search &&
        typeof search.id === "string" &&
        typeof search.name === "string" &&
        search.filters &&
        typeof search.filters === "object" &&
        FILTER_KEYS.every((key) => typeof search.filters[key] === "string") &&
        search.snapshot &&
        typeof search.snapshot === "object" &&
        Object.values(search.snapshot).every(
          (value) => typeof value === "string",
        ),
    )
  ) {
    throw new Error("Saved monitoring data has an unsupported format");
  }
  return workspace;
}

function writeWorkspace(storage, searches) {
  storage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, searches }));
}

export function loadSavedSearches(storage) {
  return readWorkspace(storage).searches;
}

export function saveSearch(storage, { id, name, filters, events }) {
  const normalizedName = typeof name === "string" ? name.trim() : "";
  if (!id || !normalizedName) {
    throw new Error("A saved search needs an identifier and a name");
  }
  const searches = readWorkspace(storage).searches;
  const search = {
    id,
    name: normalizedName,
    filters: normalizeFilters(filters),
    snapshot: snapshotEvents(events),
  };
  writeWorkspace(storage, [
    ...searches.filter((item) => item.id !== id),
    search,
  ]);
  return search;
}

export function compareSearch(search, events) {
  const current = snapshotEvents(events);
  const baseline = search.snapshot || {};
  const currentIds = Object.keys(current);
  const previousIds = Object.keys(baseline);
  return {
    added: currentIds.filter((id) => !(id in baseline)),
    changed: currentIds.filter(
      (id) => id in baseline && current[id] !== baseline[id],
    ),
    outsideCurrentResults: previousIds.filter((id) => !(id in current)),
  };
}

export function markSearchReviewed(storage, id, events) {
  const searches = readWorkspace(storage).searches;
  const index = searches.findIndex((search) => search.id === id);
  if (index < 0) throw new Error("Saved search was not found");
  const updated = { ...searches[index], snapshot: snapshotEvents(events) };
  searches[index] = updated;
  writeWorkspace(storage, searches);
  return updated;
}

function markdownText(value) {
  return String(value || "")
    .replace(/[\r\n]+/g, " ")
    .replace(/([\\`*_{}\[\]()#+.!|>])/g, "\\$1");
}

function markdownUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    if (
      (url.protocol !== "http:" && url.protocol !== "https:") ||
      url.username ||
      url.password
    ) {
      return null;
    }
    for (const key of [...url.searchParams.keys()]) {
      if (/key|token|auth|secret|signature|credential/i.test(key)) {
        url.searchParams.delete(key);
      }
    }
    return url.href.replace(/[()[\]\\]/g, encodeURIComponent);
  } catch {
    return null;
  }
}

export function createResearchBrief(events) {
  const orderedEvents = [...events].sort((left, right) => {
    const leftTime = Date.parse(left.start_time);
    const rightTime = Date.parse(right.start_time);
    if (Number.isFinite(leftTime) && Number.isFinite(rightTime)) {
      if (leftTime !== rightTime) return rightTime - leftTime;
    } else if (Number.isFinite(leftTime)) {
      return -1;
    } else if (Number.isFinite(rightTime)) {
      return 1;
    }
    return compareStrings(
      String(eventKey(left) || left.title),
      String(eventKey(right) || right.title),
    );
  });
  const lines = [
    "# HUMANITY source-linked research brief",
    "",
    "Generated from published HUMANITY records. Source article rights remain with their respective providers; this citation export does not grant reuse rights.",
    "",
  ];

  for (const event of orderedEvents) {
    const location = [event.city, event.country_name]
      .filter(Boolean)
      .join(", ");
    lines.push(`## ${markdownText(event.title)}`);
    lines.push("");
    lines.push(
      `- Date: ${markdownText(event.start_time || "Unknown")}`,
      `- Location: ${markdownText(location || "Unconfirmed")}`,
    );
    if (event.summary) lines.push(`- Summary: ${markdownText(event.summary)}`);
    if (Array.isArray(event.known) && event.known.length) {
      lines.push("- Reported as known:");
      for (const fact of event.known) lines.push(`  - ${markdownText(fact)}`);
    }
    if (Array.isArray(event.uncertain) && event.uncertain.length) {
      lines.push("- Uncertainty:");
      for (const uncertainty of event.uncertain) {
        lines.push(`  - ${markdownText(uncertainty)}`);
      }
    }
    const sources = (Array.isArray(event.sources) ? event.sources : [])
      .map((source) => ({
        source,
        url: markdownUrl(source.url),
      }))
      .filter(({ url }) => url)
      .sort((left, right) =>
        compareStrings(
          `${left.source.publisher || ""}${left.source.title}`,
          `${right.source.publisher || ""}${right.source.title}`,
        ),
      );
    if (sources.length) {
      lines.push("- Sources:");
      for (const { source, url } of sources) {
        const label = [source.publisher, source.title]
          .filter(Boolean)
          .join(" — ");
        lines.push(`  - [${markdownText(label)}](${url})`);
      }
    } else {
      lines.push("- Sources: no source links are available in this record.");
    }
    lines.push("");
  }

  return `${lines.join("\n").trimEnd()}\n`;
}

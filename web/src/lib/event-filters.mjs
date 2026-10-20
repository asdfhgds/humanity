const VALID_DAYS = new Set(["1", "7", "30", "90"]);
const VALID_SEVERITIES = new Set([
  "1",
  "2",
  "3",
  "4",
  "5",
  "6",
  "7",
  "8",
  "9",
  "10",
]);

export function parseEventFilters(params, validTypes, validCountries) {
  const type = params.get("type") || "";
  const country = params.get("country") || "";
  const severity = params.get("severity") || "";
  const days = params.get("days") || "";

  return {
    q: (params.get("q") || "").trim(),
    type: validTypes.has(type) ? type : "",
    country: validCountries.has(country) ? country : "",
    severity: VALID_SEVERITIES.has(severity) ? severity : "",
    days: VALID_DAYS.has(days) ? days : "",
  };
}

export function serializeEventFilters(
  filters,
  currentParams = new URLSearchParams(),
) {
  const params = new URLSearchParams(currentParams);
  for (const key of ["q", "type", "country", "severity", "days"]) {
    const value = filters[key]?.trim() || "";
    if (value) params.set(key, value);
    else params.delete(key);
  }
  return params;
}

export function filterPublishedEvents(events, filters, now = Date.now()) {
  const search = filters.q.toLocaleLowerCase();
  const cutoff = filters.days
    ? now - Number(filters.days) * 24 * 60 * 60 * 1000
    : null;

  return events
    .filter((event) => {
      const searchableText = [
        event.title,
        event.summary,
        event.description,
        event.city,
        event.country_name,
      ]
        .filter(Boolean)
        .join(" ")
        .toLocaleLowerCase();

      if (search && !searchableText.includes(search)) return false;
      if (filters.type && event.event_type !== filters.type) return false;
      if (filters.country && event.country_name !== filters.country)
        return false;
      if (filters.severity && (event.severity ?? 0) < Number(filters.severity))
        return false;
      if (cutoff !== null) {
        const eventTime = Date.parse(event.start_time);
        if (
          !Number.isFinite(eventTime) ||
          eventTime < cutoff ||
          eventTime > now
        )
          return false;
      }
      return true;
    })
    .sort((a, b) => Date.parse(b.start_time) - Date.parse(a.start_time));
}

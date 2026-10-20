export const UCDP_EXPORT_FIELDS = [
  'source_record_id',
  'dataset_version',
  'event_year',
  'date_start',
  'date_end',
  'country',
  'region',
  'location',
  'admin1',
  'admin2',
  'violence_type',
  'conflict_name',
  'deaths_a',
  'deaths_b',
  'deaths_civilians',
  'deaths_unknown',
  'deaths_best',
  'deaths_low',
  'deaths_high',
  'date_precision',
  'location_precision',
  'event_clarity',
  'number_of_sources',
  'latitude',
  'longitude',
];

export function parseUcdpFeed(value) {
  if (
    !value ||
    typeof value !== 'object' ||
    value.schema_version !== 1 ||
    !['not_imported', 'available', 'stale'].includes(value.status) ||
    !Array.isArray(value.records) ||
    !Number.isInteger(value.year) ||
    !Number.isInteger(value.record_count) ||
    value.record_count !== value.records.length ||
    !Number.isInteger(value.dataset_total_count) ||
    value.dataset_total_count < value.record_count
  ) {
    throw new Error('UCDP research feed has an invalid format');
  }
  if (!value.records.every(isUcdpRecord)) {
    throw new Error('UCDP research feed contains an invalid observation');
  }
  return value;
}

export function parseUcdpIndex(value) {
  if (
    !value ||
    typeof value !== 'object' ||
    value.schema_version !== 1 ||
    !['not_imported', 'available', 'stale'].includes(value.status) ||
    !Number.isInteger(value.record_count) ||
    value.record_count < 0 ||
    !Number.isInteger(value.observations_missing_source_counts) ||
    value.observations_missing_source_counts < 0 ||
    !Array.isArray(value.years) ||
    !value.years.every((item) =>
      item &&
      typeof item === 'object' &&
      Number.isInteger(item.year) &&
      Number.isInteger(item.record_count) &&
      item.record_count >= 0
    ) ||
    value.years.reduce((total, item) => total + item.record_count, 0) !== value.record_count
  ) {
    throw new Error('UCDP research index has an invalid format');
  }
  return value;
}

export function isUcdpRecord(record) {
  return Boolean(record) &&
    typeof record === 'object' &&
    typeof record.id === 'string' &&
    typeof record.source_record_id === 'string' &&
    typeof record.dataset_version === 'string' &&
    Number.isInteger(record.event_year) &&
    Number.isInteger(record.violence_type);
}

export function hasValidCoordinates(record) {
  return Number.isFinite(record.latitude) &&
    Number.isFinite(record.longitude) &&
    record.latitude >= -90 &&
    record.latitude <= 90 &&
    record.longitude >= -180 &&
    record.longitude <= 180;
}

export function filterUcdpRecords(records, filters = {}) {
  const query = (filters.query || '').trim().toLocaleLowerCase();
  return records.filter((record) => {
    const date = record.date_start;
    if (filters.from && (!date || date < filters.from)) return false;
    if (filters.to && (!date || date > filters.to)) return false;
    if (filters.country && record.country !== filters.country) return false;
    if (filters.violenceType && String(record.violence_type) !== filters.violenceType) return false;
    if (
      filters.minimumDeaths !== undefined &&
      filters.minimumDeaths !== '' &&
      (record.deaths_best === null ||
        record.deaths_best === undefined ||
        record.deaths_best < Number(filters.minimumDeaths))
    ) return false;
    if (query) {
      const searchable = [
        record.source_record_id,
        record.conflict_name,
        record.location,
        record.admin1,
        record.admin2,
        record.country,
        record.region,
      ].filter(Boolean).join(' ').toLocaleLowerCase();
      if (!searchable.includes(query)) return false;
    }
    return true;
  });
}

export function toUcdpCsv(records) {
  const escape = (value) => {
    const text = value === null || value === undefined ? '' : String(value);
    return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  };
  return [
    UCDP_EXPORT_FIELDS.join(','),
    ...records.map((record) => UCDP_EXPORT_FIELDS.map((field) => escape(record[field])).join(',')),
  ].join('\r\n');
}

export function toUcdpJson(feed, records, currentIndex = feed) {
  return JSON.stringify({
    dataset: currentIndex.dataset || feed.dataset,
    dataset_version: currentIndex.dataset_version || feed.dataset_version,
    status: currentIndex.status || feed.status,
    retrieved_at: currentIndex.retrieved_at || feed.retrieved_at,
    license: currentIndex.license || feed.license,
    license_url: currentIndex.license_url || feed.license_url,
    attribution: currentIndex.attribution || feed.attribution,
    year: feed.year,
    fields: UCDP_EXPORT_FIELDS,
    records,
  }, null, 2);
}

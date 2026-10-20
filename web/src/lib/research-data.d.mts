export interface UcdpRecord {
  id: string;
  source_dataset: string;
  dataset_version: string;
  source_record_id: string;
  record_number: string;
  event_year: number;
  date_start: string | null;
  date_end: string | null;
  country: string | null;
  region: string | null;
  location: string | null;
  admin1: string | null;
  admin2: string | null;
  latitude: number | null;
  longitude: number | null;
  violence_type: number;
  conflict_name: string | null;
  deaths_a: number | null;
  deaths_b: number | null;
  deaths_civilians: number | null;
  deaths_unknown: number | null;
  deaths_best: number | null;
  deaths_high: number | null;
  deaths_low: number | null;
  date_precision: number | null;
  location_precision: number | null;
  event_clarity: number | null;
  number_of_sources: number | null;
  [field: string]: unknown;
}

export interface UcdpFeed {
  schema_version: 1;
  dataset: string;
  dataset_version: string | null;
  status: 'not_imported' | 'available' | 'stale';
  last_attempt_at: string | null;
  last_attempt_status: string;
  last_error_category: string | null;
  retrieved_at: string | null;
  coverage_start: string | null;
  coverage_end: string | null;
  record_count: number;
  observations_missing_source_counts: number;
  year: number;
  dataset_total_count: number;
  license: string;
  license_url: string;
  attribution: string;
  records: UcdpRecord[];
}

export interface UcdpIndex extends Omit<
  UcdpFeed,
  'records' | 'year' | 'dataset_total_count'
> {
  years: { year: number; record_count: number }[];
}

export interface UcdpFilters {
  query?: string;
  country?: string;
  violenceType?: string;
  from?: string;
  to?: string;
  minimumDeaths?: number | string;
}

export const UCDP_EXPORT_FIELDS: string[];
export function parseUcdpFeed(value: unknown): UcdpFeed;
export function parseUcdpIndex(value: unknown): UcdpIndex;
export function isUcdpRecord(record: unknown): record is UcdpRecord;
export function hasValidCoordinates(
  record: UcdpRecord,
): record is UcdpRecord & { latitude: number; longitude: number };
export function filterUcdpRecords(
  records: UcdpRecord[],
  filters?: UcdpFilters,
): UcdpRecord[];
export function toUcdpCsv(records: UcdpRecord[]): string;
export function toUcdpJson(
  feed: UcdpFeed,
  records: UcdpRecord[],
  currentIndex?: UcdpFeed | UcdpIndex,
): string;

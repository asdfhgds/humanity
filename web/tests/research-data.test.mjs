import test from 'node:test';
import assert from 'node:assert/strict';
import {
  filterUcdpRecords,
  hasValidCoordinates,
  parseUcdpFeed,
  parseUcdpIndex,
  toUcdpCsv,
  toUcdpJson,
} from '../src/lib/research-data.mjs';

const record = (overrides = {}) => ({
  id: 'ucdp-ged-26.1-101',
  source_dataset: 'UCDP GED',
  dataset_version: '26.1',
  source_record_id: 'TST-2020-1-1-1',
  record_number: '101',
  event_year: 2020,
  date_start: '2020-01-02',
  date_end: '2020-01-02',
  country: 'Exampleland',
  region: 'Africa',
  location: 'Example town',
  admin1: 'Example province',
  admin2: null,
  latitude: 12.5,
  longitude: 23.5,
  violence_type: 1,
  conflict_name: 'Example conflict',
  deaths_a: 2,
  deaths_b: 1,
  deaths_civilians: 3,
  deaths_unknown: 0,
  deaths_best: 6,
  deaths_high: 7,
  deaths_low: 5,
  date_precision: 1,
  location_precision: 1,
  event_clarity: 1,
  number_of_sources: 2,
  ...overrides,
});

const feed = (records = [record()]) => ({
  schema_version: 1,
  status: 'available',
  dataset_version: '26.1',
  year: 2020,
  record_count: records.length,
  dataset_total_count: records.length,
  observations_missing_source_counts: 0,
  records,
});

test('validates the dedicated UCDP feed and rejects malformed observations', () => {
  assert.equal(parseUcdpFeed(feed()).records.length, 1);
  assert.throws(() => parseUcdpFeed({ ...feed(), schema_version: 2 }));
  assert.throws(() => parseUcdpFeed(feed([{ id: 'missing native identity' }])));
  assert.throws(() => parseUcdpFeed({ ...feed(), record_count: 5 }));
});

test('validates lightweight year index and rejects malformed chunk metadata', () => {
  const metadata = { ...feed(), years: [{ year: 2020, record_count: 1 }] };
  delete metadata.records;
  assert.equal(parseUcdpIndex(metadata).years[0].year, 2020);
  assert.throws(() => parseUcdpIndex({ ...metadata, years: [{ year: '2020' }] }));
  assert.throws(() => parseUcdpIndex({ ...metadata, record_count: 2 }));
});

test('filters by date, country, category, fatality estimate, and query', () => {
  const rows = [
    record(),
    record({
      id: 'ucdp-ged-26.1-102',
      source_record_id: 'TST-2021-1-1-1',
      date_start: '2021-03-04',
      date_end: '2021-03-04',
      country: 'Otherland',
      admin1: 'Other province',
      violence_type: 2,
      deaths_best: 2,
    }),
  ];
  assert.deepEqual(filterUcdpRecords(rows, { from: '2021-01-01' }).map((r) => r.id), [
    'ucdp-ged-26.1-102',
  ]);
  assert.deepEqual(filterUcdpRecords(rows, { country: 'Exampleland' }).map((r) => r.id), [
    'ucdp-ged-26.1-101',
  ]);
  assert.deepEqual(filterUcdpRecords(rows, { violenceType: '2' }).map((r) => r.id), [
    'ucdp-ged-26.1-102',
  ]);
  assert.deepEqual(filterUcdpRecords(rows, { minimumDeaths: 5 }).map((r) => r.id), [
    'ucdp-ged-26.1-101',
  ]);
  assert.deepEqual(filterUcdpRecords(rows, { query: 'example province' }).map((r) => r.id), [
    'ucdp-ged-26.1-101',
  ]);
});

test('date filters do not invent chronology for observations with missing dates', () => {
  const undated = record({ date_start: null, date_end: null });
  assert.deepEqual(filterUcdpRecords([undated], {}), [undated]);
  assert.deepEqual(filterUcdpRecords([undated], { from: '2020-01-01' }), []);
});

test('map eligibility requires finite, geographically valid coordinate pairs', () => {
  assert.equal(hasValidCoordinates(record()), true);
  assert.equal(hasValidCoordinates(record({ latitude: null })), false);
  assert.equal(hasValidCoordinates(record({ longitude: 181 })), false);
  assert.equal(hasValidCoordinates(record({ latitude: Number.NaN })), false);
});

test('CSV exports have stable columns, escaped values, and omit non-selected fields', () => {
  const row = record({ conflict_name: 'Conflict, "quoted"' });
  const first = toUcdpCsv([row]);
  assert.equal(first, toUcdpCsv([row]));
  assert.match(first, /"Conflict, ""quoted"""/);
  assert.match(first.split('\r\n')[0], /^source_record_id,dataset_version,event_year/);
  assert.equal(first.includes('source_article'), false);
});

test('JSON exports preserve attribution and are deterministic for a filtered selection', () => {
  const source = feed([record()]);
  source.attribution = 'UCDP GED 26.1; changes: source text omitted';
  const first = toUcdpJson(source, source.records);
  assert.equal(first, toUcdpJson(source, source.records));
  const exported = JSON.parse(first);
  assert.equal(exported.attribution, source.attribution);
  assert.equal(exported.dataset_version, '26.1');
  assert.deepEqual(exported.records, source.records);
});

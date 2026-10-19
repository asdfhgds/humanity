import type { PublishedEvent } from "./client-utils";

export interface EventFilters {
  q: string;
  type: string;
  country: string;
  severity: string;
  days: string;
}

export function parseEventFilters(
  params: URLSearchParams,
  validTypes: ReadonlySet<string>,
  validCountries: ReadonlySet<string>,
): EventFilters;

export function serializeEventFilters(
  filters: EventFilters,
  currentParams?: URLSearchParams,
): URLSearchParams;

export function filterPublishedEvents(
  events: PublishedEvent[],
  filters: EventFilters,
  now?: number,
): PublishedEvent[];

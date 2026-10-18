const siteBase = import.meta.env.BASE_URL.endsWith('/')
  ? import.meta.env.BASE_URL
  : `${import.meta.env.BASE_URL}/`;

export interface PublishedEvent {
  id?: string;
  slug?: string;
  title: string;
  summary?: string;
  description?: string;
  event_type: string;
  start_time: string;
  publication_status?: 'published' | 'published_with_warning';
  end_time?: string;
  city?: string;
  country_name?: string;
  country_code?: string;
  severity?: number;
  confidence?: string;
  confidence_label?: string;
  issue?: string;
  status?: string;
  source_count?: number;
  sources?: PublishedSource[];
  claims?: Record<string, PublishedClaim[]>;
  known?: string[];
  uncertain?: string[];
  related_events?: string[];
  conflict_id?: string;
  latitude?: number;
  longitude?: number;
  published_at?: string;
  first_detected_at?: string;
  last_updated_at?: string;
  publish_warning?: boolean;
  human_impact?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface PublishedSource {
  title: string;
  url?: string;
  publisher?: string;
  source_type?: string;
  published_at?: string;
  country?: string;
  language?: string;
}

export interface PublishedClaim {
  subject?: string;
  predicate?: string;
  value?: number | string | null;
  min_value?: number;
  max_value?: number;
  unit?: string;
  source_quote?: string;
  confidence?: number;
  verification_status?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function isPublishedSource(value: unknown): value is PublishedSource {
  return isRecord(value) &&
    typeof value.title === 'string' &&
    (value.url === undefined || typeof value.url === 'string') &&
    (value.publisher === undefined || typeof value.publisher === 'string') &&
    (value.source_type === undefined || typeof value.source_type === 'string') &&
    (value.published_at === undefined || typeof value.published_at === 'string') &&
    (value.country === undefined || typeof value.country === 'string') &&
    (value.language === undefined || typeof value.language === 'string');
}

function isPublishedClaim(value: unknown): value is PublishedClaim {
  return isRecord(value) &&
    (value.subject === undefined || typeof value.subject === 'string') &&
    (value.predicate === undefined || typeof value.predicate === 'string') &&
    (value.value === undefined || value.value === null || typeof value.value === 'number' || typeof value.value === 'string') &&
    (value.min_value === undefined || typeof value.min_value === 'number') &&
    (value.max_value === undefined || typeof value.max_value === 'number') &&
    (value.unit === undefined || typeof value.unit === 'string') &&
    (value.source_quote === undefined || typeof value.source_quote === 'string') &&
    (value.confidence === undefined || typeof value.confidence === 'number') &&
    (value.verification_status === undefined || typeof value.verification_status === 'string');
}

function isPublishedEvent(value: unknown): value is PublishedEvent {
  if (!isRecord(value)) return false;
  const claims = value.claims;
  return typeof value.title === 'string' &&
    typeof value.event_type === 'string' &&
    typeof value.start_time === 'string' &&
    (value.publication_status === undefined ||
      value.publication_status === 'published' ||
      value.publication_status === 'published_with_warning') &&
    (value.sources === undefined || (Array.isArray(value.sources) && value.sources.every(isPublishedSource))) &&
    (claims === undefined ||
      (isRecord(claims) &&
        Object.values(claims).every(
          (claimList) => Array.isArray(claimList) && claimList.every(isPublishedClaim),
        )));
}

export function requireElement<K extends keyof HTMLElementTagNameMap>(
  id: string,
  tagName: K,
): HTMLElementTagNameMap[K] {
  const element = document.getElementById(id);
  const expectedConstructor = document.createElement(tagName).constructor;
  if (!element || !(element instanceof expectedConstructor)) {
    throw new Error(`Expected <${tagName}> element #${id}`);
  }
  return element as HTMLElementTagNameMap[K];
}

export function parsePublishedEvents(value: unknown): PublishedEvent[] {
  if (!Array.isArray(value) || !value.every(isPublishedEvent)) {
    throw new Error('Published event feed has an invalid format');
  }
  return value;
}

export function parsePublishedEvent(value: unknown): PublishedEvent {
  const events = parsePublishedEvents([value]);
  return events[0];
}

export function sitePath(path: string): string {
  const relativePath = path.replace(/^\/+/, '');
  return `${siteBase}${relativePath}`;
}

export function eventPath(slug: string | null | undefined): string | null {
  if (!slug || !/^[\w/-]+$/.test(slug) || slug.split('/').some((part) => !part || part === '.' || part === '..')) {
    return null;
  }
  return sitePath(`event/${slug.split('/').map(encodeURIComponent).join('/')}/`);
}

export function escapeHtml(value: unknown): string {
  return String(value ?? '').replace(/[&<>"']/g, (character) => {
    const entities: Record<string, string> = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    };
    return entities[character];
  });
}

export function safeExternalUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
  } catch {
    return null;
  }
}

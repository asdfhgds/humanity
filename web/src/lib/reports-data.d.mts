export interface ViolenceTypeSummary {
  id: number;
  label: string;
  count: number;
  share: number | null;
}

export interface CountrySummary {
  country: string;
  count: number;
  share: number | null;
}

export interface ResearchYearSummary {
  year: number;
  recordCount: number;
  recordsWithBestEstimate: number;
  bestEstimateCoverage: number | null;
  violenceTypes: ViolenceTypeSummary[];
  countries: CountrySummary[];
}

export interface ReportComparison {
  id: number;
  label: string;
  earlierCount: number;
  laterCount: number;
  change: number;
  changePercent?: number | null;
}

export interface ResearchReport {
  slug: string;
  title: string;
  description: string;
  publishedAt: string;
  years: number[];
  kind: "year-comparison" | "country-distribution" | "endpoint-comparison";
  datasetVersion: string;
  data: {
    [year: number]: ResearchYearSummary;
    comparison?: ReportComparison[];
    totalChange?: number;
    totalChangePercent?: number | null;
    elapsedYears?: number;
  };
  index: {
    coverage_start?: string;
    coverage_end?: string;
    retrieved_at?: string;
    dataset?: string;
    dataset_url?: string;
    codebook_url?: string;
    license?: string;
    license_url?: string;
    attribution?: string;
    notice?: string;
  };
}

export const REPORT_CATALOG: Array<
  Omit<ResearchReport, "datasetVersion" | "data" | "index">
>;
export function reportCanonicalUrl(
  pathname: string,
  basePath: string,
  site: URL,
): string;
export function buildResearchReports(
  index: unknown,
  feeds: Map<number, unknown> | Record<string, unknown>,
): ResearchReport[];
export function loadResearchReports(
  publicDirectory: string,
): Promise<ResearchReport[]>;

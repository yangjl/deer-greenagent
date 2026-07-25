import type {
  DbtlInventoryItem,
  DbtlReadinessClassification,
  DbtlReadinessReport,
} from "./api";

const CLASSIFICATIONS: DbtlReadinessClassification[] = [
  "compatible",
  "repairable",
  "invalid_or_ambiguous",
  "safe_to_supersede",
];

export function groupReadinessItems(items: DbtlInventoryItem[]) {
  return CLASSIFICATIONS.map((classification) => ({
    classification,
    items: items.filter((item) => item.classification === classification),
  }));
}

export function buildReadinessExport(report: DbtlReadinessReport) {
  const day = new Date().toISOString().slice(0, 10);
  return {
    filename: `greenagent-dbtl-readiness-${day}.json`,
    contents: `${JSON.stringify(report, null, 2)}\n`,
  };
}

export function isValidationStale(
  validatedAt: string | null | undefined,
  now = Date.now(),
  maxAgeMs = 24 * 60 * 60 * 1000,
) {
  if (!validatedAt) return true;
  const timestamp = Date.parse(validatedAt);
  return !Number.isFinite(timestamp) || now - timestamp > maxAgeMs;
}

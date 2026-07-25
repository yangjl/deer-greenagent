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

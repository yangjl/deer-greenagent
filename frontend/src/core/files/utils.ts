const FILE_SIZE_UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

/**
 * Human-readable file size (binary units, one decimal above bytes).
 * Returns an empty string for null/negative sizes so directory rows
 * can pass their `size: null` straight through.
 */
export function formatFileSize(size: number | null | undefined): string {
  if (size == null || size < 0 || !Number.isFinite(size)) {
    return "";
  }
  let value = size;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < FILE_SIZE_UNITS.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const rendered =
    unitIndex === 0 ? String(Math.round(value)) : value.toFixed(1);
  return `${rendered} ${FILE_SIZE_UNITS[unitIndex]}`;
}

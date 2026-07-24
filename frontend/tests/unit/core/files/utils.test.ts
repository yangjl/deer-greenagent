import { expect, test } from "@rstest/core";

import { formatFileSize } from "@/core/files/utils";

test("formatFileSize renders bytes without decimals", () => {
  expect(formatFileSize(0)).toBe("0 B");
  expect(formatFileSize(512)).toBe("512 B");
});

test("formatFileSize renders binary units with one decimal", () => {
  expect(formatFileSize(1024)).toBe("1.0 KB");
  expect(formatFileSize(1536)).toBe("1.5 KB");
  expect(formatFileSize(5 * 1024 * 1024)).toBe("5.0 MB");
  expect(formatFileSize(3 * 1024 * 1024 * 1024)).toBe("3.0 GB");
});

test("formatFileSize returns empty string for directories and bad input", () => {
  expect(formatFileSize(null)).toBe("");
  expect(formatFileSize(undefined)).toBe("");
  expect(formatFileSize(-1)).toBe("");
  expect(formatFileSize(Number.NaN)).toBe("");
});

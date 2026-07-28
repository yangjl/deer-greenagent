export type DesignDisagreementView = {
  topic: string;
  positions: string[];
  resolution: string;
};

export type DesignConsensusView = {
  summary: string;
  agreements: string[];
  disagreements: DesignDisagreementView[];
  openQuestions: string[];
  limitations: string[];
  failedChecks: string[];
  nextActions: string[];
};

export function structuredPackagePath(
  reviewPath: string,
  reviewMarkdown: string,
): string | null {
  const match =
    /^- Structured package:\s*`([^`\r\n]+)`\s*$/m.exec(reviewMarkdown);
  const filename = match?.[1]?.trim();
  if (!filename || filename.includes("/") || filename.includes("\\")) {
    return null;
  }
  const separator = reviewPath.lastIndexOf("/");
  return separator >= 0
    ? `${reviewPath.slice(0, separator + 1)}${filename}`
    : filename;
}

export function parseDesignConsensusPackage(
  content: string,
): DesignConsensusView | null {
  let payload: unknown;
  try {
    payload = JSON.parse(content);
  } catch {
    return null;
  }
  if (!isRecord(payload) || !Array.isArray(payload.results)) {
    return null;
  }
  const chair = payload.results.find(
    (result) =>
      isRecord(result) &&
      (result.capability === "design_council_chair" ||
        isRecord(result.consensus)),
  );
  if (!isRecord(chair) || !isRecord(chair.consensus)) {
    return null;
  }
  const consensus = chair.consensus;
  const agreements = strings(consensus.agreements);
  const disagreements = Array.isArray(consensus.disagreements)
    ? consensus.disagreements.flatMap((item) => {
        if (!isRecord(item)) {
          return [];
        }
        const positions = strings(item.positions);
        if (positions.length < 2) {
          return [];
        }
        return [
          {
            topic: text(item.topic) || "Contested point",
            positions,
            resolution: text(item.resolution),
          },
        ];
      })
    : [];
  const openQuestions = strings(consensus.open_questions);
  if (
    agreements.length === 0 &&
    disagreements.length === 0 &&
    openQuestions.length === 0
  ) {
    return null;
  }

  const failedChecks = Array.isArray(chair.quality_checks)
    ? chair.quality_checks.flatMap((check) => {
        if (!isRecord(check) || check.passed === true) {
          return [];
        }
        const name = text(check.name) || "Quality check";
        const detail = text(check.detail);
        return [detail ? `${name}: ${detail}` : name];
      })
    : [];

  return {
    summary: text(chair.summary),
    agreements,
    disagreements,
    openQuestions,
    limitations: strings(chair.limitations),
    failedChecks,
    nextActions: strings(chair.recommended_next_actions),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(text).filter(Boolean) : [];
}

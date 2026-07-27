import { describe, expect, it } from "@rstest/core";

import {
  isReviewDocumentUri,
  workspacePathOfArtifactUri,
} from "@/core/dbtl/artifact-path";

describe("resolving an artifact URI to a project path", () => {
  it("strips the sandbox outputs prefix", () => {
    expect(
      workspacePathOfArtifactUri(
        "/mnt/user-data/outputs/dbtl/cyc/design/stage-run-a-b.md",
      ),
    ).toBe("outputs/dbtl/cyc/design/stage-run-a-b.md");
  });

  it("strips the sandbox workspace prefix", () => {
    expect(
      workspacePathOfArtifactUri("/mnt/user-data/workspace/notes/plan.md"),
    ).toBe("notes/plan.md");
  });

  it("leaves an already-relative path alone", () => {
    expect(workspacePathOfArtifactUri("outputs/dbtl/x.md")).toBe(
      "outputs/dbtl/x.md",
    );
  });

  it("returns null for a URI that is not a workspace file", () => {
    // A dataset fingerprint or a bare cycle id is evidence, not something the
    // file API can serve; guessing a path would produce a broken viewer.
    expect(workspacePathOfArtifactUri("cycle-123")).toBe(null);
    expect(workspacePathOfArtifactUri("dataset:g2f:abc123")).toBe(null);
    expect(workspacePathOfArtifactUri("https://example.com/x.md")).toBe(null);
    expect(workspacePathOfArtifactUri("")).toBe(null);
  });

  it("refuses a path that escapes the project root", () => {
    expect(
      workspacePathOfArtifactUri("/mnt/user-data/outputs/../../etc/passwd"),
    ).toBe(null);
  });
});

describe("recognising a readable review document", () => {
  it("accepts markdown", () => {
    expect(
      isReviewDocumentUri("/mnt/user-data/outputs/dbtl/a/design/x.md"),
    ).toBe(true);
    expect(isReviewDocumentUri("outputs/x.MD")).toBe(true);
  });

  it("rejects the structured package and other kinds", () => {
    // The JSON stays available for audit, but it is not the reading surface.
    expect(
      isReviewDocumentUri("/mnt/user-data/outputs/dbtl/a/design/x.json"),
    ).toBe(false);
    expect(isReviewDocumentUri("cycle-123")).toBe(false);
    expect(isReviewDocumentUri("")).toBe(false);
  });
});

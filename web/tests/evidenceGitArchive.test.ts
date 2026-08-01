import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const root = path.resolve(__dirname, "../..");
const source = fs.readFileSync(
  path.join(root, "web/src/components/ComplianceEvidenceTab.tsx"),
  "utf8",
);

describe("root Evidence Git archive status", () => {
  it("shows non-secret durable state and keeps credential management in the TUI", () => {
    expect(source).toContain("/api/v1/admin/evidence/archive");
    expect(source).toContain("Fine-grained GitHub personal access token");
    expect(source).toContain("latest_archived_chain_head");
    expect(source).toContain("pull_request_head_sha");
    expect(source).toContain("Retry safe failed submission");
    expect(source).toContain("No token value or secret path is available");
    expect(source).not.toContain("github_pat_");
    expect(source).not.toContain("type=\"password\"");
  });
});

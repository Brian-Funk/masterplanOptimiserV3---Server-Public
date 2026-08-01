import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const testDir = path.dirname(fileURLToPath(import.meta.url));
const serverSrc = path.resolve(testDir, "..", "src");

function readSource(relativePath: string): string {
  return fs.readFileSync(path.join(serverSrc, relativePath), "utf8");
}

describe("web edit review placement", () => {
  it("does not mount the full review workflow on the broad admin page", () => {
    const adminPage = readSource(path.join("app", "admin", "page.tsx"));

    expect(adminPage).not.toContain("WebEditSummaryBar");
    expect(adminPage).not.toContain("<WebEditReviewModal");
  });

  it("mounts the review workflow from the schedule calendar context", () => {
    const calendarPage = readSource(path.join("app", "calendar", "page.tsx"));

    expect(calendarPage).toContain("ScheduleWebEditIndicator");
    expect(calendarPage).toContain("<WebEditReviewModal");
    expect(calendarPage).toContain("/web-edits");
  });
});

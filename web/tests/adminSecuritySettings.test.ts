/**
 * Static checks for root-admin security settings wiring.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

const serverRoot = path.resolve(__dirname, "..", "..");
const adminPagePath = path.join(
  serverRoot,
  "web",
  "src",
  "app",
  "admin",
  "page.tsx",
);

function adminPageSource(): string {
  return readFileSync(adminPagePath, "utf8");
}

describe("admin security settings", () => {
  it("shows the offline calendar window in its own security section", () => {
    const source = adminPageSource();

    expect(source).toContain('title: "Offline Access"');
    expect(source).toContain('"offline_access_ttl_hours"');
    expect(source).toContain("cached masterplan while offline");
  });

  it("limits the Security tab to root admins", () => {
    const source = adminPageSource();

    expect(source).toContain('["security", "privacy", "ha"].includes(tab)');
    expect(source).toContain("isRootAdmin={!!user?.is_root_admin}");
    expect(source).toContain("user?.is_root_admin");
    expect(source).toContain('tab === "security" && user?.is_root_admin');
  });

  it("groups both desktop publishing limits in the security panel", () => {
    const source = adminPageSource();

    expect(source).toContain('title: "Desktop Publishing"');
    expect(source).toContain('"masterplan_pushes_per_minute"');
    expect(source).toContain('"public_schedule_pushes_per_minute"');
  });

  it("exposes the configurable passkey request limit", () => {
    const source = adminPageSource();

    expect(source).toContain('title: "Passkeys"');
    expect(source).toContain('"passkey_requests_per_minute"');
    expect(source).toContain("limited per activation or account session");
  });
});

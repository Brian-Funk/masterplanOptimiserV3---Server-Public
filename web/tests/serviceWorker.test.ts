/**
 * Static checks for the PWA service worker shell fallback.
 */
import { describe, expect, it } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

const serverRoot = path.resolve(__dirname, "..", "..");
const swPath = path.join(serverRoot, "web", "public", "sw.js");
const outDir = path.join(serverRoot, "web", "out");

function serviceWorkerSource(): string {
  return readFileSync(swPath, "utf8");
}

function appShellEntries(source: string): string[] {
  const match = source.match(/const APP_SHELL = \[([\s\S]*?)\];/);
  if (!match) return [];
  return Array.from(match[1].matchAll(/"([^"]+)"/g)).map((entry) => entry[1]);
}

describe("service worker offline shell", () => {
  it("pre-caches only exported shell files", () => {
    const entries = appShellEntries(serviceWorkerSource());

    expect(entries).not.toContain("/offline");
    expect(entries).toContain("/calendar.html");
    expect(entries).toContain("/shared-schedule.html");
    for (const entry of entries) {
      expect(existsSync(path.join(outDir, entry.replace(/^\//, "")))).toBe(true);
    }
  });

  it("falls back calendar navigation to calendar.html", () => {
    const source = serviceWorkerSource();

    expect(source).toContain('"/calendar": "/calendar.html"');
    expect(source).toContain("networkFirstNavigation(event.request)");
    expect(source).not.toContain('caches.match("/")');
  });

  it("falls back shared schedule navigation to its exported shell", () => {
    const source = serviceWorkerSource();

    expect(source).toContain('"/shared-schedule": "/shared-schedule.html"');
  });

  it("never stores authenticated API responses in Cache Storage", () => {
    const source = serviceWorkerSource();

    expect(source).not.toContain('url.pathname.startsWith("/api/")');
    expect(source).not.toContain("mp-opt-offline-api");
    expect(source).not.toContain("cache.put(event.request");
  });
});

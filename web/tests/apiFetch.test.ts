/**
 * Tests for the apiFetch wrapper - CSRF, credentials, content-type.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock environment
vi.mock("@/lib/environment", () => ({
  getApiUrl: () => "https://api.test",
}));

// Mock fetch
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

// Simulate csrf_token cookie
Object.defineProperty(document, "cookie", {
  writable: true,
  value: "csrf_token=test-csrf-123",
});

import { apiFetch } from "@/lib/api";

beforeEach(() => {
  mockFetch.mockReset();
  mockFetch.mockResolvedValue(new Response("{}", { status: 200 }));
});

describe("apiFetch", () => {
  it("prepends API URL to path", async () => {
    await apiFetch("/api/v1/events");
    expect(mockFetch).toHaveBeenCalledWith(
      "https://api.test/api/v1/events",
      expect.any(Object),
    );
  });

  it("includes credentials", async () => {
    await apiFetch("/api/v1/events");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("adds CSRF header on POST", async () => {
    await apiFetch("/api/v1/events", { method: "POST", body: "{}" });
    const call = mockFetch.mock.calls[0];
    expect(call[1].headers["X-CSRF-Token"]).toBe("test-csrf-123");
  });

  it("adds CSRF header on DELETE", async () => {
    await apiFetch("/api/v1/events/1", { method: "DELETE" });
    const call = mockFetch.mock.calls[0];
    expect(call[1].headers["X-CSRF-Token"]).toBe("test-csrf-123");
  });

  it("does NOT add CSRF on GET", async () => {
    await apiFetch("/api/v1/events");
    const call = mockFetch.mock.calls[0];
    expect(call[1].headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("sets Content-Type for string body", async () => {
    await apiFetch("/api/v1/events", {
      method: "POST",
      body: JSON.stringify({ name: "test" }),
    });
    const call = mockFetch.mock.calls[0];
    expect(call[1].headers["Content-Type"]).toBe("application/json");
  });

  it("uses no-store cache", async () => {
    await apiFetch("/api/v1/events");
    const call = mockFetch.mock.calls[0];
    expect(call[1].cache).toBe("no-store");
  });

  it("adds CSRF header on PUT", async () => {
    await apiFetch("/api/v1/events/1", {
      method: "PUT",
      body: JSON.stringify({ name: "updated" }),
    });
    const call = mockFetch.mock.calls[0];
    expect(call[1].headers["X-CSRF-Token"]).toBe("test-csrf-123");
  });

  it("adds CSRF header on PATCH", async () => {
    await apiFetch("/api/v1/events/1", {
      method: "PATCH",
      body: JSON.stringify({ name: "patched" }),
    });
    const call = mockFetch.mock.calls[0];
    expect(call[1].headers["X-CSRF-Token"]).toBe("test-csrf-123");
  });

  it("does not override existing Content-Type header", async () => {
    await apiFetch("/api/v1/upload", {
      method: "POST",
      headers: { "Content-Type": "multipart/form-data" },
      body: "raw-data",
    });
    const call = mockFetch.mock.calls[0];
    expect(call[1].headers["Content-Type"]).toBe("multipart/form-data");
  });

  it("does not set Content-Type when body is not a string", async () => {
    const formData = new FormData();
    await apiFetch("/api/v1/upload", {
      method: "POST",
      body: formData,
    });
    const call = mockFetch.mock.calls[0];
    expect(call[1].headers["Content-Type"]).toBeUndefined();
  });

  it("returns the raw Response object", async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ id: 1 }), { status: 201 }),
    );
    const res = await apiFetch("/api/v1/events", {
      method: "POST",
      body: "{}",
    });
    expect(res).toBeInstanceOf(Response);
    expect(res.status).toBe(201);
  });

  it("handles empty CSRF cookie gracefully", async () => {
    const originalCookie = document.cookie;
    document.cookie = "";
    await apiFetch("/api/v1/events", { method: "POST", body: "{}" });
    const call = mockFetch.mock.calls[0];
    // Should not add an empty CSRF header
    expect(call[1].headers["X-CSRF-Token"]).toBeUndefined();
    document.cookie = originalCookie;
  });
});

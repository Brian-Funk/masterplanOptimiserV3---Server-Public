/**
 * Tests for reauth - passkey re-authentication ceremony and withReauth wrapper.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock environment
vi.mock("@/lib/environment", () => ({
  getApiUrl: () => "https://api.test",
}));

// Mock @simplewebauthn/browser
const mockStartAuthentication = vi.fn();
vi.mock("@simplewebauthn/browser", () => ({
  startAuthentication: (...args: unknown[]) => mockStartAuthentication(...args),
}));

// Mock fetch
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

// CSRF cookie
Object.defineProperty(document, "cookie", {
  writable: true,
  value: "csrf_token=reauth-csrf-456",
});

import { performReauth, withReauth } from "@/lib/reauth";

beforeEach(() => {
  mockFetch.mockReset();
  mockStartAuthentication.mockReset();
});

describe("performReauth", () => {
  it("completes the full reauth ceremony", async () => {
    // begin returns options
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ options: JSON.stringify({ challenge: "abc" }), ceremony_id: 91 }),
    });
    // startAuthentication returns a credential
    mockStartAuthentication.mockResolvedValueOnce({
      id: "cred-1",
      type: "public-key",
    });
    // complete succeeds
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => ({}) });

    await expect(performReauth()).resolves.toBeUndefined();

    // begin was called with POST
    expect(mockFetch.mock.calls[0][0]).toContain("/admin/reauth/begin");
    expect(mockFetch.mock.calls[0][1].method).toBe("POST");

    // startAuthentication was called with parsed options
    expect(mockStartAuthentication).toHaveBeenCalledWith({
      optionsJSON: { challenge: "abc" },
    });

    // complete was called with credential
    expect(mockFetch.mock.calls[1][0]).toContain("/admin/reauth/complete");
    const completeBody = JSON.parse(mockFetch.mock.calls[1][1].body);
    expect(completeBody.credential.id).toBe("cred-1");
    expect(completeBody.ceremony_id).toBe(91);
  });

  it("throws when begin endpoint fails", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      json: async () => ({ detail: "Not authorized" }),
    });

    await expect(performReauth()).rejects.toThrow("Not authorized");
    expect(mockStartAuthentication).not.toHaveBeenCalled();
  });

  it("throws with default message when begin returns no detail", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      json: async () => {
        throw new Error("no json");
      },
    });

    await expect(performReauth()).rejects.toThrow(
      "Failed to start re-authentication",
    );
  });

  it("throws when complete endpoint fails", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ options: JSON.stringify({ challenge: "abc" }) }),
    });
    mockStartAuthentication.mockResolvedValueOnce({ id: "cred-1" });
    mockFetch.mockResolvedValueOnce({
      ok: false,
      json: async () => ({ detail: "Verification failed" }),
    });

    await expect(performReauth()).rejects.toThrow("Verification failed");
  });

  it("propagates browser passkey error", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ options: JSON.stringify({ challenge: "abc" }) }),
    });
    mockStartAuthentication.mockRejectedValueOnce(new Error("User cancelled"));

    await expect(performReauth()).rejects.toThrow("User cancelled");
  });
});

describe("withReauth", () => {
  it("returns response directly when not 403", async () => {
    const action = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ ok: true }), { status: 200 }),
      );

    const res = await withReauth(action);
    expect(res.status).toBe(200);
    expect(action).toHaveBeenCalledTimes(1);
  });

  it("retries action after reauth on 403 with reauth-required detail", async () => {
    // First call: 403 reauth required
    const firstResponse = new Response(
      JSON.stringify({ detail: "Re-authentication required" }),
      { status: 403, headers: { "Content-Type": "application/json" } },
    );
    // Second call: success
    const secondResponse = new Response(JSON.stringify({ success: true }), {
      status: 200,
    });

    const action = vi
      .fn()
      .mockResolvedValueOnce(firstResponse)
      .mockResolvedValueOnce(secondResponse);

    // Set up reauth mocks
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ options: JSON.stringify({ challenge: "abc" }) }),
    });
    mockStartAuthentication.mockResolvedValueOnce({ id: "cred-1" });
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => ({}) });

    const res = await withReauth(action);
    expect(res.status).toBe(200);
    expect(action).toHaveBeenCalledTimes(2);
  });

  it("returns 403 as-is when detail is not reauth-required", async () => {
    const response = new Response(JSON.stringify({ detail: "Forbidden" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    });

    const action = vi.fn().mockResolvedValue(response);

    const res = await withReauth(action);
    expect(res.status).toBe(403);
    const body = await res.json();
    expect(body.detail).toBe("Forbidden");
    expect(action).toHaveBeenCalledTimes(1);
    // No reauth calls
    expect(mockStartAuthentication).not.toHaveBeenCalled();
  });

  it("propagates reauth error on retry", async () => {
    const response = new Response(
      JSON.stringify({ detail: "Re-authentication required" }),
      { status: 403, headers: { "Content-Type": "application/json" } },
    );

    const action = vi.fn().mockResolvedValue(response);

    // Reauth begin fails
    mockFetch.mockResolvedValueOnce({
      ok: false,
      json: async () => ({ detail: "Session expired" }),
    });

    await expect(withReauth(action)).rejects.toThrow("Session expired");
  });
});

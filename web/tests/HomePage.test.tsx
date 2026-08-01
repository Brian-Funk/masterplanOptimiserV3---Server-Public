/**
 * Tests for the root route redirect behaviour.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import React from "react";

const mockReplace = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
}));

function storeOfflineMarker(validUntil: string): void {
  localStorage.setItem(
    "mp-opt-offline-access",
    JSON.stringify({
      schema_version: 1,
      user_id: 42,
      event_id: 9,
      cached_at: "2026-05-21T09:30:00.000Z",
      valid_until: validUntil,
      ttl_hours: 24,
    }),
  );
}

describe("HomePage", () => {
  beforeEach(() => {
    mockReplace.mockReset();
    localStorage.clear();
  });

  it("routes through login when offline access is valid", async () => {
    storeOfflineMarker("2999-05-21T23:59:59.999Z");

    const { default: HomePage } = await import("@/app/page");
    render(<HomePage />);

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/login");
    });
  });

  it("redirects to login when no valid offline marker exists", async () => {
    const { default: HomePage } = await import("@/app/page");
    render(<HomePage />);

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/login");
    });
  });

  it("redirects to login when offline access expired", async () => {
    storeOfflineMarker("2000-05-21T23:59:59.999Z");

    const { default: HomePage } = await import("@/app/page");
    render(<HomePage />);

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/login");
    });
  });
});

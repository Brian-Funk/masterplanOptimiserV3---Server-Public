import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  canManagePublicScheduleLinks,
  PublicScheduleLinksTab,
} from "@/components/PublicScheduleLinksTab";

const mockApiFetch = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  apiFetch: mockApiFetch,
}));

const activeLink = {
  id: 5,
  event_id: 7,
  description: "Shared with the board",
  expires_at: "2026-08-08T12:00:00Z",
  invalidated_at: null,
  created_at: "2026-08-01T12:00:00Z",
  updated_at: null,
  created_by_id: 1,
  status: "active" as const,
  views: [{ id: 10, name: "Delegates", available: true }],
};

function jsonResponse(data: unknown, ok = true): Response {
  return {
    ok,
    status: ok ? 200 : 422,
    json: async () => data,
  } as Response;
}

function installApi(links: unknown[] = []) {
  mockApiFetch.mockImplementation(async (path: string, options?: RequestInit) => {
    if (path.endsWith("/general-schedule")) {
      return jsonResponse({
        schedule_views: [
          { id: 10, name: "Delegates", sort_order: 0 },
          { id: 11, name: "Officials", sort_order: 1 },
        ],
      });
    }
    if (options?.method === "POST" && !path.endsWith("/invalidate")) {
      return jsonResponse({
        ...activeLink,
        share_url: "/shared-schedule#token=one-time-token-value",
      });
    }
    if (options?.method === "PATCH" || options?.method === "DELETE") {
      return jsonResponse(activeLink);
    }
    return jsonResponse(links);
  });
}

describe("PublicScheduleLinksTab", () => {
  beforeEach(() => {
    mockApiFetch.mockReset();
  });

  it("shows the tab only for root administrators and issuers", () => {
    expect(canManagePublicScheduleLinks({ is_root_admin: true, is_issuer: false })).toBe(true);
    expect(canManagePublicScheduleLinks({ is_root_admin: false, is_issuer: true })).toBe(true);
    expect(canManagePublicScheduleLinks({ is_root_admin: false, is_issuer: false })).toBe(false);
    expect(canManagePublicScheduleLinks(null)).toBe(false);
  });

  it("requires an event before loading link management", () => {
    render(<PublicScheduleLinksTab eventId={null} />);

    expect(screen.getByText("Select an event to manage its public links.")).toBeInTheDocument();
    expect(mockApiFetch).not.toHaveBeenCalled();
  });

  it("creates a multi-view link and shows its URL once", async () => {
    installApi();
    const user = userEvent.setup();
    render(<PublicScheduleLinksTab eventId={7} />);

    await user.click(await screen.findByRole("button", { name: "New Link" }));
    await user.type(screen.getByLabelText("Internal description"), "Shared with chairs");
    await user.click(screen.getByRole("checkbox", { name: "Delegates" }));
    await user.click(screen.getByRole("checkbox", { name: "Officials" }));
    await user.click(screen.getByRole("button", { name: "Create Link" }));

    await waitFor(() => {
      expect(mockApiFetch).toHaveBeenCalledWith(
        "/api/v1/admin/events/7/public-schedule-links",
        expect.objectContaining({ method: "POST" }),
      );
    });
    const createCall = mockApiFetch.mock.calls.find(
      ([, options]) => options?.method === "POST",
    );
    expect(JSON.parse(createCall?.[1]?.body as string)).toMatchObject({
      description: "Shared with chairs",
      view_ids: [10, 11],
    });
    expect(await screen.findByText("Public link created")).toBeInTheDocument();
    expect(screen.getByLabelText("Generated public schedule URL")).toHaveValue(
      "http://localhost:3000/shared-schedule#token=one-time-token-value",
    );
  });

  it("edits permissions and invalidates an active link", async () => {
    installApi([activeLink]);
    const user = userEvent.setup();
    render(<PublicScheduleLinksTab eventId={7} />);

    await user.click(await screen.findByRole("button", { name: "Edit Shared with the board" }));
    await user.click(screen.getByRole("checkbox", { name: "Officials" }));
    await user.click(screen.getByRole("button", { name: "Save Changes" }));
    await waitFor(() =>
      expect(mockApiFetch).toHaveBeenCalledWith(
        "/api/v1/admin/events/7/public-schedule-links/5",
        expect.objectContaining({ method: "PATCH" }),
      ),
    );

    await user.click(screen.getByRole("button", { name: "Invalidate Shared with the board" }));
    await user.click(
      screen.getByRole("button", { name: "Confirm invalidating Shared with the board" }),
    );
    await waitFor(() =>
      expect(mockApiFetch).toHaveBeenCalledWith(
        "/api/v1/admin/events/7/public-schedule-links/5/invalidate",
        { method: "POST", body: "{}" },
      ),
    );
  });

  it("permanently deletes a managed link", async () => {
    installApi([activeLink]);
    const user = userEvent.setup();
    render(<PublicScheduleLinksTab eventId={7} />);

    await user.click(
      await screen.findByRole("button", {
        name: "Permanently delete Shared with the board",
      }),
    );
    await user.click(
      screen.getByRole("button", {
        name: "Confirm permanently deleting Shared with the board",
      }),
    );

    await waitFor(() =>
      expect(mockApiFetch).toHaveBeenCalledWith(
        "/api/v1/admin/events/7/public-schedule-links/5",
        { method: "DELETE" },
      ),
    );
  });

  it("marks a removed permitted view as unavailable", async () => {
    installApi([
      {
        ...activeLink,
        status: "unavailable",
        views: [{ id: 99, name: "Removed View", available: false }],
      },
    ]);
    render(<PublicScheduleLinksTab eventId={7} />);

    expect(await screen.findByText("unavailable")).toBeInTheDocument();
    expect(screen.getByText("Removed View")).toHaveClass("line-through");
  });
});

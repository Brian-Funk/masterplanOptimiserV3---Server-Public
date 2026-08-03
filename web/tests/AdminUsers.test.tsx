import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AdminPage from "@/app/admin/page";
import { deriveUsernameFromDisplayName } from "@/lib/adminUsers";

const mockApiFetch = vi.hoisted(() => vi.fn());
const mockPush = vi.hoisted(() => vi.fn());
const mockReplace = vi.hoisted(() => vi.fn());
const mockUseAuth = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  apiFetch: mockApiFetch,
}));

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: mockUseAuth,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: mockReplace }),
}));

vi.mock("@/components/PasskeyManager", () => ({
  PasskeyManager: () => null,
}));

vi.mock("@/components/Logo", () => ({
  Logo: () => <div>Logo</div>,
}));

vi.mock("@/components/ThemeToggle", () => ({
  ThemeToggle: () => <button type="button">Theme</button>,
}));

const issuerUser = {
  id: 10,
  username: "issuer",
  display_name: "Issuer",
  email: null,
  is_root_admin: false,
  is_admin: false,
  is_issuer: true,
  can_edit: false,
  is_active: true,
  is_activated: true,
  linked_person_id: null,
  event_id: 7,
};

const rootUser = {
  ...issuerUser,
  username: "root",
  display_name: "Root",
  is_root_admin: true,
  is_admin: true,
  is_issuer: false,
};

const event = {
  id: 7,
  name: "OWIII",
  location: null,
  start_date: null,
  end_date: null,
  status: "draft",
  created_at: null,
};

function jsonResponse(data: unknown, ok = true): Response {
  return {
    ok,
    status: ok ? 200 : 422,
    json: async () => data,
  } as Response;
}

describe("Admin users", () => {
  beforeEach(() => {
    mockApiFetch.mockReset();
    mockPush.mockReset();
    mockReplace.mockReset();
    mockUseAuth.mockReturnValue({
      user: rootUser,
      logout: vi.fn(),
      isLoading: false,
    });
  });

  it("derives usernames from display names", () => {
    expect(deriveUsernameFromDisplayName("Alpha Tester")).toBe("alpha.tester");
    expect(deriveUsernameFromDisplayName("  Alpha   Tester  ")).toBe(
      "alpha.tester",
    );
  });

  it("does not redirect while authentication is being rechecked", () => {
    mockUseAuth.mockReturnValue({
      user: null,
      logout: vi.fn(),
      isLoggingOut: false,
      isLoading: false,
      authStatus: "checking",
    });

    render(<AdminPage />);

    expect(mockReplace).not.toHaveBeenCalled();
  });

  it("never strands a participant on the administration loading state", async () => {
    mockUseAuth.mockReturnValue({
      user: {
        ...issuerUser,
        is_issuer: false,
        event_id: 7,
      },
      logout: vi.fn(),
      isLoading: false,
      authStatus: "authenticated",
    });

    render(<AdminPage />);

    expect(
      screen.getByRole("heading", { name: "Administration is not available" }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(mockReplace).toHaveBeenCalledWith("/calendar?event=7"),
    );
  });

  it("keeps an explicit All events context when only one event exists", async () => {
    mockApiFetch.mockImplementation(async (path: string) => {
      if (path === "/api/v1/admin/events") return jsonResponse([event]);
      return jsonResponse([]);
    });

    const user = userEvent.setup();
    render(<AdminPage />);

    await user.click(await screen.findByRole("button", { name: "Users" }));
    const eventContext = await screen.findByLabelText("Event context");
    await user.selectOptions(eventContext, "7");
    expect(eventContext).toHaveValue("7");
    await user.selectOptions(eventContext, "");
    expect(eventContext).toHaveValue("");
  });

  it("shows one calm account-settings area without repeating summary details", async () => {
    const managedUser = {
      ...rootUser,
      id: 22,
      username: "participant.a",
      display_name: "Participant A",
      email: "participant-a@example.com",
      is_root_admin: false,
      is_admin: false,
      can_edit: true,
      event_id: 7,
      tags: ["phase3"],
      is_active: true,
      is_activated: false,
    };
    mockApiFetch.mockImplementation(async (path: string) => {
      if (path === "/api/v1/admin/events") return jsonResponse([event]);
      if (path === "/api/v1/admin/users") return jsonResponse([managedUser]);
      return jsonResponse([]);
    });

    const user = userEvent.setup();
    render(<AdminPage />);
    await user.click(await screen.findByRole("button", { name: "Users" }));
    await user.click(await screen.findByTitle("Account details"));

    expect(screen.getByText("Account settings")).toBeInTheDocument();
    expect(screen.getByDisplayValue("participant-a@example.com")).toBeInTheDocument();
    expect(screen.queryByTitle("Show people tagged phase3")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export user data" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove or delete account" })).toBeInTheDocument();
  });

  it("shows the stored expiry for an individually generated activation link", async () => {
    const managedUser = {
      ...rootUser,
      id: 23,
      username: "pending.user",
      display_name: "Pending User",
      is_root_admin: false,
      is_admin: false,
      event_id: 7,
      is_active: true,
      is_activated: false,
      has_valid_email: false,
    };
    mockApiFetch.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === "/api/v1/admin/events") return jsonResponse([event]);
      if (path === "/api/v1/admin/users" && !options?.method) {
        return jsonResponse([managedUser]);
      }
      if (
        path === "/api/v1/admin/users/23/activation-link" &&
        options?.method === "POST"
      ) {
        return jsonResponse({
          activation_url: "/activate#token=pending-token",
          expires_at: "2030-12-31T23:59:00Z",
          purpose: "initial_setup",
        });
      }
      return jsonResponse([]);
    });

    const user = userEvent.setup();
    render(<AdminPage />);
    await user.click(await screen.findByRole("button", { name: "Users" }));
    await user.click(await screen.findByTitle("Generate activation link"));

    expect(await screen.findByText(/Expires .*your local time/)).toBeInTheDocument();
  });

  it("lets an issuer create a user without sending an event id", async () => {
    mockUseAuth.mockReturnValue({
      user: issuerUser,
      logout: vi.fn(),
      isLoading: false,
    });
    mockApiFetch.mockImplementation(
      async (path: string, options?: RequestInit) => {
        if (options?.method === "POST" && path === "/api/v1/admin/users") {
          return jsonResponse({
            user: {
              ...issuerUser,
              id: 21,
              username: "alpha.tester",
              display_name: "Alpha Tester",
              is_issuer: false,
              event_id: 7,
              tags: [],
              has_activation_link: true,
              last_activation_link_created_at: null,
              last_activation_at: null,
              last_login_at: null,
              deletion_requested_at: null,
            },
            activation_url: "/activate#token=abc",
            expires_at: "2030-12-31T23:59:00Z",
          });
        }
        return jsonResponse([]);
      },
    );

    const user = userEvent.setup();
    render(<AdminPage />);

    await user.click(await screen.findByRole("button", { name: "New User" }));
    await user.type(screen.getByLabelText("Username"), "alpha.tester");
    await user.type(screen.getByLabelText("Display name"), "Alpha Tester");
    await user.click(screen.getByRole("button", { name: "Create User" }));

    await waitFor(() => {
      const createCall = mockApiFetch.mock.calls.find(
        ([path, options]) =>
          path === "/api/v1/admin/users" && options?.method === "POST",
      );
      expect(createCall).toBeTruthy();
      expect(JSON.parse(createCall?.[1]?.body as string)).not.toHaveProperty(
        "event_id",
      );
    });

    expect(await screen.findByText("Activation link ready")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy link" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show QR code" })).toBeInTheDocument();
    expect(screen.getByText(/Expires .*your local time/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("Activation link ready")).not.toBeInTheDocument();
  });

  it("bulk creates users with derived usernames and bulk tags", async () => {
    mockApiFetch.mockImplementation(
      async (path: string, options?: RequestInit) => {
        if (path === "/api/v1/admin/events") return jsonResponse([event]);
        if (path === "/api/v1/admin/users" && !options?.method) {
          return jsonResponse([]);
        }
        if (
          path === "/api/v1/admin/users/bulk" &&
          options?.method === "POST"
        ) {
          return jsonResponse({
            created: [
              {
                ...rootUser,
                id: 31,
                username: "alpha.tester",
                display_name: "Alpha Tester",
                is_root_admin: false,
                is_admin: false,
                event_id: 7,
                tags: ["board"],
              },
            ],
            errors: [],
          });
        }
        return jsonResponse([]);
      },
    );

    const user = userEvent.setup();
    render(<AdminPage />);

    await user.click(await screen.findByRole("button", { name: "Users" }));
    await user.click(await screen.findByRole("button", { name: "Bulk Users" }));
    await user.selectOptions(screen.getByLabelText("Event"), "7");
    await user.type(
      screen.getAllByPlaceholderText("Alpha Tester")[0],
      "Alpha Tester",
    );
    expect(screen.getAllByPlaceholderText("alpha.tester")[0]).toHaveValue(
      "alpha.tester",
    );
    await user.type(screen.getByPlaceholderText("e.g. board, late arrival"), "board");
    await user.click(screen.getByRole("button", { name: "Apply" }));
    await user.click(screen.getByRole("button", { name: "Create Users" }));

    await waitFor(() => {
      const bulkCall = mockApiFetch.mock.calls.find(
        ([path, options]) =>
          path === "/api/v1/admin/users/bulk" && options?.method === "POST",
      );
      expect(bulkCall).toBeTruthy();
      expect(JSON.parse(bulkCall?.[1]?.body as string)).toMatchObject({
        event_id: 7,
        bulk_tags: ["board"],
        users: [
          {
            username: "alpha.tester",
            display_name: "Alpha Tester",
            tags: ["board"],
          },
        ],
      });
    });
  });
});

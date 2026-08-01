import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AccountSecurityPage from "@/app/account/security/page";

const mockApiFetch = vi.hoisted(() => vi.fn());
const mockPush = vi.hoisted(() => vi.fn());
const mockReplace = vi.hoisted(() => vi.fn());
const mockUseAuth = vi.hoisted(() => vi.fn());
const mockRouter = vi.hoisted(() => ({
  push: mockPush,
  replace: mockReplace,
}));

vi.mock("@/lib/api", () => ({ apiFetch: mockApiFetch }));
vi.mock("@/contexts/AuthContext", () => ({ useAuth: mockUseAuth }));
vi.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
}));
vi.mock("@/components/Logo", () => ({ Logo: () => <div>Logo</div> }));
vi.mock("@/components/ThemeToggle", () => ({ ThemeToggle: () => null }));

const account = {
  id: 7,
  username: "participant",
  display_name: "Participant",
  email: null,
  is_root_admin: false,
  is_admin: false,
  is_issuer: false,
  can_edit: false,
  is_active: true,
  is_activated: true,
  linked_person_id: 12,
  event_id: 3,
  offline_access_ttl_hours: 24,
};

const sessions = [
  {
    id: 11,
    current: true,
    device: "Chrome on Windows",
    created_at: "2026-07-30T10:00:00Z",
    last_seen_at: "2026-07-30T11:00:00Z",
    expires_at: "2026-07-30T18:00:00Z",
  },
  {
    id: 12,
    current: false,
    device: "Firefox on Linux",
    created_at: "2026-07-29T10:00:00Z",
    last_seen_at: null,
    expires_at: "2026-07-30T17:00:00Z",
  },
];

function jsonResponse(data: unknown, ok = true): Response {
  return {
    ok,
    status: ok ? 200 : 422,
    json: async () => data,
  } as Response;
}

describe("Account security page", () => {
  beforeEach(() => {
    mockApiFetch.mockReset();
    mockPush.mockReset();
    mockReplace.mockReset();
    mockUseAuth.mockReturnValue({ user: account, isLoading: false });
    mockApiFetch.mockResolvedValue(jsonResponse(sessions));
  });

  it("shows only coarse active-session metadata", async () => {
    render(<AccountSecurityPage />);

    expect(await screen.findByText("Chrome on Windows")).toBeInTheDocument();
    expect(screen.getByText("Firefox on Linux")).toBeInTheDocument();
    expect(screen.getByText("Current")).toBeInTheDocument();
    expect(screen.getByText(/raw IP details are not shown/i)).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith("/api/v1/auth/sessions");
  });

  it("revokes another session and removes it from the list", async () => {
    const user = userEvent.setup();
    mockApiFetch
      .mockResolvedValueOnce(jsonResponse(sessions))
      .mockResolvedValueOnce(jsonResponse({ revoked: true, current: false }));
    render(<AccountSecurityPage />);

    await screen.findByText("Firefox on Linux");
    await user.click(screen.getByRole("button", { name: "Revoke" }));

    await waitFor(() => {
      expect(mockApiFetch).toHaveBeenCalledWith("/api/v1/auth/sessions/12", {
        method: "DELETE",
      });
      expect(screen.queryByText("Firefox on Linux")).not.toBeInTheDocument();
    });
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it("returns to login when the current session is revoked", async () => {
    const user = userEvent.setup();
    mockApiFetch
      .mockResolvedValueOnce(jsonResponse(sessions))
      .mockResolvedValueOnce(jsonResponse({ revoked: true, current: true }));
    render(<AccountSecurityPage />);

    await screen.findByText("Chrome on Windows");
    await user.click(screen.getByRole("button", { name: "Revoke and log out" }));

    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/login"));
  });
});

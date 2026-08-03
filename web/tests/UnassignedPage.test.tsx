import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import UnassignedPage from "@/app/unassigned/page";

const replace = vi.fn();
const push = vi.fn();
const logout = vi.fn();
const auth = vi.hoisted(() => ({
  user: {
    id: 7,
    username: "unassigned",
    display_name: "Aurora Unassigned User",
    email: "unassigned@example.test",
    is_root_admin: false,
    is_admin: false,
    is_issuer: false,
    can_edit: false,
    is_active: true,
    is_activated: true,
    linked_person_id: null,
    event_id: null as number | null,
    offline_access_ttl_hours: 24,
  },
  isLoading: false,
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, push }) }));
vi.mock("@/components/Logo", () => ({ Logo: () => <div>Masterplan</div> }));
vi.mock("@/components/ThemeToggle", () => ({ ThemeToggle: () => <button>Theme</button> }));
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ ...auth, logout }),
}));

describe("UnassignedPage", () => {
  beforeEach(() => {
    replace.mockReset();
    push.mockReset();
    logout.mockReset();
    auth.user.event_id = null;
    auth.user.is_admin = false;
    auth.user.is_root_admin = false;
  });

  it("explains the authenticated unassigned state without admin controls", () => {
    render(<UnassignedPage />);

    expect(screen.getByRole("heading", { name: "Waiting for event assignment" })).toBeInTheDocument();
    expect(screen.getByText(/account is active, but it has not been assigned/i)).toBeInTheDocument();
    expect(screen.getByText(/Aurora Unassigned User/)).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("signs out through the shared authentication boundary", async () => {
    logout.mockResolvedValue(true);
    render(<UnassignedPage />);

    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
  });

  it("exposes the signed-in account security controls", async () => {
    render(<UnassignedPage />);

    await userEvent.click(screen.getByRole("button", { name: "Account security" }));

    expect(push).toHaveBeenCalledWith("/account/security");
  });

  it("redirects an assigned user to their calendar", async () => {
    auth.user.event_id = 42;
    render(<UnassignedPage />);

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/calendar?event=42"));
  });
});

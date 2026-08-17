import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MorePage from "@/app/more/page";

const mockReplace = vi.hoisted(() => vi.fn());
const mockUseAuth = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
}));
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: mockUseAuth,
}));
vi.mock("@/components/ThemeToggle", () => ({ ThemeToggle: () => <span>Theme control</span> }));
vi.mock("@/components/NotificationBell", () => ({ NotificationBell: () => <span>Notification control</span> }));
vi.mock("@/components/OfflineScheduleSettings", () => ({ OfflineScheduleSettings: () => <section>Offline control</section> }));
vi.mock("@/components/DeleteMyDataLink", () => ({ DeleteMyDataLink: () => <a href="/delete">Delete my data</a> }));

describe("More page", () => {
  beforeEach(() => {
    mockReplace.mockReset();
    mockUseAuth.mockReturnValue({
      user: {
        id: 4,
        event_id: 7,
        is_root_admin: false,
        is_admin: false,
        is_issuer: true,
      },
      isLoading: false,
      isLoggingOut: false,
      logout: vi.fn(),
    });
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      json: async () => ({
        configured: true,
        version: 1,
        content_sha256: "f1437362f8f5" + "0".repeat(52),
      }),
    })));
  });

  it("keeps issuer secondary routes and the exact permitted-data policy in More", async () => {
    render(<MorePage />);

    expect(screen.getByRole("link", { name: "History" })).toHaveAttribute(
      "href",
      "/admin?tab=history&event=7",
    );
    expect(screen.getByRole("link", { name: "Public links" })).toHaveAttribute(
      "href",
      "/admin?tab=public-links&event=7",
    );
    await waitFor(() => expect(
      screen.getByRole("link", { name: /Permitted-data policy v1/ }),
    ).toHaveAttribute(
      "href",
      "/api/v1/governance/public/versions/1/data-policy.html",
    ));
    expect(screen.getByText("f1437362f8f5...")).toBeInTheDocument();
  });

  it("does not offer public-link management to a non-issuer admin", () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: 5,
        event_id: 7,
        is_root_admin: false,
        is_admin: true,
        is_issuer: false,
      },
      isLoading: false,
      isLoggingOut: false,
      logout: vi.fn(),
    });

    render(<MorePage />);
    expect(screen.getByRole("link", { name: "History" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Public links" })).toBeNull();
  });
});

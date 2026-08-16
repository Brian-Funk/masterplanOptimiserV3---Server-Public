/** Phone navigation and action-sheet behaviour. */
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MobileActionSheet } from "@/components/MobileActionSheet";
import { MobileBottomNavigation } from "@/components/MobileBottomNavigation";
import { AuthenticatedMobileShell } from "@/components/AuthenticatedMobileShell";

const shell = vi.hoisted(() => ({
  pathname: "/calendar",
  search: new URLSearchParams("event=7&view=all"),
  push: vi.fn(),
  user: null as null | Record<string, unknown>,
}));

vi.mock("next/navigation", () => ({
  usePathname: () => shell.pathname,
  useSearchParams: () => shell.search,
  useRouter: () => ({ push: shell.push }),
}));
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: shell.user, isLoading: false }),
}));

describe("MobileBottomNavigation", () => {
  it("renders at most four destinations and exposes the active destination", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <MobileBottomNavigation
        items={[
          { id: "schedule", label: "Schedule", icon: <span>S</span>, active: true, onSelect },
          { id: "people", label: "People", icon: <span>P</span>, onSelect },
          { id: "updates", label: "Updates", icon: <span>U</span>, onSelect },
          { id: "more", label: "More", icon: <span>M</span>, onSelect },
          { id: "hidden", label: "Hidden", icon: <span>H</span>, onSelect },
        ]}
      />,
    );

    expect(screen.getByRole("button", { name: /schedule/i })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.queryByRole("button", { name: /hidden/i })).toBeNull();
    await user.click(screen.getByRole("button", { name: /people/i }));
    expect(onSelect).toHaveBeenCalledOnce();
  });
});

describe("AuthenticatedMobileShell", () => {
  const baseUser = {
    id: 1, event_id: 7, linked_person_id: 41,
    is_root_admin: false, is_admin: false, is_issuer: false, can_edit: false,
  };

  it("uses stable participant destinations and deterministic calendar URLs", async () => {
    shell.user = baseUser;
    shell.pathname = "/calendar";
    shell.search = new URLSearchParams("event=7&view=all");
    shell.push.mockReset();
    const user = userEvent.setup();
    render(<AuthenticatedMobileShell />);

    expect(screen.getByRole("button", { name: "Schedule" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "My schedule" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Programme" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "More" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "My schedule" }));
    expect(shell.push).toHaveBeenCalledWith("/calendar?event=7&view=mine");
  });

  it("omits My schedule for an unlinked editor", () => {
    shell.user = { ...baseUser, linked_person_id: null, can_edit: true };
    render(<AuthenticatedMobileShell />);
    expect(screen.queryByRole("button", { name: "My schedule" })).toBeNull();
    expect(screen.getAllByRole("button")).toHaveLength(3);
  });

  it("keeps the linked person's schedule selected for older calendar links", () => {
    shell.user = baseUser;
    shell.pathname = "/calendar";
    shell.search = new URLSearchParams("event=7");
    render(<AuthenticatedMobileShell />);
    expect(screen.getByRole("button", { name: "My schedule" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("keeps the issuer destinations stable on secondary pages", async () => {
    shell.user = { ...baseUser, is_issuer: true };
    shell.pathname = "/account/security";
    shell.search = new URLSearchParams("event=7");
    shell.push.mockReset();
    const user = userEvent.setup();
    render(<AuthenticatedMobileShell />);

    expect(screen.getByRole("button", { name: "More" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "People" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Updates" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "People" }));
    expect(shell.push).toHaveBeenCalledWith("/admin?tab=users&event=7");
  });

  it("keeps root administration outside the phone shell", () => {
    shell.user = { ...baseUser, is_root_admin: true, is_admin: true };
    const { container } = render(<AuthenticatedMobileShell />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("MobileActionSheet", () => {
  it("labels the dialog, locks background scrolling and closes on Escape", () => {
    const onClose = vi.fn();
    const { unmount } = render(
      <MobileActionSheet open title="View and filters" onClose={onClose}>
        <button type="button">Done</button>
      </MobileActionSheet>,
    );

    expect(screen.getByRole("dialog", { name: "View and filters" })).toBeInTheDocument();
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
    unmount();
    expect(document.body.style.overflow).toBe("");
  });
});

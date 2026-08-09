import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminNavigation } from "@/components/AdminNavigation";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

describe("AdminNavigation", () => {
  beforeEach(() => push.mockReset());

  it("organises every root page into four calm groups", () => {
    render(
      <AdminNavigation
        active="privacy"
        isRootAdmin
        isIssuerOnly={false}
        canManagePublicLinks
        onSelect={vi.fn()}
      />,
    );

    expect(screen.getAllByText("Operations").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Publishing").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Governance").length).toBeGreaterThan(0);
    expect(screen.getAllByText("System").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Policies & notices").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Trust & keys").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Deletion evidence").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Audit log").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("combobox")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Governance" })).toHaveAttribute("aria-current", "page");
  });

  it("preserves in-page tab selection and routes policies to its editor", () => {
    const onSelect = vi.fn();
    render(
      <AdminNavigation
        active="events"
        isRootAdmin
        isIssuerOnly={false}
        canManagePublicLinks
        onSelect={onSelect}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Publishing" }));
    expect(onSelect).toHaveBeenCalledWith("announcements");

    fireEvent.click(screen.getByRole("button", { name: "Governance" }));
    expect(push).toHaveBeenCalledWith("/admin/governance");
  });

  it("shows only permitted groups and pages to an issuer", () => {
    render(
      <AdminNavigation
        active="users"
        isRootAdmin={false}
        isIssuerOnly
        canManagePublicLinks
        onSelect={vi.fn()}
      />,
    );

    expect(screen.queryByText("Governance")).not.toBeInTheDocument();
    expect(screen.queryByText("System")).not.toBeInTheDocument();
    expect(screen.queryByText("Events")).not.toBeInTheDocument();
    expect(screen.getAllByText("Users").length).toBeGreaterThan(0);
    expect(screen.getByText("Issuer administration")).toBeInTheDocument();
    expect(screen.getAllByRole("combobox")).toHaveLength(1);
  });

  it("uses one grouped mobile selector for root pages", () => {
    const onSelect = vi.fn();
    render(
      <AdminNavigation
        active="events"
        isRootAdmin
        isIssuerOnly={false}
        canManagePublicLinks
        onSelect={onSelect}
      />,
    );

    fireEvent.change(screen.getByRole("combobox", { name: "Administration page" }), {
      target: { value: "privacy" },
    });
    expect(onSelect).toHaveBeenCalledWith("privacy");
  });
});

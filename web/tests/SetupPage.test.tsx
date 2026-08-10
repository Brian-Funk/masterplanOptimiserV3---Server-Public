import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockApiFetch = vi.hoisted(() => vi.fn());
const mockHardNavigate = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ apiFetch: mockApiFetch }));
vi.mock("@/lib/hardNavigation", () => ({ hardNavigate: mockHardNavigate }));
vi.mock("@/app/admin/governance/page", () => ({ GovernanceWorkspace: () => <div>Governance workspace</div> }));
vi.mock("@/components/Logo", () => ({ Logo: () => <div>Masterplan</div> }));
vi.mock("@/components/ThemeToggle", () => ({ ThemeToggle: () => <button type="button">Theme</button> }));

const completeStatus = {
  current_step: "complete",
  current_step_number: 3,
  total_steps: 3,
  percent_complete: 100,
  steps: [
    { id: "recovery", title: "Recovery key", number: 1, status: "complete", completed_at: "2026-08-04T10:00:00Z" },
    { id: "controller", title: "Controller identity", number: 2, status: "complete", completed_at: "2026-08-04T10:05:00Z" },
    { id: "governance", title: "Governance baseline", number: 3, status: "complete", completed_at: "2026-08-04T10:10:00Z" },
  ],
  next_action: { code: "enter_administration", message: "Commissioning is complete. Normal administration is available." },
  can_enter_administration: true,
  controller: { entity_id: "ctl-synthetic0001", key_id: "ek-1234567890abcdef", public_key_sha256: "a".repeat(64), trust_establishment_sha256: "b".repeat(64) },
  governance: { published: true, version: 1, content_sha256: "c".repeat(64) },
  commissioning: { completed_at: "2026-08-04T10:10:00Z", receipt_sha256: "d".repeat(64) },
};

describe("root commissioning setup", () => {
  beforeEach(() => {
    mockApiFetch.mockReset();
    mockHardNavigate.mockReset();
  });

  it("shows authoritative completion and only then offers administration", async () => {
    mockApiFetch.mockResolvedValue(new Response(JSON.stringify(completeStatus), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    const { default: SetupPage } = await import("@/app/setup/page");
    render(<SetupPage />);

    expect(await screen.findByRole("heading", { name: "Commissioning complete" })).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("ek-1234567890abcdef")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download commissioning report" })).toHaveAttribute("href", "/api/v1/setup/report.zip");
    fireEvent.click(screen.getByRole("button", { name: "Enter administration" }));
    expect(mockHardNavigate).toHaveBeenCalledWith("/admin");
  });

  it("returns an expired restricted session to login with a setup return path", async () => {
    mockApiFetch.mockResolvedValue(new Response("{}", { status: 401 }));
    const { default: SetupPage } = await import("@/app/setup/page");
    render(<SetupPage />);

    await waitFor(() => expect(mockHardNavigate).toHaveBeenCalledWith("/login?next=/setup"));
    expect(screen.queryByRole("button", { name: "Enter administration" })).not.toBeInTheDocument();
  });
});

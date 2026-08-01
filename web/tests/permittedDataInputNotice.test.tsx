import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PermittedDataInputNotice } from "@/components/PermittedDataInputNotice";

vi.mock("@/lib/environment", () => ({
  getApiUrl: () => "https://server.example",
}));

describe("PermittedDataInputNotice", () => {
  it("shows the full fail-safe warning and permanent exact-version link before acknowledgement", async () => {
    const user = userEvent.setup();
    render(
      <PermittedDataInputNotice
        acknowledged={false}
        version={7}
        sha256={"a".repeat(64)}
      />,
    );

    expect(screen.getByText("Operational information only")).toBeInTheDocument();
    expect(screen.getByText(/Do not enter health, dietary, safeguarding/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /exact permitted-data rules v7/i });
    expect(link).toHaveAttribute(
      "href",
      "https://server.example/api/v1/governance/public/versions/7/data-policy.html",
    );
    await user.tab();
    expect(link).toHaveFocus();
  });

  it("keeps a compact exact-version link and digest marker after acknowledgement", () => {
    render(
      <PermittedDataInputNotice
        acknowledged
        version={8}
        sha256={"0123456789abcdef".repeat(4)}
      />,
    );

    expect(screen.getByText(/Operational data only/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /permitted-data rules v8/i })).toHaveAttribute(
      "href",
      "https://server.example/api/v1/governance/public/versions/8/data-policy.html",
    );
    expect(screen.getByText("0123456789ab...")).toBeInTheDocument();
  });
});

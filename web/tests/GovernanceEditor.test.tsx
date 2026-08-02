import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GovernanceEditor } from "@/components/GovernanceEditor";
import { createInitialStructured } from "@/lib/governanceDraft";

describe("GovernanceEditor", () => {
  it("uses guided sections and links configurable retention to security settings", () => {
    render(<GovernanceEditor value={createInitialStructured({ smtp_enabled: true, push_enabled: false, ha_enabled: false, dns_mode: "dns_only" }, {})} onChange={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "3. Deployment and jurisdiction" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "4. Processing purposes and legal decisions" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "6. Providers, countries and transfers" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "7. Retention and enabled features" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Administration.*Security settings/ })).toHaveAttribute("href", "/admin?tab=security");
    expect(screen.getByText(/software does not infer jurisdiction/i)).toBeInTheDocument();
    expect(screen.queryByText(/physical deletion/i)).not.toBeInTheDocument();
  });
});

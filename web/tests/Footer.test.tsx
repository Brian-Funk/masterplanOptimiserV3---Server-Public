/**
 * Tests for Footer component.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";

// Mock next/link
vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    ...props
  }: {
    children: React.ReactNode;
    href: string;
  }) => React.createElement("a", { href, ...props }, children),
}));

// Mock DeleteMyDataLink - it has its own test file.
vi.mock("@/components/DeleteMyDataLink", () => ({
  DeleteMyDataLink: () => null,
}));

import { Footer } from "@/components/Footer";

describe("Footer", () => {
  it("identifies the self-hosted instance", () => {
    render(<Footer />);
    expect(
      screen.getByText("Masterplan Optimiser self-hosted instance"),
    ).toBeInTheDocument();
  });

  it("renders About link", () => {
    render(<Footer />);
    const link = screen.getByText("About");
    expect(link).toHaveAttribute("href", "/about");
  });

  it("renders Privacy link", () => {
    render(<Footer />);
    const link = screen.getByText("Privacy");
    expect(link).toHaveAttribute("href", "/privacy");
  });

  it("renders Legal link", () => {
    render(<Footer />);
    const link = screen.getByText("Legal");
    expect(link).toHaveAttribute("href", "/legal");
  });

  it("renders Disclaimer link", () => {
    render(<Footer />);
    const link = screen.getByText("Disclaimer");
    expect(link).toHaveAttribute("href", "/disclaimer");
  });

  it("renders footer element", () => {
    render(<Footer />);
    const footer = document.querySelector("footer");
    expect(footer).toBeInTheDocument();
  });

  it("renders version placeholder", () => {
    render(<Footer />);
    expect(screen.getByText(/^v/)).toBeInTheDocument();
  });
});

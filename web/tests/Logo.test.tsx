/**
 * Tests for Logo component.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";

// Mock ThemeContext
let currentTheme = "light";
vi.mock("@/contexts/ThemeContext", () => ({
  useTheme: () => ({ theme: currentTheme, toggleTheme: vi.fn() }),
}));

// Mock brand
vi.mock("@/lib/brand", () => ({
  BRAND: { color1: "#2563eb", color2: "#7c3aed" },
}));

import { Logo } from "@/components/Logo";

describe("Logo", () => {
  it("renders an image with alt text", () => {
    currentTheme = "light";
    render(<Logo />);
    expect(screen.getByAltText("Masterplan Optimiser")).toBeInTheDocument();
  });

  it("uses light logo in light mode", () => {
    currentTheme = "light";
    render(<Logo />);
    const img = screen.getByAltText("Masterplan Optimiser") as HTMLImageElement;
    expect(img.src).toContain("logo_normal.svg");
  });

  it("uses dark logo in dark mode", () => {
    currentTheme = "dark";
    render(<Logo />);
    const img = screen.getByAltText("Masterplan Optimiser") as HTMLImageElement;
    expect(img.src).toContain("logo_dark.svg");
  });

  it("applies custom height", () => {
    currentTheme = "light";
    render(<Logo height={100} />);
    const img = screen.getByAltText("Masterplan Optimiser") as HTMLImageElement;
    expect(img.style.height).toBe("100px");
  });

  it("wraps in link when href is provided", () => {
    currentTheme = "light";
    render(<Logo href="https://example.com" />);
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "https://example.com");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("does not wrap in link when href is not provided", () => {
    currentTheme = "light";
    render(<Logo />);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("applies custom className", () => {
    currentTheme = "light";
    const { container } = render(<Logo className="mt-4" />);
    const wrapper = container.querySelector(".relative.inline-block");
    expect(wrapper?.className).toContain("mt-4");
  });

  it("uses fixed brand colors", () => {
    currentTheme = "light";
    const { container } = render(<Logo />);
    const gradientDiv = container.querySelector(
      ".absolute.inset-0",
    ) as HTMLElement;
    expect(gradientDiv.style.background).toContain("#2563eb");
    expect(gradientDiv.style.background).toContain("#7c3aed");
  });
});

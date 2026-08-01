/**
 * Tests for ThemeToggle component.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

// Mock ThemeContext
const mockToggleTheme = vi.fn();
let currentTheme = "light";

vi.mock("@/contexts/ThemeContext", () => ({
  useTheme: () => ({ theme: currentTheme, toggleTheme: mockToggleTheme }),
}));

// Mock lucide-react icons
vi.mock("lucide-react", () => ({
  Moon: (props: Record<string, unknown>) =>
    React.createElement("svg", { "data-testid": "moon-icon", ...props }),
  Sun: (props: Record<string, unknown>) =>
    React.createElement("svg", { "data-testid": "sun-icon", ...props }),
}));

import { ThemeToggle } from "@/components/ThemeToggle";

describe("ThemeToggle", () => {
  it("renders a button with toggle theme label", () => {
    currentTheme = "light";
    render(<ThemeToggle />);
    expect(
      screen.getByRole("button", { name: /toggle theme/i }),
    ).toBeInTheDocument();
  });

  it("shows Moon icon in light mode", () => {
    currentTheme = "light";
    render(<ThemeToggle />);
    expect(screen.getByTestId("moon-icon")).toBeInTheDocument();
    expect(screen.queryByTestId("sun-icon")).toBeNull();
  });

  it("shows Sun icon in dark mode", () => {
    currentTheme = "dark";
    render(<ThemeToggle />);
    expect(screen.getByTestId("sun-icon")).toBeInTheDocument();
    expect(screen.queryByTestId("moon-icon")).toBeNull();
  });

  it("calls toggleTheme on click", async () => {
    currentTheme = "light";
    const user = userEvent.setup();
    render(<ThemeToggle />);
    await user.click(screen.getByRole("button"));
    expect(mockToggleTheme).toHaveBeenCalledOnce();
  });
});

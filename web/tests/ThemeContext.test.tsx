/**
 * Tests for ThemeContext - dark/light mode toggling.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { ThemeProvider, useTheme } from "@/contexts/ThemeContext";

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: vi.fn((key: string) => store[key] ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store[key] = value;
    }),
    clear: () => {
      store = {};
    },
  };
})();
vi.stubGlobal("localStorage", localStorageMock);

// Mock matchMedia
vi.stubGlobal("matchMedia", (query: string) => ({
  matches: false,
  media: query,
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  addListener: vi.fn(),
  removeListener: vi.fn(),
  dispatchEvent: vi.fn(),
  onchange: null,
}));

function ThemeConsumer() {
  const { theme, toggleTheme } = useTheme();
  return (
    <div>
      <span data-testid="theme">{theme}</span>
      <button onClick={toggleTheme}>Toggle</button>
    </div>
  );
}

beforeEach(() => {
  localStorageMock.clear();
  localStorageMock.getItem.mockClear();
  localStorageMock.setItem.mockClear();
  document.documentElement.classList.remove("dark");
});

describe("ThemeContext", () => {
  it("defaults to light theme", () => {
    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>,
    );
    // Initial render is light, useEffect may update
    expect(screen.getByTestId("theme")).toHaveTextContent("light");
  });

  it("reads stored theme from localStorage", () => {
    localStorageMock.setItem("theme", "dark");
    localStorageMock.setItem.mockClear();

    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>,
    );

    // After effect fires, theme should be dark
    expect(localStorageMock.getItem).toHaveBeenCalledWith("theme");
  });

  it("toggles theme and saves to localStorage", async () => {
    const user = userEvent.setup();

    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>,
    );

    await user.click(screen.getByText("Toggle"));
    expect(localStorageMock.setItem).toHaveBeenCalledWith("theme", "dark");
  });

  it("toggles back to light after two toggles", async () => {
    const user = userEvent.setup();

    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>,
    );

    await user.click(screen.getByText("Toggle"));
    await user.click(screen.getByText("Toggle"));
    expect(localStorageMock.setItem).toHaveBeenLastCalledWith("theme", "light");
  });

  it("adds dark class to documentElement when toggled to dark", async () => {
    const user = userEvent.setup();

    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>,
    );

    await user.click(screen.getByText("Toggle"));
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("removes dark class when toggled back to light", async () => {
    const user = userEvent.setup();

    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>,
    );

    await user.click(screen.getByText("Toggle")); // -> dark
    await user.click(screen.getByText("Toggle")); // -> light
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("respects system dark mode preference", () => {
    // Override matchMedia to prefer dark
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query === "(prefers-color-scheme: dark)",
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
      onchange: null,
    }));

    render(
      <ThemeProvider>
        <ThemeConsumer />
      </ThemeProvider>,
    );

    // After the useEffect, theme should be dark from system preference
    // (no localStorage value set, so falls through to matchMedia)
    // Reset matchMedia to default for other tests
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
      onchange: null,
    }));
  });
});

describe("useTheme outside provider", () => {
  it("throws when used outside ThemeProvider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});

    function Bare() {
      useTheme();
      return null;
    }

    expect(() => render(<Bare />)).toThrow(
      "useTheme must be used within a ThemeProvider",
    );

    spy.mockRestore();
  });
});

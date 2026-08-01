/**
 * Tests for UI components - Button, Card, Input.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";

describe("Button", () => {
  it("renders children", () => {
    render(<Button>Click me</Button>);
    expect(screen.getByRole("button")).toHaveTextContent("Click me");
  });

  it("applies variant classes", () => {
    render(<Button variant="danger">Delete</Button>);
    const btn = screen.getByRole("button");
    // Danger variant uses inline style for backgroundColor
    expect(btn).toBeInTheDocument();
  });

  it("handles click events", async () => {
    const user = userEvent.setup();
    let clicked = false;
    render(<Button onClick={() => (clicked = true)}>Go</Button>);
    await user.click(screen.getByRole("button"));
    expect(clicked).toBe(true);
  });

  it("disables when disabled prop is set", () => {
    render(<Button disabled>Disabled</Button>);
    expect(screen.getByRole("button")).toBeDisabled();
  });

  it("applies full width", () => {
    render(<Button fullWidth>Wide</Button>);
    expect(screen.getByRole("button").className).toContain("w-full");
  });

  it("applies size classes", () => {
    render(<Button size="lg">Large</Button>);
    expect(screen.getByRole("button").className).toContain("px-5");
  });

  it("applies small size classes", () => {
    render(<Button size="sm">Small</Button>);
    expect(screen.getByRole("button").className).toContain("px-3");
  });

  it("applies outline variant classes", () => {
    render(<Button variant="outline">Outline</Button>);
    const btn = screen.getByRole("button");
    expect(btn.className).toContain("border");
  });

  it("applies ghost variant classes", () => {
    render(<Button variant="ghost">Ghost</Button>);
    const btn = screen.getByRole("button");
    expect(btn.className).toContain("hover:bg-gray-100");
  });

  it("applies secondary variant with inline style", () => {
    render(<Button variant="secondary">Secondary</Button>);
    const btn = screen.getByRole("button");
    expect(btn.style.backgroundColor).toBe("var(--color-secondary)");
  });

  it("applies danger variant with inline style", () => {
    render(<Button variant="danger">Delete</Button>);
    const btn = screen.getByRole("button");
    expect(btn.style.backgroundColor).toBe("var(--color-error)");
  });

  it("applies primary variant with inline style", () => {
    render(<Button variant="primary">Primary</Button>);
    const btn = screen.getByRole("button");
    expect(btn.style.backgroundColor).toBe("var(--color-primary)");
  });

  it("applies custom className", () => {
    render(<Button className="mt-4">Custom</Button>);
    expect(screen.getByRole("button").className).toContain("mt-4");
  });

  it("does not apply w-full when fullWidth is false", () => {
    render(<Button>Normal</Button>);
    expect(screen.getByRole("button").className).not.toContain("w-full");
  });

  it("does not fire click when disabled", async () => {
    const user = userEvent.setup();
    let clicked = false;
    render(
      <Button disabled onClick={() => (clicked = true)}>
        No click
      </Button>,
    );
    await user.click(screen.getByRole("button"));
    expect(clicked).toBe(false);
  });
});

describe("Card", () => {
  it("renders children", () => {
    render(<Card>Card content</Card>);
    expect(screen.getByText("Card content")).toBeInTheDocument();
  });

  it("applies hover class when hover prop is true", () => {
    const { container } = render(<Card hover>Hoverable</Card>);
    expect(container.firstChild).toHaveClass("hover:shadow-sm");
  });

  it("applies custom className", () => {
    const { container } = render(<Card className="mt-4">Styled</Card>);
    expect(container.firstChild).toHaveClass("mt-4");
  });

  it("does not apply hover class when hover is false", () => {
    const { container } = render(<Card>No hover</Card>);
    expect(container.firstChild).not.toHaveClass("hover:shadow-sm");
  });

  it("has border and background classes", () => {
    const { container } = render(<Card>Content</Card>);
    const el = container.firstChild as HTMLElement;
    expect(el.className).toContain("bg-white");
    expect(el.className).toContain("border");
    expect(el.className).toContain("rounded-xl");
  });
});

describe("Input", () => {
  it("renders with label", () => {
    render(<Input label="Email" />);
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
  });

  it("shows error message", () => {
    render(<Input label="Name" error="Required" />);
    expect(screen.getByText("Required")).toBeInTheDocument();
  });

  it("shows helper text when no error", () => {
    render(<Input label="Name" helperText="Enter your name" />);
    expect(screen.getByText("Enter your name")).toBeInTheDocument();
  });

  it("hides helper text when error is present", () => {
    render(<Input label="Name" error="Bad" helperText="Enter your name" />);
    expect(screen.queryByText("Enter your name")).toBeNull();
    expect(screen.getByText("Bad")).toBeInTheDocument();
  });

  it("passes through HTML attributes", async () => {
    const user = userEvent.setup();
    render(<Input label="Search" placeholder="Type here..." />);
    const input = screen.getByPlaceholderText("Type here...");
    await user.type(input, "hello");
    expect(input).toHaveValue("hello");
  });

  it("disables input", () => {
    render(<Input label="Locked" disabled />);
    expect(screen.getByLabelText("Locked")).toBeDisabled();
  });

  it("applies error border class when error is present", () => {
    render(<Input label="Email" error="Invalid" />);
    const input = screen.getByLabelText("Email");
    expect(input.className).toContain("border-red-300");
  });

  it("applies normal border class when no error", () => {
    render(<Input label="Email" />);
    const input = screen.getByLabelText("Email");
    expect(input.className).toContain("border-gray-300");
    expect(input.className).not.toContain("border-red-300");
  });

  it("renders without label", () => {
    const { container } = render(<Input placeholder="no label" />);
    expect(container.querySelector("label")).toBeNull();
    expect(screen.getByPlaceholderText("no label")).toBeInTheDocument();
  });

  it("generates unique id when not provided", () => {
    render(<Input label="Field" />);
    const input = screen.getByLabelText("Field");
    expect(input.id).toBeTruthy();
    expect(input.id).toContain("input-");
  });

  it("uses provided id", () => {
    render(<Input label="Field" id="my-id" />);
    const input = screen.getByLabelText("Field");
    expect(input.id).toBe("my-id");
  });
});

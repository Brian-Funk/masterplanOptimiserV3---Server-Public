/** Phone navigation and action-sheet behaviour. */
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MobileActionSheet } from "@/components/MobileActionSheet";
import { MobileBottomNavigation } from "@/components/MobileBottomNavigation";

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

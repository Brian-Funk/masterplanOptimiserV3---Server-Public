import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

import { PublicScheduleCalendarGrid } from "@/components/PublicScheduleCalendarGrid";

function fireTouchPointerEvent(
  target: Element,
  type: "pointerdown" | "pointerup",
  { clientX, clientY }: { clientX: number; clientY: number },
) {
  const event = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    clientX,
    clientY,
  });
  Object.defineProperty(event, "pointerType", { value: "touch" });
  fireEvent(target, event);
}

describe("PublicScheduleCalendarGrid", () => {
  const detailedItem = {
    id: 1,
    title: "Opening Briefing",
    date: "2026-08-01",
    start_time: "09:00",
    end_time: "10:00",
    location_name: "Room A",
    location_address: "1 Parliament Square",
    responsible: "Session president",
    audience_teams: [{ name: "Delegates", short_name: "DEL" }],
    description: "Bring laptops.\nUse the front entrance.",
    type_name: "Briefing",
    colour: "#7dd3fc",
    sort_order: 0,
  };

  it("opens complete public details by double-click without expanding the card", async () => {
    const user = userEvent.setup();
    render(
      <PublicScheduleCalendarGrid
        selectedDate="2026-08-01"
        items={[detailedItem]}
      />,
    );

    const trigger = screen.getByRole("button", {
      name: "View details for Opening Briefing",
    });
    expect(screen.queryByText("1 Parliament Square")).not.toBeInTheDocument();
    expect(screen.queryByText(/Bring laptops/)).not.toBeInTheDocument();
    expect(screen.getByText("Auto-Fit")).toBeInTheDocument();

    await user.dblClick(trigger);

    expect(
      screen.getByRole("dialog", { name: "Opening Briefing" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Room A - 1 Parliament Square")).toBeInTheDocument();
    expect(screen.getByText("Session president")).toBeInTheDocument();
    expect(screen.getByText("Briefing")).toBeInTheDocument();
    expect(screen.getAllByText("DEL").length).toBeGreaterThan(0);
    expect(screen.getByText(/Bring laptops/)).toHaveClass("whitespace-pre-wrap");

    await user.click(
      screen.getByRole("button", { name: "Close schedule details" }),
    );
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("supports keyboard, backdrop and touch access while omitting empty fields", async () => {
    const user = userEvent.setup();
    render(
      <PublicScheduleCalendarGrid
        selectedDate="2026-08-01"
        items={[
          {
            ...detailedItem,
            location_name: null,
            location_address: " ",
            responsible: null,
            audience_teams: [],
            description: " ",
            type_name: null,
          },
        ]}
      />,
    );

    const trigger = screen.getByRole("button", {
      name: "View details for Opening Briefing",
    });
    trigger.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByText("Location")).not.toBeInTheDocument();
    expect(screen.queryByText("Responsible")).not.toBeInTheDocument();
    expect(screen.queryByText("Audience")).not.toBeInTheDocument();
    expect(screen.queryByText("Description")).not.toBeInTheDocument();
    expect(screen.queryByText("Type")).not.toBeInTheDocument();

    const backdrop = screen.getByRole("dialog").parentElement;
    expect(backdrop).not.toBeNull();
    fireEvent.mouseDown(backdrop!);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireTouchPointerEvent(trigger, "pointerdown", {
      clientX: 10,
      clientY: 10,
    });
    fireTouchPointerEvent(trigger, "pointerup", {
      clientX: 10,
      clientY: 10,
    });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders next-day items in the published overnight tail", () => {
    render(
      <PublicScheduleCalendarGrid
        selectedDate="2026-08-01"
        scheduleDayRange={{ start_hour: 6, end_hour: 30 }}
        items={[
          {
            ...detailedItem,
            date: "2026-08-02",
            working_date: "2026-08-01",
            start_time: "01:00",
            end_time: "02:00",
          },
        ]}
      />,
    );

    expect(screen.getByText("00:00 (+1)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View details for Opening Briefing" })).toBeInTheDocument();
  });
});

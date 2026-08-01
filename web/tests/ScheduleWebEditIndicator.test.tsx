import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

import { ScheduleWebEditIndicator } from "@/components/ScheduleWebEditIndicator";
import type { WebEditSummary } from "@/lib/webEditConfidence";

function summary(overrides: Partial<WebEditSummary> = {}): WebEditSummary {
  return {
    level: "review",
    edited_task_count: 2,
    last_edited_at: "2026-05-21T14:20:00",
    last_edited_by: "Anna",
    has_published_baseline: true,
    headline: "Review needed",
    description: "2 web edits since the last desktop publish.",
    items: [
      {
        task_id: 1,
        task_name: "Opening Briefing",
        day: "2026-05-21",
        start: "2026-05-21T09:00:00",
        end: "2026-05-21T10:00:00",
        location: "Room A",
        edited_at: "2026-05-21T14:20:00",
        edited_by: "Anna",
        edited_by_user_id: 7,
        change_summary: ["Time changed"],
        original_summary: "09:00 - 10:00 - Room A - Anna",
        current_summary: "09:30 - 10:30 - Room A - Anna",
      },
      {
        task_id: 2,
        task_name: "Jury Meeting",
        day: "2026-05-21",
        start: "2026-05-21T11:00:00",
        end: "2026-05-21T12:00:00",
        location: "Room B",
        edited_at: "2026-05-21T15:00:00",
        edited_by: "Ben",
        edited_by_user_id: 8,
        change_summary: ["Location changed"],
        original_summary: "11:00 - 12:00 - Room A - Ben",
        current_summary: "11:00 - 12:00 - Room B - Ben",
      },
    ],
    ...overrides,
  };
}

describe("ScheduleWebEditIndicator", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders a compact schedule-context notice with a review action", async () => {
    const onReview = vi.fn();
    const user = userEvent.setup();

    render(
      <ScheduleWebEditIndicator eventId={42} summary={summary()} onReview={onReview} />,
    );

    expect(screen.getByText(/2 web edits since desktop publish/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Review" }));
    expect(onReview).toHaveBeenCalledOnce();
  });

  it("dismisses the compact notice but keeps a quiet pencil entry point", async () => {
    const onReview = vi.fn();
    const user = userEvent.setup();

    render(
      <ScheduleWebEditIndicator eventId={42} summary={summary()} onReview={onReview} />,
    );

    await user.click(screen.getByRole("button", { name: "Dismiss web edit notice" }));

    expect(screen.queryByText(/since desktop publish/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "2 web edits" }));
    expect(onReview).toHaveBeenCalledOnce();
  });

  it("shows the notice again when a new web-edit state arrives", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <ScheduleWebEditIndicator eventId={42} summary={summary()} onReview={() => undefined} />,
    );

    await user.click(screen.getByRole("button", { name: "Dismiss web edit notice" }));
    expect(screen.queryByText(/since desktop publish/)).toBeNull();

    rerender(
      <ScheduleWebEditIndicator
        eventId={42}
        summary={summary({ last_edited_at: "2026-05-21T16:00:00" })}
        onReview={() => undefined}
      />,
    );

    expect(screen.getByText(/2 web edits since desktop publish/)).toBeInTheDocument();
  });

  it("hides the schedule indicator when there are no web edits", () => {
    const { container } = render(
      <ScheduleWebEditIndicator
        eventId={42}
        summary={summary({ edited_task_count: 0, items: [] })}
        onReview={() => undefined}
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
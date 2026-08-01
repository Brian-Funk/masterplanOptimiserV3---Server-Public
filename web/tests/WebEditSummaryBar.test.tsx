import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

import { WebEditSummaryBar } from "@/components/WebEditSummaryBar";
import type { WebEditSummary } from "@/lib/webEditConfidence";

const reviewSummary: WebEditSummary = {
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
      original_summary: "09:00 - 10:00 · Room A · Anna",
      current_summary: "09:30 - 10:30 · Room A · Anna",
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
      original_summary: "11:00 - 12:00 · Room A · Ben",
      current_summary: "11:00 - 12:00 · Room B · Ben",
    },
  ],
};

describe("WebEditSummaryBar", () => {
  it("renders one compact review summary and review action", async () => {
    const onReview = vi.fn();
    const user = userEvent.setup();

    render(<WebEditSummaryBar summary={reviewSummary} onReview={onReview} />);

    expect(screen.getByText("Review needed")).toBeInTheDocument();
    expect(screen.getByText("2 edits")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Review web edits" }));
    expect(onReview).toHaveBeenCalledOnce();
  });

  it("shows grouped details only after expansion", async () => {
    const user = userEvent.setup();
    render(<WebEditSummaryBar summary={reviewSummary} />);

    expect(screen.queryByText("Opening Briefing")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Show details" }));

    expect(screen.getByText("21.05.2026")).toBeInTheDocument();
    expect(screen.getByText("Opening Briefing")).toBeInTheDocument();
    expect(screen.getByText(/Time changed/)).toBeInTheDocument();
  });

  it("filters edits to the current user", async () => {
    const user = userEvent.setup();
    render(<WebEditSummaryBar summary={reviewSummary} currentUserId={7} />);

    await user.click(screen.getByRole("button", { name: "Show details" }));
    await user.click(screen.getByRole("button", { name: "Edited by me" }));

    expect(screen.getByText("Opening Briefing")).toBeInTheDocument();
    expect(screen.queryByText("Jury Meeting")).toBeNull();
  });

  it("shows a quiet loading state", () => {
    render(<WebEditSummaryBar summary={null} loading />);
    expect(screen.getByText("Loading web edit state...")).toBeInTheDocument();
  });
});

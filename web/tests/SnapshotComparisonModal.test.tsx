import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { SnapshotComparisonModal } from "@/components/SnapshotComparisonModal";
import { compareSnapshotToCurrent } from "@/lib/snapshotComparison";

const now = new Date("2026-05-21T18:30:00");

function task(overrides: Record<string, unknown> = {}) {
  return {
    id: overrides.id ?? 1,
    external_task_id: overrides.external_task_id ?? 101,
    name: overrides.name ?? "Opening Briefing",
    start: overrides.start ?? "2026-05-21T10:00:00",
    end: overrides.end ?? "2026-05-21T11:00:00",
    location_name: overrides.location_name ?? "Room A",
    attendees: overrides.attendees ?? [{ name: "Anna", person_id: 1 }],
    description: overrides.description ?? "Original notes",
  };
}

describe("SnapshotComparisonModal", () => {
  it("shows the loading state while schedules are being compared", () => {
    render(
      <SnapshotComparisonModal
        open
        loading
        summary={null}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("Comparing schedules")).toBeInTheDocument();
    expect(screen.getByText("Loading comparison...")).toBeInTheDocument();
  });

  it("renders the compact comparison summary and changed item details", () => {
    const summary = compareSnapshotToCurrent(
      [task()],
      [task({ start: "2026-05-21T10:30:00", end: "2026-05-21T11:30:00", location_name: "Room C" })],
      { snapshotId: "4", snapshotCreatedAt: "2026-05-21T16:00:00", now },
    );

    render(
      <SnapshotComparisonModal
        open
        summary={summary}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("Changes found since this snapshot")).toBeInTheDocument();
    expect(screen.getByText("Compared with snapshot from today at 16:00")).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.textContent === "Time 1")).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.textContent === "Location 1")).toBeInTheDocument();
    expect(screen.getAllByText("Opening Briefing")).toHaveLength(2);
    expect(screen.getByText("10:00 - 11:00 -> 10:30 - 11:30")).toBeInTheDocument();
    expect(screen.getByText("Room A -> Room C")).toBeInTheDocument();
  });

  it("keeps matching schedules calm", () => {
    const summary = compareSnapshotToCurrent([task()], [task()], {
      snapshotId: "1",
      now,
    });

    render(
      <SnapshotComparisonModal
        open
        summary={summary}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("No changes since this snapshot")).toBeInTheDocument();
    expect(screen.getByText("There are no schedule differences to review.")).toBeInTheDocument();
  });

  it("collapses long change lists by default", async () => {
    const user = userEvent.setup();
    const snapshotTasks = [1, 2, 3, 4].map((id) => task({ external_task_id: id, name: `Task ${id}` }));
    const currentTasks = snapshotTasks.map((item, index) => ({
      ...item,
      start: `2026-05-21T1${index}:30:00`,
    }));
    const summary = compareSnapshotToCurrent(snapshotTasks, currentTasks, {
      snapshotId: "8",
      now,
    });

    render(
      <SnapshotComparisonModal
        open
        summary={summary}
        onClose={vi.fn()}
      />,
    );

    expect(screen.queryByText("Task 4")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Show all 4" }));
    expect(screen.getByText("Task 4")).toBeInTheDocument();
  });
});
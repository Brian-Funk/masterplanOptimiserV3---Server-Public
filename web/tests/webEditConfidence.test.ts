import { describe, expect, it } from "vitest";

import {
  describeWebEditTask,
  formatWebEditTimestamp,
  groupWebEditItemsByDay,
  summariseWebEditState,
  type WebEditSummary,
} from "@/lib/webEditConfidence";

const now = new Date("2026-05-21T18:30:00");

function summary(overrides: Partial<WebEditSummary>): WebEditSummary {
  return {
    level: "healthy",
    edited_task_count: 0,
    last_edited_at: null,
    last_edited_by: null,
    has_published_baseline: true,
    headline: "No web edits",
    description: "Live schedule matches the published desktop source.",
    items: [],
    ...overrides,
  };
}

describe("web edit confidence helpers", () => {
  it("formats human-readable timestamps", () => {
    expect(formatWebEditTimestamp("2026-05-21T14:20:00", now)).toBe(
      "today at 14:20",
    );
    expect(formatWebEditTimestamp("2026-05-20T18:05:00", now)).toBe(
      "yesterday at 18:05",
    );
  });

  it("summarises healthy, review, and unknown states", () => {
    expect(summariseWebEditState(summary({ level: "healthy" }), now).headline).toBe(
      "No web edits",
    );
    expect(
      summariseWebEditState(
        summary({
          level: "review",
          edited_task_count: 3,
          last_edited_at: "2026-05-21T14:20:00",
          last_edited_by: "Anna",
          headline: "Review needed",
        }),
        now,
      ).description,
    ).toBe("3 web edits since the last desktop publish. Last edited by Anna today at 14:20.");
    expect(
      summariseWebEditState(
        summary({
          level: "unknown",
          has_published_baseline: false,
          headline: "Web edit state unknown",
        }),
        now,
      ).description,
    ).toBe("No published desktop baseline is available yet.");
  });

  it("describes task-level web edits without exposing raw data", () => {
    expect(
      describeWebEditTask(
        {
          has_web_edit: true,
          web_edit_edited_by: "Ben",
          web_edit_edited_at: "2026-05-21T14:20:00",
          web_edit_change_summary: ["Time changed", "Location changed"],
        },
        now,
      ),
    ).toBe("Edited on the web by Ben today at 14:20. Time changed; Location changed.");
  });

  it("groups review items by day", () => {
    const groups = groupWebEditItemsByDay([
      {
        task_id: 1,
        task_name: "Opening",
        day: "2026-05-21",
        edited_at: null,
        edited_by: null,
        edited_by_user_id: null,
        change_summary: [],
        original_summary: "09:00 - 10:00 · Room A · Anna",
        current_summary: "09:00 - 10:00 · Room A · Anna",
      },
      {
        task_id: 2,
        task_name: "Closing",
        day: null,
        edited_at: null,
        edited_by: null,
        edited_by_user_id: null,
        change_summary: [],
        original_summary: "09:00 - 10:00 · Room A · Anna",
        current_summary: "09:00 - 10:00 · Room A · Anna",
      },
    ]);

    expect(groups.map((group) => group.day)).toEqual(["2026-05-21", "No day"]);
  });
});

import { describe, expect, it } from "vitest";

import {
  compareSnapshotToCurrent,
  createUnavailableSnapshotComparison,
  formatSnapshotComparisonTimestamp,
} from "@/lib/snapshotComparison";

const now = new Date("2026-05-21T18:30:00");

function task(overrides: Record<string, unknown> = {}) {
  return {
    id: overrides.id ?? 1,
    external_task_id: overrides.external_task_id ?? 101,
    name: overrides.name ?? "Opening Briefing",
    summary: overrides.summary ?? "Daily setup",
    description: overrides.description ?? "Brief the team",
    start: overrides.start ?? "2026-05-21T10:00:00",
    end: overrides.end ?? "2026-05-21T11:00:00",
    location_name: overrides.location_name ?? "Room A",
    task_type_name: overrides.task_type_name ?? "Session",
    attendees: overrides.attendees ?? [
      { name: "Anna", person_id: 1 },
      { name: "Ben", person_id: 2 },
    ],
    field_assignments: overrides.field_assignments ?? null,
    field_values: overrides.field_values ?? null,
    additional: overrides.additional ?? null,
  };
}

describe("snapshot comparison", () => {
  it("returns a healthy summary when schedules match", () => {
    const summary = compareSnapshotToCurrent([task()], [task()], {
      snapshotId: "3",
      snapshotCreatedAt: "2026-05-21T16:00:00",
      now,
    });

    expect(summary.level).toBe("healthy");
    expect(summary.headline).toBe("No changes since this snapshot");
    expect(summary.totalChanges).toBe(0);
    expect(summary.snapshotLabel).toBe("snapshot from today at 16:00");
  });

  it("groups added and removed tasks", () => {
    const summary = compareSnapshotToCurrent(
      [task({ external_task_id: 101 }), task({ external_task_id: 102, name: "Removed Task" })],
      [task({ external_task_id: 101 }), task({ external_task_id: 103, name: "Added Task" })],
      { snapshotId: "1", now },
    );

    expect(summary.level).toBe("review");
    expect(summary.addedCount).toBe(1);
    expect(summary.removedCount).toBe(1);
    expect(summary.sections.find((section) => section.id === "added")?.items[0].taskName).toBe("Added Task");
    expect(summary.sections.find((section) => section.id === "removed")?.items[0].taskName).toBe("Removed Task");
  });

  it("groups time, location, assignment, and details changes", () => {
    const summary = compareSnapshotToCurrent(
      [task()],
      [
        task({
          start: "2026-05-21T10:30:00",
          end: "2026-05-21T11:30:00",
          location_name: "Room C",
          attendees: [{ name: "Clara", person_id: 3 }],
          description: "Updated briefing notes",
        }),
      ],
      { snapshotId: "2", now },
    );

    expect(summary.timeChangeCount).toBe(1);
    expect(summary.locationChangeCount).toBe(1);
    expect(summary.assignmentChangeCount).toBe(1);
    expect(summary.detailsChangeCount).toBe(1);
    expect(summary.sections.map((section) => section.id)).toEqual([
      "time",
      "location",
      "assignments",
      "details",
    ]);
  });

  it("reports unavailable comparisons for incompatible data", () => {
    const summary = compareSnapshotToCurrent(undefined, [task()], {
      snapshotId: "5",
      snapshotLabel: "Version 5",
      now,
    });

    expect(summary.level).toBe("blocked");
    expect(summary.headline).toBe("Snapshot comparison unavailable");
    expect(summary.description).toContain("does not contain comparable schedule data");
  });

  it("formats timestamps with human-readable Swiss dates", () => {
    expect(formatSnapshotComparisonTimestamp("2026-05-21T16:00:00", now)).toBe("today at 16:00");
    expect(formatSnapshotComparisonTimestamp("2026-05-20T18:10:00", now)).toBe("yesterday at 18:10");
    expect(formatSnapshotComparisonTimestamp("2026-05-12T09:10:00", now)).toBe("12.05.2026 at 09:10");
  });

  it("creates a blocked summary for failed live comparisons", () => {
    const summary = createUnavailableSnapshotComparison({
      snapshotId: "7",
      snapshotLabel: "Version 7",
      reason: "The current live schedule could not be loaded.",
      now,
    });

    expect(summary.level).toBe("blocked");
    expect(summary.snapshotLabel).toBe("Version 7");
    expect(summary.description).toBe("The current live schedule could not be loaded.");
  });
});
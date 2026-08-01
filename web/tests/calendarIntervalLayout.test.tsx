import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import {
  type CalendarInterval,
  layoutCalendarIntervals,
} from "@/lib/calendarIntervalLayout";
import { CalendarGrid, type Task } from "@/components/CalendarGrid";
import {
  PublicScheduleCalendarGrid,
  type PublicScheduleCalendarItem,
} from "@/components/PublicScheduleCalendarGrid";

const STAGGERED_INTERVALS: CalendarInterval[] = [
  { id: 1, start: 8 * 60, end: 11 * 60 },
  { id: 2, start: 10 * 60 + 30, end: 13 * 60 },
  { id: 3, start: 11 * 60 + 30, end: 13 * 60 + 30 },
  { id: 4, start: 12 * 60, end: 12 * 60 + 30 },
];

function expectCollisionFree(
  intervals: CalendarInterval[],
  minimumDurationMinutes = 0,
) {
  const layout = layoutCalendarIntervals(intervals, minimumDurationMinutes);
  const effectiveEnd = (interval: CalendarInterval) =>
    Math.max(interval.end, interval.start + minimumDurationMinutes);

  for (let firstIndex = 0; firstIndex < intervals.length; firstIndex += 1) {
    for (let secondIndex = firstIndex + 1; secondIndex < intervals.length; secondIndex += 1) {
      const first = intervals[firstIndex];
      const second = intervals[secondIndex];
      const verticallyOverlapping =
        first.start < effectiveEnd(second) && second.start < effectiveEnd(first);
      if (!verticallyOverlapping) continue;

      const firstLayout = layout.get(first.id)!;
      const secondLayout = layout.get(second.id)!;
      const firstRight = firstLayout.leftPercentage + firstLayout.widthPercentage;
      const secondRight = secondLayout.leftPercentage + secondLayout.widthPercentage;
      const horizontallyDisjoint =
        firstRight <= secondLayout.leftPercentage + Number.EPSILON ||
        secondRight <= firstLayout.leftPercentage + Number.EPSILON;

      expect(horizontallyDisjoint).toBe(true);
    }
  }
}

function publicItem(
  id: number,
  title: string,
  startTime: string,
  endTime: string,
): PublicScheduleCalendarItem {
  return {
    id,
    title,
    date: "2026-08-01",
    start_time: startTime,
    end_time: endTime,
    location_name: null,
    audience_teams: [],
    description: null,
    type_name: null,
    colour: "#7dd3fc",
    sort_order: id,
  };
}

function task(id: number, name: string, startTime: string, endTime: string): Task {
  return {
    id,
    external_task_id: id,
    name,
    summary: null,
    description: null,
    start: `2026-08-01T${startTime}:00`,
    end: `2026-08-01T${endTime}:00`,
    location_name: null,
    location_address: null,
    task_type_code: null,
    task_type_name: null,
    color: "#7dd3fc",
    attendees: [],
    field_assignments: null,
    field_values: null,
    field_definitions: null,
    additional: null,
    sort_order: id,
    has_web_edit: false,
  };
}

function expectStaggeredCardPositions(elements: HTMLElement[]) {
  const [first, second] = elements;
  const firstRight = Number.parseFloat(first.style.left) + Number.parseFloat(first.style.width);
  const secondLeft = Number.parseFloat(second.style.left);

  expect(Number.parseFloat(first.style.width)).toBeCloseTo(100 / 3);
  expect(secondLeft).toBeCloseTo(100 / 3);
  expect(firstRight).toBeCloseTo(secondLeft);
}

describe("calendar interval layout", () => {
  it("keeps staggered overlap groups collision-free", () => {
    const layout = layoutCalendarIntervals(STAGGERED_INTERVALS);

    expect(layout.get(1)).toMatchObject({ laneIndex: 0, laneCount: 3, laneSpan: 1 });
    expect(layout.get(2)).toMatchObject({ laneIndex: 1, laneCount: 3, laneSpan: 1 });
    expectCollisionFree(STAGGERED_INTERVALS);
  });

  it("uses separate full-width groups for intervals that do not overlap", () => {
    const layout = layoutCalendarIntervals([
      { id: 1, start: 0, end: 10 },
      { id: 2, start: 10, end: 20 },
      { id: 3, start: 30, end: 40 },
    ]);

    expect([...layout.values()]).toEqual([
      expect.objectContaining({ laneCount: 1, widthPercentage: 100 }),
      expect.objectContaining({ laneCount: 1, widthPercentage: 100 }),
      expect.objectContaining({ laneCount: 1, widthPercentage: 100 }),
    ]);
  });

  it("reserves the minimum displayed duration when assigning lanes", () => {
    const intervals = [
      { id: 1, start: 0, end: 5 },
      { id: 2, start: 5, end: 10 },
      { id: 3, start: 10, end: 15 },
    ];
    const layout = layoutCalendarIntervals(intervals, 12);

    expect(layout.get(1)?.laneIndex).not.toBe(layout.get(2)?.laneIndex);
    expect(layout.get(2)?.laneIndex).not.toBe(layout.get(3)?.laneIndex);
    expectCollisionFree(intervals, 12);
  });

  it("allocates enough lanes for nested and fully simultaneous intervals", () => {
    const intervals = [
      { id: 1, start: 0, end: 60 },
      { id: 2, start: 10, end: 50 },
      { id: 3, start: 20, end: 40 },
      { id: 4, start: 20, end: 40 },
    ];
    const layout = layoutCalendarIntervals(intervals);

    expect(layout.get(1)?.laneCount).toBe(4);
    expect(layout.get(4)?.laneCount).toBe(4);
    expectCollisionFree(intervals);
  });

  it("expands cards through adjacent lanes that are free for their duration", () => {
    const intervals = [
      { id: 1, start: 0, end: 100 },
      { id: 2, start: 0, end: 20 },
      { id: 3, start: 0, end: 10 },
      { id: 4, start: 30, end: 40 },
    ];
    const layout = layoutCalendarIntervals(intervals);

    expect(layout.get(4)).toMatchObject({ laneIndex: 1, laneCount: 3, laneSpan: 2 });
    expect(layout.get(4)?.widthPercentage).toBeCloseTo(200 / 3);
    expectCollisionFree(intervals);
  });

  it("is deterministic when input order changes", () => {
    const normal = layoutCalendarIntervals(STAGGERED_INTERVALS);
    const reversed = layoutCalendarIntervals([...STAGGERED_INTERVALS].reverse());

    STAGGERED_INTERVALS.forEach(({ id }) => {
      expect(reversed.get(id)).toEqual(normal.get(id));
    });
  });

  it("uses the collision-free layout in the public schedule grid", () => {
    render(
      <PublicScheduleCalendarGrid
        selectedDate="2026-08-01"
        items={[
          publicItem(1, "First", "08:00", "11:00"),
          publicItem(2, "Second", "10:30", "13:00"),
          publicItem(3, "Third", "11:30", "13:30"),
          publicItem(4, "Fourth", "12:00", "12:30"),
        ]}
      />,
    );

    expectStaggeredCardPositions([
      screen.getByRole("button", { name: "View details for First" }).parentElement!,
      screen.getByRole("button", { name: "View details for Second" }).parentElement!,
    ]);
  });

  it("uses the collision-free layout in the authenticated calendar grid", () => {
    render(
      <CalendarGrid
        selectedDate="2026-08-01"
        highlightedPersonId={null}
        highlightMode="off"
        onTaskDoubleClick={vi.fn()}
        tasks={[
          task(1, "First", "08:00", "11:00"),
          task(2, "Second", "10:30", "13:00"),
          task(3, "Third", "11:30", "13:30"),
          task(4, "Fourth", "12:00", "12:30"),
        ]}
      />,
    );

    const cardWrapper = (name: string) =>
      screen.getAllByText(name)[0].closest(".group")?.parentElement as HTMLElement;
    expectStaggeredCardPositions([cardWrapper("First"), cardWrapper("Second")]);
  });
});

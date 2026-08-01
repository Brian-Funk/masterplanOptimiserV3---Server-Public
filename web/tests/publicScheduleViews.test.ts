import { describe, expect, it } from "vitest";

import {
  getOrderedPublicScheduleViews,
  getPublicScheduleItemsForView,
} from "@/lib/publicScheduleViews";

const views = [
  { id: "10", name: "Delegates", sort_order: 1 },
  { id: "11", name: "Officials", sort_order: 0 },
];

const items = [
  {
    date: "2026-08-01",
    category_id: 10,
    start_time: "09:00",
    sort_order: 0,
    title: "Delegates briefing",
  },
  {
    date: "2026-08-02",
    working_date: "2026-08-01",
    category_id: 11,
    start_time: "01:00",
    sort_order: 0,
    title: "Officials briefing",
  },
];

describe("public schedule item filtering", () => {
  it("keeps every configured view available regardless of the selected date", () => {
    expect(getOrderedPublicScheduleViews(views)).toEqual([views[1], views[0]]);
  });

  it("returns sorted items for the selected view and date", () => {
    expect(getPublicScheduleItemsForView(items, "2026-08-01", "11")).toEqual([
      items[1],
    ]);
    expect(getPublicScheduleItemsForView(items, "2026-08-02", "11")).toEqual([]);
  });
});

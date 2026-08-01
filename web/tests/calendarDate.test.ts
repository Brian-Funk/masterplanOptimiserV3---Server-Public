import { describe, expect, it } from "vitest";
import { chooseInitialScheduleDate, getLocalDateString } from "@/lib/calendarDate";

describe("calendar initial date selection", () => {
  it("selects today when today is part of the published schedule", () => {
    expect(
      chooseInitialScheduleDate(
        ["2026-06-17", "2026-06-18", "2026-06-19"],
        "2026-06-18",
      ),
    ).toBe("2026-06-18");
  });

  it("selects the first upcoming date when today is before the event", () => {
    expect(
      chooseInitialScheduleDate(["2026-06-20", "2026-06-21"], "2026-06-18"),
    ).toBe("2026-06-20");
  });

  it("selects the latest event date when today is after the event", () => {
    expect(
      chooseInitialScheduleDate(["2026-06-16", "2026-06-17"], "2026-06-18"),
    ).toBe("2026-06-17");
  });

  it("returns null for an empty event schedule", () => {
    expect(chooseInitialScheduleDate([], "2026-06-18")).toBeNull();
  });

  it("formats local dates without UTC conversion", () => {
    const localEvening = new Date(2026, 5, 18, 23, 30);
    expect(getLocalDateString(localEvening)).toBe("2026-06-18");
  });
});
import { describe, expect, it } from "vitest";

import { EVENT_DATE_RANGE_ERROR, eventDateRangeError } from "@/lib/eventDates";

describe("event date ranges", () => {
  it("rejects an end date before the start date", () => {
    expect(eventDateRangeError("2031-08-20", "2031-08-12")).toBe(
      EVENT_DATE_RANGE_ERROR,
    );
  });

  it("allows partial, same-day, and increasing ranges", () => {
    expect(eventDateRangeError("", "2031-08-12")).toBeNull();
    expect(eventDateRangeError("2031-08-12", "")).toBeNull();
    expect(eventDateRangeError("2031-08-12", "2031-08-12")).toBeNull();
    expect(eventDateRangeError("2031-08-12", "2031-08-20")).toBeNull();
  });
});

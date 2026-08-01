/** Tests for the fail-closed localStorage offline-access marker. */
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildOfflineAccessForCalendar,
  commitOfflineAccessMarker,
  getOfflineAccessMarker,
  isOfflineAccessValid,
} from "@/lib/offlineAccess";

const user = { id: 4, event_id: 9, offline_access_ttl_hours: 3 };

beforeEach(() => {
  localStorage.clear();
  vi.useRealTimers();
});

describe("offlineAccess", () => {
  it("versions a marker and commits it only when explicitly requested", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-30T09:00:00.000Z"));
    const marker = buildOfflineAccessForCalendar(
      user,
      9,
      "2026-07-30T09:00:00.000Z",
    );

    expect(marker).toMatchObject({
      schema_version: 1,
      user_id: 4,
      event_id: 9,
      valid_until: "2026-07-30T12:00:00.000Z",
    });
    expect(localStorage.getItem("mp-opt-offline-access")).toBeNull();

    commitOfflineAccessMarker(marker);
    expect(getOfflineAccessMarker()).toEqual(marker);
  });

  it("removes and rejects a legacy unversioned marker", () => {
    localStorage.setItem("mp-opt-offline-access", JSON.stringify({
      user_id: 4,
      event_id: 9,
      cached_at: "2026-07-30T09:00:00.000Z",
      valid_until: "2999-07-30T12:00:00.000Z",
      ttl_hours: 3,
    }));

    expect(getOfflineAccessMarker()).toBeNull();
    expect(localStorage.getItem("mp-opt-offline-access")).toBeNull();
  });

  it("removes and rejects a marker with an unknown field", () => {
    localStorage.setItem("mp-opt-offline-access", JSON.stringify({
      schema_version: 1,
      user_id: 4,
      event_id: 9,
      cached_at: "2026-07-30T09:00:00.000Z",
      valid_until: "2999-07-30T12:00:00.000Z",
      ttl_hours: 3,
      copied_from: "unapproved",
    }));

    expect(getOfflineAccessMarker()).toBeNull();
    expect(localStorage.getItem("mp-opt-offline-access")).toBeNull();
  });

  it("rejects invalid timestamps and expired access", () => {
    const invalid = {
      schema_version: 1 as const,
      user_id: 4,
      event_id: 9,
      cached_at: "not-a-time",
      valid_until: "also-not-a-time",
      ttl_hours: 3,
    };
    commitOfflineAccessMarker(invalid);
    expect(getOfflineAccessMarker()).toBeNull();

    expect(isOfflineAccessValid({
      ...invalid,
      cached_at: "2026-07-30T09:00:00.000Z",
      valid_until: "2000-01-01T00:00:00.000Z",
    })).toBe(false);
  });
});

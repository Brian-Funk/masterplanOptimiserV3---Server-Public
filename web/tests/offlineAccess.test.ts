/** Tests for the fail-closed localStorage offline-access marker. */
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildOfflineAccessForCalendar,
  commitOfflineAccessMarker,
  getOfflineAccessMarker,
  isOfflineAccessValid,
} from "@/lib/offlineAccess";

const user = {
  id: 4,
  event_id: 9,
  event_ref: "9d492121-5a09-4e8a-a0a9-3c873af85928",
  membership_id: 12,
  controller_public_id: "07096118-e64c-4b89-a7c9-1f26d6198719",
  controller_trust_entity_id: "ctl-0123456789abcdef",
  data_policy_version: 2,
  data_policy_sha256: "a".repeat(64),
  offline_access_ttl_hours: 3,
};

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
      schema_version: 2,
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
      schema_version: 2,
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
      schema_version: 2 as const,
      user_id: 4,
      event_id: 9,
      event_ref: user.event_ref,
      membership_id: user.membership_id,
      controller_public_id: user.controller_public_id,
      controller_trust_entity_id: user.controller_trust_entity_id,
      data_policy_version: user.data_policy_version,
      data_policy_sha256: user.data_policy_sha256,
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

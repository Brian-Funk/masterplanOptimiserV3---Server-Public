/** Tests for fail-closed, expiring IndexedDB calendar storage. */
import { beforeEach, describe, expect, it } from "vitest";
import {
  clearOfflineCalendarCacheForUser,
  getOfflineCalendarPayload,
  OfflineCalendarStorageError,
  offlineCalendarStorageEnabled,
  participantSafeOfflineCalendarPayload,
  pruneExpiredOfflineCalendarPayloads,
  setOfflineCalendarStorageEnabled,
  storeOfflineCalendarPayload,
} from "@/lib/offlineCalendarCache";

const cachedAt = "2026-07-30T09:30:00.000Z";
const validUntil = "2026-07-30T12:30:00.000Z";
const beforeExpiry = new Date("2026-07-30T10:00:00.000Z");

const payload = {
  event_id: 7,
  event_name: "Cached Event",
  start_date: "2026-07-30",
  end_date: "2026-07-30",
  day_aliases: null,
  schedule_day_range: { start_hour: 6, end_hour: 24 },
  tasks: [{
    id: 1,
    external_task_id: 10,
    name: "Opening",
    summary: null,
    description: null,
    start: "2026-07-30T09:00:00",
    end: "2026-07-30T10:00:00",
    working_date: "2026-07-30",
    location_name: "Hall A",
    location_address: null,
    task_type_code: null,
    task_type_name: null,
    color: null,
    attendees: [{ name: "Viewer", person_id: 1 }],
    field_assignments: null,
    field_values: null,
    field_definitions: null,
    additional: null,
    sort_order: 0,
    has_web_edit: false,
    web_edit_edited_at: null,
    web_edit_edited_by: null,
    web_edit_edited_by_user_id: null,
    web_edit_change_summary: [],
  }],
  persons: [{ id: 1, external_person_id: 1, first_name: "View", last_name: "Only" }],
  public_schedule_views: [],
  public_schedule_categories: [],
  public_schedule_items: [],
  unavailabilities: [],
  data_policy_version: 1,
  data_policy_sha256: "1".repeat(64),
  data_policy_acknowledged: true,
};

const records = new Map<string, Record<string, unknown>>();
let failOpen = false;
let failWrite = false;

function installMemoryIndexedDb(): void {
  let storeCreated = false;
  const database = {
    objectStoreNames: { contains: () => storeCreated },
    createObjectStore: () => {
      storeCreated = true;
      return {};
    },
    deleteObjectStore: () => {
      storeCreated = false;
      records.clear();
    },
    transaction: () => {
      const transaction: Record<string, unknown> = {};
      const finish = () => queueMicrotask(() =>
        (transaction.oncomplete as (() => void) | undefined)?.(),
      );
      const store = {
        put: (record: Record<string, unknown>) => {
          const request: Record<string, unknown> = {};
          queueMicrotask(() => {
            if (failWrite) {
              (request.onerror as (() => void) | undefined)?.();
              (transaction.onerror as (() => void) | undefined)?.();
              return;
            }
            records.set(String(record.id), record);
            finish();
          });
          return request;
        },
        get: (key: string) => {
          const request: Record<string, unknown> = {};
          queueMicrotask(() => {
            request.result = records.get(key);
            (request.onsuccess as (() => void) | undefined)?.();
            finish();
          });
          return request;
        },
        delete: (key: string) => {
          records.delete(key);
          return {};
        },
        getAll: () => {
          const request: Record<string, unknown> = {};
          queueMicrotask(() => {
            request.result = Array.from(records.values());
            (request.onsuccess as (() => void) | undefined)?.();
            finish();
          });
          return request;
        },
      };
      transaction.objectStore = () => store;
      return transaction;
    },
    close: () => undefined,
  };
  const indexedDb = {
    open: () => {
      const request: Record<string, unknown> = {};
      queueMicrotask(() => {
        if (failOpen) {
          (request.onerror as (() => void) | undefined)?.();
          return;
        }
        request.result = database;
        if (!storeCreated) {
          (request.onupgradeneeded as (() => void) | undefined)?.();
        }
        (request.onsuccess as (() => void) | undefined)?.();
      });
      return request;
    },
  };
  Object.defineProperty(window, "indexedDB", {
    configurable: true,
    value: indexedDb as unknown as IDBFactory,
  });
}

function disableIndexedDb(): void {
  Object.defineProperty(window, "indexedDB", {
    configurable: true,
    value: undefined,
  });
}

beforeEach(() => {
  records.clear();
  localStorage.clear();
  failOpen = false;
  failWrite = false;
  installMemoryIndexedDb();
});

describe("offlineCalendarCache", () => {
  it("stores a versioned payload with its own server-bounded expiry", async () => {
    setOfflineCalendarStorageEnabled(12, true);
    await storeOfflineCalendarPayload(12, 7, payload, cachedAt, validUntil, beforeExpiry);

    await expect(
      getOfflineCalendarPayload<typeof payload>(12, 7, beforeExpiry),
    ).resolves.toMatchObject({
      schema_version: 3,
      user_id: 12,
      event_id: 7,
      cached_at: cachedAt,
      valid_until: validUntil,
      payload,
    });
    expect(offlineCalendarStorageEnabled(12)).toBe(true);
  });

  it("does not store a schedule before explicit opt-in", async () => {
    await expect(
      storeOfflineCalendarPayload(12, 7, payload, cachedAt, validUntil, beforeExpiry),
    ).resolves.toBeNull();
    expect(records.size).toBe(0);
  });

  it("accepts explicitly participant-visible fields", () => {
    expect(() => participantSafeOfflineCalendarPayload({
      ...payload,
      tasks: [{
        ...payload.tasks[0],
        field_values: { brief: "Bring the room key" },
        field_definitions: [{
          id: "brief",
          name: "Participant-visible instruction",
          type: "text",
          purpose: "operational_instruction",
          visibility: "participant",
        }],
      }],
    })).not.toThrow();
  });

  it("rejects organiser fields and unknown fields instead of sanitising client-side", () => {
    expect(() => participantSafeOfflineCalendarPayload({
      ...payload,
      tasks: [{
        ...payload.tasks[0],
        field_values: { note: "organiser only" },
        field_definitions: [{
          id: "note",
          name: "Internal organiser operational note",
          type: "text",
          purpose: "operational_instruction",
          visibility: "organiser",
        }],
      }],
    })).toThrow(OfflineCalendarStorageError);
    expect(() => participantSafeOfflineCalendarPayload({
      ...payload,
      unexpected: "not in the approved contract",
    })).toThrow(/unsupported field/);
  });

  it("rejects another participant's identity or availability", () => {
    expect(() => participantSafeOfflineCalendarPayload({
      ...payload,
      persons: [
        ...payload.persons,
        { id: 2, external_person_id: 2, first_name: "Other", last_name: "Person" },
      ],
    })).toThrow(/another participant/);
    expect(() => participantSafeOfflineCalendarPayload({
      ...payload,
      unavailabilities: [{
        person_id: 2,
        working_date: "2026-07-30",
        start: "2026-07-30T08:00:00",
        end: "2026-07-30T09:00:00",
      }],
    })).toThrow(/another participant/);
  });

  it("does not return another user or event's payload", async () => {
    setOfflineCalendarStorageEnabled(12, true);
    setOfflineCalendarStorageEnabled(13, true);
    await storeOfflineCalendarPayload(12, 7, payload, cachedAt, validUntil, beforeExpiry);

    await expect(getOfflineCalendarPayload(13, 7, beforeExpiry)).resolves.toBeNull();
    await expect(getOfflineCalendarPayload(12, 8, beforeExpiry)).resolves.toBeNull();
  });

  it("physically deletes an expired payload before returning null", async () => {
    setOfflineCalendarStorageEnabled(12, true);
    await storeOfflineCalendarPayload(12, 7, payload, cachedAt, validUntil, beforeExpiry);

    await expect(
      getOfflineCalendarPayload(12, 7, new Date("2026-07-30T12:30:00.001Z")),
    ).resolves.toBeNull();
    expect(records.size).toBe(0);
  });

  it("physically deletes a legacy or malformed entry", async () => {
    setOfflineCalendarStorageEnabled(12, true);
    await storeOfflineCalendarPayload(12, 7, payload, cachedAt, validUntil, beforeExpiry);
    records.get("12:7")!.schema_version = 1;

    await expect(getOfflineCalendarPayload(12, 7, beforeExpiry)).resolves.toBeNull();
    expect(records.size).toBe(0);
  });

  it("prunes expired and malformed entries on application start", async () => {
    setOfflineCalendarStorageEnabled(12, true);
    await storeOfflineCalendarPayload(12, 7, payload, cachedAt, validUntil, beforeExpiry);
    records.set("legacy", { id: "legacy", schema_version: 1, user_id: 8, event_id: 4 });

    await expect(
      pruneExpiredOfflineCalendarPayloads(new Date("2026-07-30T13:00:00.000Z")),
    ).resolves.toBe(2);
    expect(records.size).toBe(0);
  });

  it("clears cached payloads for only the selected user", async () => {
    setOfflineCalendarStorageEnabled(12, true);
    setOfflineCalendarStorageEnabled(13, true);
    await storeOfflineCalendarPayload(12, 7, payload, cachedAt, validUntil, beforeExpiry);
    await storeOfflineCalendarPayload(
      13,
      7,
      { ...payload, event_name: "Other" },
      cachedAt,
      validUntil,
      beforeExpiry,
    );

    await clearOfflineCalendarCacheForUser(12);

    await expect(getOfflineCalendarPayload(12, 7, beforeExpiry)).resolves.toBeNull();
    await expect(getOfflineCalendarPayload(13, 7, beforeExpiry)).resolves.toMatchObject({
      payload: { event_name: "Other" },
    });
  });

  it("reports IndexedDB unavailability instead of claiming a save", async () => {
    disableIndexedDb();
    setOfflineCalendarStorageEnabled(12, true);

    await expect(
      storeOfflineCalendarPayload(12, 7, payload, cachedAt, validUntil, beforeExpiry),
    ).rejects.toMatchObject({ code: "storage_unavailable" });
    expect(localStorage.getItem("mp-opt-offline-calendar-enabled:12")).toBe("true");
  });

  it("reports an IndexedDB write failure", async () => {
    failWrite = true;
    setOfflineCalendarStorageEnabled(12, true);

    await expect(
      storeOfflineCalendarPayload(12, 7, payload, cachedAt, validUntil, beforeExpiry),
    ).rejects.toMatchObject({ code: "storage_write_failed" });
    expect(records.size).toBe(0);
  });

  it("reports a blocked or failed database open", async () => {
    failOpen = true;
    setOfflineCalendarStorageEnabled(12, true);

    await expect(getOfflineCalendarPayload(12, 7, beforeExpiry)).rejects.toMatchObject({
      code: "storage_unavailable",
    });
  });
});

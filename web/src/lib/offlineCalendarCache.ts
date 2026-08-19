"use client";

const DB_NAME = "mp-opt-offline-calendar";
const DB_VERSION = 5;
const STORE_NAME = "calendar-payloads";
const CACHE_SCHEMA_VERSION = 5;
const OPT_IN_PREFIX = "mp-opt-offline-calendar-enabled:v2:";

type StorageErrorCode =
  | "storage_unavailable"
  | "storage_write_failed"
  | "storage_read_failed"
  | "storage_delete_failed"
  | "unsafe_payload"
  | "invalid_expiry";

/** A safe, user-visible failure of optional offline storage. */
export class OfflineCalendarStorageError extends Error {
  constructor(
    public readonly code: StorageErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "OfflineCalendarStorageError";
  }
}

/** Calendar payload stored locally for read-only offline access. */
export interface OfflineCalendarCacheEntry<TPayload = unknown> {
  schema_version: typeof CACHE_SCHEMA_VERSION;
  user_id: number;
  event_id: number;
  event_ref: string;
  membership_id: number;
  controller_public_id: string;
  controller_trust_entity_id: string;
  data_policy_version: number;
  data_policy_sha256: string;
  cached_at: string;
  valid_until: string;
  payload: TPayload;
}

type StoredOfflineCalendarCacheEntry<TPayload = unknown> =
  OfflineCalendarCacheEntry<TPayload> & { id: string };

function cacheKey(userId: number, eventId: number): string {
  return `${userId}:${eventId}`;
}

function canUseWindowStorage(): boolean {
  return typeof window !== "undefined";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function assertOnlyKeys(
  value: Record<string, unknown>,
  allowed: ReadonlySet<string>,
  context: string,
): void {
  const unknown = Object.keys(value).find((key) => !allowed.has(key));
  if (unknown) {
    throw new OfflineCalendarStorageError(
      "unsafe_payload",
      `The offline ${context} contains an unsupported field: ${unknown}.`,
    );
  }
}

const CALENDAR_KEYS = new Set([
  "offline_contract_version",
  "controller_public_id",
  "controller_trust_entity_id",
  "event_ref",
  "membership_id",
  "event_id",
  "event_name",
  "start_date",
  "end_date",
  "day_aliases",
  "schedule_day_range",
  "tasks",
  "persons",
  "public_schedule_views",
  "public_schedule_categories",
  "public_schedule_items",
  "unavailabilities",
  "data_policy_version",
  "data_policy_sha256",
  "data_policy_acknowledged",
]);
const TASK_KEYS = new Set([
  "id",
  "external_task_id",
  "name",
  "summary",
  "description",
  "start",
  "end",
  "working_date",
  "location_name",
  "location_address",
  "task_type_code",
  "task_type_name",
  "color",
  "attendees",
  "field_assignments",
  "field_values",
  "field_definitions",
  "additional",
  "sort_order",
  "has_web_edit",
  "web_edit_edited_at",
  "web_edit_edited_by",
  "web_edit_edited_by_user_id",
  "web_edit_change_summary",
]);
const ATTENDEE_KEYS = new Set(["name", "person_id"]);
const FIELD_DEFINITION_KEYS = new Set(["id", "name", "type", "purpose", "visibility"]);
const PARTICIPANT_FIELD_TYPES = new Set([
  "capabilities_list",
  "duration",
  "link",
  "location",
  "number",
  "persons_list",
  "start_end_time",
  "text",
  "time_range",
]);
const AUTHENTICATED_FIELD_VISIBILITIES = new Set([
  "participant",
  "organiser",
  "public",
]);
const PERSON_KEYS = new Set(["id", "external_person_id", "first_name", "last_name"]);
const CATEGORY_KEYS = new Set(["id", "name", "sort_order"]);
const PUBLIC_ITEM_KEYS = new Set([
  "id",
  "external_session_element_id",
  "title",
  "date",
  "working_date",
  "start_time",
  "end_time",
  "location_name",
  "location_address",
  "responsible",
  "audience_teams",
  "description",
  "category_id",
  "category_name",
  "type_name",
  "colour",
  "sort_order",
]);
const AUDIENCE_TEAM_KEYS = new Set(["name", "short_name"]);
const UNAVAILABILITY_KEYS = new Set(["person_id", "working_date", "start", "end"]);
const SCHEDULE_RANGE_KEYS = new Set(["start_hour", "end_hour"]);

function assertAttendee(value: unknown, context: string): void {
  if (!isRecord(value)) {
    throw new OfflineCalendarStorageError("unsafe_payload", `The offline ${context} is invalid.`);
  }
  assertOnlyKeys(value, ATTENDEE_KEYS, context);
  if (typeof value.name !== "string" || !Number.isInteger(value.person_id)) {
    throw new OfflineCalendarStorageError("unsafe_payload", `The offline ${context} is invalid.`);
  }
}

function assertParticipantFieldValue(type: string, value: unknown, context: string): void {
  let valid = false;
  if (type === "text" || type === "link") {
    valid = typeof value === "string" && value.length <= 10_000;
  } else if (type === "number" || type === "duration") {
    valid = typeof value === "number" && Number.isFinite(value);
  } else if (type === "capabilities_list") {
    valid = Array.isArray(value) && value.length <= 200 && value.every(
      (item) => typeof item === "string" && item.length <= 128,
    );
  } else if (type === "location") {
    valid = isRecord(value)
      && Object.keys(value).every((key) => key === "name" || key === "address")
      && typeof value.name === "string"
      && (value.address === undefined || value.address === null || typeof value.address === "string");
  } else if (type === "start_end_time" || type === "time_range") {
    valid = isRecord(value)
      && Object.keys(value).length === 2
      && typeof value.start === "string"
      && typeof value.end === "string";
  }
  if (!valid) {
    throw new OfflineCalendarStorageError("unsafe_payload", `Offline ${context} is invalid.`);
  }
}

/** Reject any payload that is not the dedicated bounded offline contract. */
export function assertParticipantSafeOfflineCalendarPayload(
  payload: unknown,
): asserts payload is Record<string, unknown> {
  if (!isRecord(payload)) {
    throw new OfflineCalendarStorageError("unsafe_payload", "The offline calendar payload is invalid.");
  }
  assertOnlyKeys(payload, CALENDAR_KEYS, "calendar");
  if (
    payload.offline_contract_version !== "mp-opt-offline-calendar-v5" ||
    typeof payload.controller_public_id !== "string" ||
    payload.controller_public_id.length === 0 ||
    typeof payload.controller_trust_entity_id !== "string" ||
    payload.controller_trust_entity_id.length === 0 ||
    typeof payload.event_ref !== "string" ||
    payload.event_ref.length === 0 ||
    !Number.isInteger(payload.membership_id) ||
    Number(payload.membership_id) <= 0 ||
    !Number.isInteger(payload.data_policy_version) ||
    Number(payload.data_policy_version) <= 0 ||
    typeof payload.data_policy_sha256 !== "string" ||
    !/^[0-9a-f]{64}$/.test(payload.data_policy_sha256) ||
    !Number.isInteger(payload.event_id) ||
    typeof payload.event_name !== "string" ||
    !Array.isArray(payload.tasks) ||
    !Array.isArray(payload.persons)
  ) {
    throw new OfflineCalendarStorageError("unsafe_payload", "The offline calendar payload is incomplete.");
  }

  const directoryIdentityIds = new Set<number>();
  for (const taskValue of payload.tasks) {
    if (!isRecord(taskValue)) {
      throw new OfflineCalendarStorageError("unsafe_payload", "An offline task is invalid.");
    }
    assertOnlyKeys(taskValue, TASK_KEYS, "task");
    if (
      taskValue.additional !== null ||
      taskValue.web_edit_edited_at !== null ||
      taskValue.web_edit_edited_by !== null ||
      taskValue.web_edit_edited_by_user_id !== null ||
      taskValue.has_web_edit !== false ||
      !Array.isArray(taskValue.web_edit_change_summary) ||
      taskValue.web_edit_change_summary.length !== 0 ||
      !Array.isArray(taskValue.attendees)
    ) {
      throw new OfflineCalendarStorageError(
        "unsafe_payload",
        "An offline task contains management-only information.",
      );
    }
    const definitions = taskValue.field_definitions === null
      ? []
      : taskValue.field_definitions;
    if (!Array.isArray(definitions)) {
      throw new OfflineCalendarStorageError("unsafe_payload", "Offline task field definitions are invalid.");
    }
    const definitionById = new Map<string, string>();
    for (const definitionValue of definitions) {
      if (!isRecord(definitionValue)) {
        throw new OfflineCalendarStorageError("unsafe_payload", "An offline task field definition is invalid.");
      }
      assertOnlyKeys(definitionValue, FIELD_DEFINITION_KEYS, "task field definition");
      if (
        typeof definitionValue.id !== "string"
        || typeof definitionValue.name !== "string"
        || typeof definitionValue.type !== "string"
        || typeof definitionValue.purpose !== "string"
        || typeof definitionValue.visibility !== "string"
        || !AUTHENTICATED_FIELD_VISIBILITIES.has(definitionValue.visibility)
        || !PARTICIPANT_FIELD_TYPES.has(definitionValue.type)
        || definitionById.has(definitionValue.id)
      ) {
        throw new OfflineCalendarStorageError(
          "unsafe_payload",
          "An offline task contains a field without an explicit authenticated-event classification.",
        );
      }
      definitionById.set(definitionValue.id, definitionValue.type);
    }
    const fieldValues = taskValue.field_values === null ? {} : taskValue.field_values;
    if (!isRecord(fieldValues)) {
      throw new OfflineCalendarStorageError("unsafe_payload", "Offline task field values are invalid.");
    }
    for (const [fieldId, value] of Object.entries(fieldValues)) {
      const type = definitionById.get(fieldId);
      if (!type || type === "persons_list") {
        throw new OfflineCalendarStorageError("unsafe_payload", "An offline task field is unclassified.");
      }
      assertParticipantFieldValue(type, value, `task field ${fieldId}`);
    }
    const flatAttendeeIds: number[] = [];
    taskValue.attendees.forEach((attendee, index) => {
      assertAttendee(attendee, `task attendee ${index + 1}`);
      flatAttendeeIds.push((attendee as Record<string, unknown>).person_id as number);
    });
    if (taskValue.field_assignments !== null) {
      if (!isRecord(taskValue.field_assignments)) {
        throw new OfflineCalendarStorageError("unsafe_payload", "Offline task assignments are invalid.");
      }
      const assignedIds: number[] = [];
      const assignedOnce = new Set<number>();
      for (const [field, attendees] of Object.entries(taskValue.field_assignments)) {
        if (definitionById.get(field) !== "persons_list" || !Array.isArray(attendees)) {
          throw new OfflineCalendarStorageError("unsafe_payload", `Offline task assignment ${field} is invalid.`);
        }
        attendees.forEach((attendee, index) => {
          assertAttendee(attendee, `task assignment ${field} item ${index + 1}`);
          const personId = (attendee as Record<string, unknown>).person_id as number;
          if (assignedOnce.has(personId)) {
            throw new OfflineCalendarStorageError(
              "unsafe_payload",
              "An offline task assigns one person to more than one allocation.",
            );
          }
          assignedOnce.add(personId);
          assignedIds.push(personId);
        });
      }
      if (
        assignedIds.length !== flatAttendeeIds.length
        || assignedIds.some((personId, index) => flatAttendeeIds[index] !== personId)
      ) {
        throw new OfflineCalendarStorageError(
          "unsafe_payload",
          "An offline task's flat and structured allocations do not agree.",
        );
      }
    }
  }

  for (const personValue of payload.persons) {
    if (!isRecord(personValue)) {
      throw new OfflineCalendarStorageError("unsafe_payload", "An offline person is invalid.");
    }
    assertOnlyKeys(personValue, PERSON_KEYS, "person");
    if (!Number.isInteger(personValue.external_person_id)) {
      throw new OfflineCalendarStorageError("unsafe_payload", "An offline person is invalid.");
    }
    directoryIdentityIds.add(personValue.external_person_id as number);
  }

  for (const key of ["public_schedule_views", "public_schedule_categories"] as const) {
    const categories = payload[key];
    if (categories !== undefined && !Array.isArray(categories)) {
      throw new OfflineCalendarStorageError("unsafe_payload", `Offline ${key} are invalid.`);
    }
    for (const categoryValue of categories ?? []) {
      if (!isRecord(categoryValue)) {
        throw new OfflineCalendarStorageError("unsafe_payload", "An offline schedule category is invalid.");
      }
      assertOnlyKeys(categoryValue, CATEGORY_KEYS, "schedule category");
    }
  }

  if (payload.public_schedule_items !== undefined && !Array.isArray(payload.public_schedule_items)) {
    throw new OfflineCalendarStorageError("unsafe_payload", "Offline public schedule items are invalid.");
  }
  for (const itemValue of payload.public_schedule_items ?? []) {
    if (!isRecord(itemValue)) {
      throw new OfflineCalendarStorageError("unsafe_payload", "An offline public schedule item is invalid.");
    }
    assertOnlyKeys(itemValue, PUBLIC_ITEM_KEYS, "public schedule item");
    if (!Array.isArray(itemValue.audience_teams)) {
      throw new OfflineCalendarStorageError("unsafe_payload", "Offline audience teams are invalid.");
    }
    for (const teamValue of itemValue.audience_teams) {
      if (!isRecord(teamValue)) {
        throw new OfflineCalendarStorageError("unsafe_payload", "An offline audience team is invalid.");
      }
      assertOnlyKeys(teamValue, AUDIENCE_TEAM_KEYS, "audience team");
    }
  }

  if (payload.unavailabilities !== undefined && !Array.isArray(payload.unavailabilities)) {
    throw new OfflineCalendarStorageError("unsafe_payload", "Offline unavailability data is invalid.");
  }
  for (const intervalValue of payload.unavailabilities ?? []) {
    if (!isRecord(intervalValue)) {
      throw new OfflineCalendarStorageError("unsafe_payload", "An offline unavailability is invalid.");
    }
    assertOnlyKeys(intervalValue, UNAVAILABILITY_KEYS, "unavailability");
    if (!Number.isInteger(intervalValue.person_id)) {
      throw new OfflineCalendarStorageError("unsafe_payload", "An offline unavailability is invalid.");
    }
    directoryIdentityIds.add(intervalValue.person_id as number);
  }

  if (directoryIdentityIds.size > 1) {
    throw new OfflineCalendarStorageError(
      "unsafe_payload",
      "The offline calendar contains another person's directory identity or availability.",
    );
  }

  if (payload.schedule_day_range !== undefined) {
    if (!isRecord(payload.schedule_day_range)) {
      throw new OfflineCalendarStorageError("unsafe_payload", "The offline schedule-day range is invalid.");
    }
    assertOnlyKeys(payload.schedule_day_range, SCHEDULE_RANGE_KEYS, "schedule-day range");
  }
}

/** Validate the exact participant-safe payload without deleting fields client-side. */
export function participantSafeOfflineCalendarPayload<TPayload>(payload: TPayload): TPayload {
  assertParticipantSafeOfflineCalendarPayload(payload);
  return payload;
}

/** Return whether this browser explicitly enabled offline schedule storage. */
export function offlineCalendarStorageEnabled(userId: number): boolean {
  if (!canUseWindowStorage()) return false;
  return window.localStorage.getItem(`${OPT_IN_PREFIX}${userId}`) === "true";
}

/** Persist the user's local opt-in choice. This value contains no schedule data. */
export function setOfflineCalendarStorageEnabled(userId: number, enabled: boolean): void {
  if (!canUseWindowStorage()) return;
  const key = `${OPT_IN_PREFIX}${userId}`;
  if (enabled) window.localStorage.setItem(key, "true");
  else window.localStorage.removeItem(key);
}

function openDatabase(): Promise<IDBDatabase> {
  if (!canUseWindowStorage() || !window.indexedDB) {
    return Promise.reject(
      new OfflineCalendarStorageError(
        "storage_unavailable",
        "Offline schedule storage is unavailable in this browser.",
      ),
    );
  }

  return new Promise((resolve, reject) => {
    let request: IDBOpenDBRequest;
    let settled = false;
    const rejectOnce = (message: string) => {
      if (settled) return;
      settled = true;
      reject(new OfflineCalendarStorageError("storage_unavailable", message));
    };
    try {
      request = window.indexedDB.open(DB_NAME, DB_VERSION);
    } catch {
      rejectOnce("Offline schedule storage could not be opened.");
      return;
    }

    request.onupgradeneeded = () => {
      const db = request.result;
      if (db.objectStoreNames.contains(STORE_NAME)) {
        db.deleteObjectStore(STORE_NAME);
      }
      db.createObjectStore(STORE_NAME, { keyPath: "id" });
    };
    request.onsuccess = () => {
      if (settled) {
        request.result.close();
        return;
      }
      settled = true;
      resolve(request.result);
    };
    request.onerror = () => rejectOnce("Offline schedule storage could not be opened.");
    request.onblocked = () => rejectOnce("Offline schedule storage is blocked by another tab.");
  });
}

function entryRecord<TPayload>(
  entry: OfflineCalendarCacheEntry<TPayload>,
): StoredOfflineCalendarCacheEntry<TPayload> {
  return { ...entry, id: cacheKey(entry.user_id, entry.event_id) };
}

function timestamp(value: unknown): number | null {
  if (typeof value !== "string") return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function validStoredEntry<TPayload>(
  value: unknown,
  userId: number,
  eventId: number,
  now: Date,
  expectedIdentity?: {
    event_ref: string | null;
    membership_id: number | null;
    controller_public_id: string | null;
    controller_trust_entity_id: string | null;
    data_policy_version: number | null;
    data_policy_sha256: string | null;
  },
): value is StoredOfflineCalendarCacheEntry<TPayload> {
  if (!isRecord(value)) return false;
  const cachedAt = timestamp(value.cached_at);
  const validUntil = timestamp(value.valid_until);
  if (
    value.schema_version !== CACHE_SCHEMA_VERSION ||
    value.id !== cacheKey(userId, eventId) ||
    value.user_id !== userId ||
    value.event_id !== eventId ||
    typeof value.event_ref !== "string" ||
    !Number.isInteger(value.membership_id) ||
    typeof value.controller_public_id !== "string" ||
    typeof value.controller_trust_entity_id !== "string" ||
    !Number.isInteger(value.data_policy_version) ||
    typeof value.data_policy_sha256 !== "string" ||
    (expectedIdentity !== undefined && (
      value.event_ref !== expectedIdentity.event_ref ||
      value.membership_id !== expectedIdentity.membership_id ||
      value.controller_public_id !== expectedIdentity.controller_public_id ||
      value.controller_trust_entity_id !== expectedIdentity.controller_trust_entity_id ||
      value.data_policy_version !== expectedIdentity.data_policy_version ||
      value.data_policy_sha256 !== expectedIdentity.data_policy_sha256
    )) ||
    cachedAt === null ||
    validUntil === null ||
    cachedAt > validUntil ||
    validUntil < now.getTime()
  ) {
    return false;
  }
  try {
    assertParticipantSafeOfflineCalendarPayload(value.payload);
    return true;
  } catch {
    return false;
  }
}

/** Store a bounded calendar payload for later offline viewing. */
export async function storeOfflineCalendarPayload<TPayload>(
  userId: number,
  eventId: number,
  payload: TPayload,
  cachedAt: string,
  validUntil: string,
  now = new Date(),
  expectedIdentity?: {
    event_ref: string | null;
    membership_id: number | null;
    controller_public_id: string | null;
    controller_trust_entity_id: string | null;
    data_policy_version: number | null;
    data_policy_sha256: string | null;
  },
): Promise<OfflineCalendarCacheEntry<TPayload> | null> {
  if (!offlineCalendarStorageEnabled(userId)) return null;
  assertParticipantSafeOfflineCalendarPayload(payload);
  const boundedPayload = payload as Record<string, unknown>;
  if (expectedIdentity !== undefined && (
    boundedPayload.event_ref !== expectedIdentity.event_ref ||
    boundedPayload.membership_id !== expectedIdentity.membership_id ||
    boundedPayload.controller_public_id !== expectedIdentity.controller_public_id ||
    boundedPayload.controller_trust_entity_id !== expectedIdentity.controller_trust_entity_id ||
    boundedPayload.data_policy_version !== expectedIdentity.data_policy_version ||
    boundedPayload.data_policy_sha256 !== expectedIdentity.data_policy_sha256
  )) {
    throw new OfflineCalendarStorageError(
      "unsafe_payload",
      "The offline schedule did not match the authenticated tenant and policy.",
    );
  }
  const cachedTime = timestamp(cachedAt);
  const validTime = timestamp(validUntil);
  if (
    cachedTime === null ||
    validTime === null ||
    cachedTime > validTime ||
    validTime < now.getTime()
  ) {
    throw new OfflineCalendarStorageError(
      "invalid_expiry",
      "The offline schedule expiry is invalid or has already passed.",
    );
  }

  const entry: OfflineCalendarCacheEntry<TPayload> = {
    schema_version: CACHE_SCHEMA_VERSION,
    user_id: userId,
    event_id: eventId,
    event_ref: boundedPayload.event_ref as string,
    membership_id: boundedPayload.membership_id as number,
    controller_public_id: boundedPayload.controller_public_id as string,
    controller_trust_entity_id: boundedPayload.controller_trust_entity_id as string,
    data_policy_version: boundedPayload.data_policy_version as number,
    data_policy_sha256: boundedPayload.data_policy_sha256 as string,
    cached_at: cachedAt,
    valid_until: validUntil,
    payload,
  };
  const db = await openDatabase();
  try {
    await new Promise<void>((resolve, reject) => {
      let settled = false;
      const fail = () => {
        if (settled) return;
        settled = true;
        reject(
          new OfflineCalendarStorageError(
            "storage_write_failed",
            "The offline schedule could not be saved on this device.",
          ),
        );
      };
      try {
        const transaction = db.transaction(STORE_NAME, "readwrite");
        const request = transaction.objectStore(STORE_NAME).put(entryRecord(entry));
        request.onerror = fail;
        transaction.oncomplete = () => {
          if (settled) return;
          settled = true;
          resolve();
        };
        transaction.onerror = fail;
        transaction.onabort = fail;
      } catch {
        fail();
      }
    });
  } finally {
    db.close();
  }
  return entry;
}

/** Return a cached calendar, deleting it first when it is expired or invalid. */
export async function getOfflineCalendarPayload<TPayload>(
  userId: number,
  eventId: number,
  expectedIdentityOrNow?: {
    event_ref: string | null;
    membership_id: number | null;
    controller_public_id: string | null;
    controller_trust_entity_id: string | null;
    data_policy_version: number | null;
    data_policy_sha256: string | null;
  } | Date,
  nowArg?: Date,
): Promise<OfflineCalendarCacheEntry<TPayload> | null> {
  if (!offlineCalendarStorageEnabled(userId)) return null;
  const expectedIdentity = expectedIdentityOrNow instanceof Date
    ? undefined
    : expectedIdentityOrNow;
  const now = expectedIdentityOrNow instanceof Date
    ? expectedIdentityOrNow
    : (nowArg ?? new Date());
  const db = await openDatabase();
  try {
    return await new Promise<OfflineCalendarCacheEntry<TPayload> | null>((resolve, reject) => {
      let result: OfflineCalendarCacheEntry<TPayload> | null = null;
      let settled = false;
      const fail = () => {
        if (settled) return;
        settled = true;
        reject(
          new OfflineCalendarStorageError(
            "storage_read_failed",
            "The offline schedule could not be read or cleaned up.",
          ),
        );
      };
      try {
        const transaction = db.transaction(STORE_NAME, "readwrite");
        const store = transaction.objectStore(STORE_NAME);
        const request = store.get(cacheKey(userId, eventId));
        request.onsuccess = () => {
          const record = request.result as unknown;
          if (validStoredEntry<TPayload>(record, userId, eventId, now, expectedIdentity)) {
            result = {
              schema_version: CACHE_SCHEMA_VERSION,
              user_id: record.user_id,
              event_id: record.event_id,
              event_ref: record.event_ref,
              membership_id: record.membership_id,
              controller_public_id: record.controller_public_id,
              controller_trust_entity_id: record.controller_trust_entity_id,
              data_policy_version: record.data_policy_version,
              data_policy_sha256: record.data_policy_sha256,
              cached_at: record.cached_at,
              valid_until: record.valid_until,
              payload: record.payload,
            };
          } else if (record !== undefined) {
            store.delete(cacheKey(userId, eventId));
          }
        };
        request.onerror = fail;
        transaction.oncomplete = () => {
          if (settled) return;
          settled = true;
          resolve(result);
        };
        transaction.onerror = fail;
        transaction.onabort = fail;
      } catch {
        fail();
      }
    });
  } finally {
    db.close();
  }
}

/** Physically remove all cached calendar payloads for one user. */
export async function clearOfflineCalendarCacheForUser(userId: number): Promise<void> {
  const db = await openDatabase();
  try {
    await new Promise<void>((resolve, reject) => {
      let settled = false;
      const fail = () => {
        if (settled) return;
        settled = true;
        reject(
          new OfflineCalendarStorageError(
            "storage_delete_failed",
            "The saved offline schedule could not be removed from this device.",
          ),
        );
      };
      try {
        const transaction = db.transaction(STORE_NAME, "readwrite");
        const store = transaction.objectStore(STORE_NAME);
        const request = store.getAll();
        request.onsuccess = () => {
          for (const value of request.result as unknown[]) {
            const record = value as Partial<StoredOfflineCalendarCacheEntry>;
            if (record.user_id === userId && typeof record.id === "string") {
              store.delete(record.id);
            }
          }
        };
        request.onerror = fail;
        transaction.oncomplete = () => {
          if (settled) return;
          settled = true;
          resolve();
        };
        transaction.onerror = fail;
        transaction.onabort = fail;
      } catch {
        fail();
      }
    });
  } finally {
    db.close();
  }
}

/** Remove every expired, legacy or malformed cache entry on application start. */
export async function pruneExpiredOfflineCalendarPayloads(now = new Date()): Promise<number> {
  const db = await openDatabase();
  let removed = 0;
  try {
    await new Promise<void>((resolve, reject) => {
      let settled = false;
      const fail = () => {
        if (settled) return;
        settled = true;
        reject(
          new OfflineCalendarStorageError(
            "storage_delete_failed",
            "Expired offline schedules could not be removed from this device.",
          ),
        );
      };
      try {
        const transaction = db.transaction(STORE_NAME, "readwrite");
        const store = transaction.objectStore(STORE_NAME);
        const request = store.getAll();
        request.onsuccess = () => {
          for (const value of request.result as unknown[]) {
            const record = value as Partial<StoredOfflineCalendarCacheEntry>;
            const userId = Number(record.user_id);
            const eventId = Number(record.event_id);
            if (
              !Number.isInteger(userId) ||
              !Number.isInteger(eventId) ||
              !validStoredEntry(record, userId, eventId, now)
            ) {
              if (typeof record.id === "string") store.delete(record.id);
              removed += 1;
            }
          }
        };
        request.onerror = fail;
        transaction.oncomplete = () => {
          if (settled) return;
          settled = true;
          resolve();
        };
        transaction.onerror = fail;
        transaction.onabort = fail;
      } catch {
        fail();
      }
    });
  } finally {
    db.close();
  }
  return removed;
}

/** Ask the active service worker to remove legacy protected-response caches. */
export async function clearLegacyPrivateCaches(): Promise<void> {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
  try {
    const registration = await navigator.serviceWorker.ready;
    registration.active?.postMessage({ type: "CLEAR_PRIVATE_CACHES" });
    navigator.serviceWorker.controller?.postMessage({ type: "CLEAR_PRIVATE_CACHES" });
  } catch {
    /* A missing service worker must not prevent logout cleanup. */
  }
}

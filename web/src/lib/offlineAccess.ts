"use client";

const OFFLINE_ACCESS_KEY = "mp-opt-offline-access";
const OFFLINE_ACCESS_SCHEMA_VERSION = 2;
const DEFAULT_OFFLINE_ACCESS_TTL_HOURS = 24;
const HOUR_MS = 60 * 60 * 1000;

/** Minimal non-sensitive marker for same-day offline calendar access. */
export interface OfflineAccessMarker {
  schema_version: typeof OFFLINE_ACCESS_SCHEMA_VERSION;
  user_id: number;
  event_id: number | null;
  event_ref: string | null;
  membership_id: number | null;
  controller_public_id: string | null;
  controller_trust_entity_id: string | null;
  data_policy_version: number | null;
  data_policy_sha256: string | null;
  cached_at: string | null;
  valid_until: string;
  ttl_hours: number;
}

interface OfflineAccessUser {
  id: number;
  event_id: number | null;
  event_ref?: string | null;
  membership_id?: number | null;
  controller_public_id?: string | null;
  controller_trust_entity_id?: string | null;
  data_policy_version?: number | null;
  data_policy_sha256?: string | null;
  offline_access_ttl_hours?: number | null;
}

function localEndOfDay(now: Date): Date {
  const end = new Date(now);
  end.setHours(23, 59, 59, 999);
  return end;
}

function normaliseOfflineAccessTtlHours(ttlHours: unknown): number {
  if (typeof ttlHours !== "number" || !Number.isFinite(ttlHours)) {
    return DEFAULT_OFFLINE_ACCESS_TTL_HOURS;
  }
  return Math.min(
    DEFAULT_OFFLINE_ACCESS_TTL_HOURS,
    Math.max(1, Math.trunc(ttlHours)),
  );
}

function nextOfflineExpiry(
  ttlHours = DEFAULT_OFFLINE_ACCESS_TTL_HOURS,
  now = new Date(),
): string {
  const endOfDay = localEndOfDay(now).getTime();
  const maxTtl =
    now.getTime() + normaliseOfflineAccessTtlHours(ttlHours) * HOUR_MS;
  return new Date(Math.min(endOfDay, maxTtl)).toISOString();
}

function readStoredMarker(): OfflineAccessMarker | null {
  if (typeof window === "undefined") return null;
  const rejectStoredMarker = (): null => {
    try {
      window.localStorage.removeItem(OFFLINE_ACCESS_KEY);
    } catch {
      /* Access remains denied even when the browser refuses cleanup. */
    }
    return null;
  };
  try {
    const raw = window.localStorage.getItem(OFFLINE_ACCESS_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<OfflineAccessMarker>;
    const allowedKeys = new Set([
      "schema_version",
      "user_id",
      "event_id",
      "event_ref",
      "membership_id",
      "controller_public_id",
      "controller_trust_entity_id",
      "data_policy_version",
      "data_policy_sha256",
      "cached_at",
      "valid_until",
      "ttl_hours",
    ]);
    if (
      parsed.schema_version !== OFFLINE_ACCESS_SCHEMA_VERSION ||
      Object.keys(parsed).some((key) => !allowedKeys.has(key)) ||
      !Number.isInteger(parsed.user_id) ||
      Number(parsed.user_id) <= 0 ||
      !(
        parsed.event_id === null ||
        (Number.isInteger(parsed.event_id) && Number(parsed.event_id) > 0)
      ) ||
      !(parsed.event_ref === null || (typeof parsed.event_ref === "string" && parsed.event_ref.length > 0)) ||
      !(parsed.membership_id === null || (Number.isInteger(parsed.membership_id) && Number(parsed.membership_id) > 0)) ||
      !(parsed.controller_public_id === null || (typeof parsed.controller_public_id === "string" && parsed.controller_public_id.length > 0)) ||
      !(parsed.controller_trust_entity_id === null || (typeof parsed.controller_trust_entity_id === "string" && parsed.controller_trust_entity_id.length > 0)) ||
      !(parsed.data_policy_version === null || (Number.isInteger(parsed.data_policy_version) && Number(parsed.data_policy_version) > 0)) ||
      !(parsed.data_policy_sha256 === null || (typeof parsed.data_policy_sha256 === "string" && /^[0-9a-f]{64}$/.test(parsed.data_policy_sha256))) ||
      !(
        parsed.cached_at === null ||
        (typeof parsed.cached_at === "string" &&
          Number.isFinite(Date.parse(parsed.cached_at)))
      ) ||
      typeof parsed.valid_until !== "string" ||
      !Number.isFinite(Date.parse(parsed.valid_until)) ||
      !Number.isInteger(parsed.ttl_hours) ||
      Number(parsed.ttl_hours) < 1 ||
      Number(parsed.ttl_hours) > DEFAULT_OFFLINE_ACCESS_TTL_HOURS
    ) {
      return rejectStoredMarker();
    }
    return {
      schema_version: OFFLINE_ACCESS_SCHEMA_VERSION,
      user_id: parsed.user_id as number,
      event_id: parsed.event_id as number | null,
      event_ref: parsed.event_ref as string | null,
      membership_id: parsed.membership_id as number | null,
      controller_public_id: parsed.controller_public_id as string | null,
      controller_trust_entity_id: parsed.controller_trust_entity_id as string | null,
      data_policy_version: parsed.data_policy_version as number | null,
      data_policy_sha256: parsed.data_policy_sha256 as string | null,
      cached_at: parsed.cached_at,
      valid_until: parsed.valid_until,
      ttl_hours: normaliseOfflineAccessTtlHours(parsed.ttl_hours),
    };
  } catch {
    return rejectStoredMarker();
  }
}

function writeStoredMarker(marker: OfflineAccessMarker): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(OFFLINE_ACCESS_KEY, JSON.stringify(marker));
}

/** Return the stored offline marker, including expired markers for UI messaging. */
export function getOfflineAccessMarker(): OfflineAccessMarker | null {
  return readStoredMarker();
}

/** Remove the offline marker after confirmed logout or unauthenticated status. */
export function clearOfflineAccessMarker(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(OFFLINE_ACCESS_KEY);
}

/** Record a successful online sign-in without marking schedule data as cached. */
export function storeOfflineAccessForUser(user: OfflineAccessUser): OfflineAccessMarker {
  const existing = readStoredMarker();
  const ttlHours = normaliseOfflineAccessTtlHours(
    user.offline_access_ttl_hours,
  );
  const marker: OfflineAccessMarker = {
    schema_version: OFFLINE_ACCESS_SCHEMA_VERSION,
    user_id: user.id,
    event_id: user.event_id ?? existing?.event_id ?? null,
    event_ref: user.event_ref ?? existing?.event_ref ?? null,
    membership_id: user.membership_id ?? existing?.membership_id ?? null,
    controller_public_id: user.controller_public_id ?? existing?.controller_public_id ?? null,
    controller_trust_entity_id: user.controller_trust_entity_id ?? existing?.controller_trust_entity_id ?? null,
    data_policy_version: user.data_policy_version ?? existing?.data_policy_version ?? null,
    data_policy_sha256: user.data_policy_sha256 ?? existing?.data_policy_sha256 ?? null,
    cached_at: existing?.cached_at ?? null,
    valid_until: nextOfflineExpiry(ttlHours),
    ttl_hours: ttlHours,
  };
  writeStoredMarker(marker);
  return marker;
}

/** Record that the calendar payload for an event has been fetched and cached. */
export function storeOfflineAccessForCalendar(
  user: OfflineAccessUser,
  eventId: number,
  cachedAt = new Date().toISOString(),
): OfflineAccessMarker {
  const marker = buildOfflineAccessForCalendar(user, eventId, cachedAt);
  writeStoredMarker(marker);
  return marker;
}

/** Build a calendar marker without claiming that its payload was persisted. */
export function buildOfflineAccessForCalendar(
  user: OfflineAccessUser,
  eventId: number,
  cachedAt = new Date().toISOString(),
): OfflineAccessMarker {
  const ttlHours = normaliseOfflineAccessTtlHours(
    user.offline_access_ttl_hours,
  );
  if (
    user.event_id !== eventId ||
    !user.event_ref ||
    !user.membership_id ||
    !user.controller_public_id ||
    !user.controller_trust_entity_id ||
    !user.data_policy_version ||
    !user.data_policy_sha256
  ) {
    throw new Error("The authenticated tenant identity is incomplete for offline storage.");
  }
  return {
    schema_version: OFFLINE_ACCESS_SCHEMA_VERSION,
    user_id: user.id,
    event_id: eventId,
    event_ref: user.event_ref,
    membership_id: user.membership_id,
    controller_public_id: user.controller_public_id,
    controller_trust_entity_id: user.controller_trust_entity_id,
    data_policy_version: user.data_policy_version,
    data_policy_sha256: user.data_policy_sha256,
    cached_at: cachedAt,
    valid_until: nextOfflineExpiry(ttlHours),
    ttl_hours: ttlHours,
  };
}

/** Persist a marker only after its matching IndexedDB payload is durable. */
export function commitOfflineAccessMarker(marker: OfflineAccessMarker): void {
  writeStoredMarker(marker);
}

/** Return whether the marker is still within its local offline access window. */
export function isOfflineAccessValid(
  marker: OfflineAccessMarker | null,
): boolean {
  if (!marker) return false;
  return Date.now() <= new Date(marker.valid_until).getTime();
}

/** Return whether a marker can be used for a cached calendar event. */
export function offlineAccessAllowsEvent(
  marker: OfflineAccessMarker | null,
  eventId: number,
): marker is OfflineAccessMarker {
  if (!marker || !isOfflineAccessValid(marker)) return false;
  return marker.event_id === null || marker.event_id === eventId;
}

/** Format the cache timestamp for the offline schedule banner. */
export function formatOfflineCachedAt(
  marker: OfflineAccessMarker | null,
): string | null {
  if (!marker?.cached_at) return null;
  return new Date(marker.cached_at).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

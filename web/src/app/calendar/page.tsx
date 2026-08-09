"use client";

import { useState, useEffect, useMemo, useCallback, useRef, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { useServiceAvailability } from "@/contexts/ServiceAvailabilityContext";
import { apiFetch } from "@/lib/api";
import { getApiUrl } from "@/lib/environment";
import { hardNavigate } from "@/lib/hardNavigation";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Logo } from "@/components/Logo";
import { DynamicPWA } from "@/components/DynamicPWA";
import { ServiceStatusBanner, ServiceStatusPanel } from "@/components/ServiceStatusPanel";
import { Footer } from "@/components/Footer";
import { CalendarGrid } from "@/components/CalendarGrid";
import { PublicScheduleCalendarGrid } from "@/components/PublicScheduleCalendarGrid";
import {
  DailyUnavailabilityIndicator,
  type PublishedUnavailability,
} from "@/components/DailyUnavailabilityIndicator";
import { TaskDetailModal } from "@/components/TaskDetailModal";
import { NotificationBell } from "@/components/NotificationBell";
import { AnnouncementBanner } from "@/components/AnnouncementBanner";
import { DraftChangesPanel } from "@/components/DraftChangesPanel";
import { CreateTaskModal } from "@/components/CreateTaskModal";
import { ChangesModal } from "@/components/ChangesModal";
import { WebEditReviewModal } from "@/components/WebEditReviewModal";
import { ScheduleWebEditIndicator } from "@/components/ScheduleWebEditIndicator";
import { MobileActionSheet } from "@/components/MobileActionSheet";
import { MobileBottomNavigation } from "@/components/MobileBottomNavigation";
import {
  describeWebEditTask,
  type WebEditSummary,
} from "@/lib/webEditConfidence";
import { chooseInitialScheduleDate } from "@/lib/calendarDate";
import {
  getOrderedPublicScheduleViews,
  getPublicScheduleItemsForView,
} from "@/lib/publicScheduleViews";
import {
  buildOfflineAccessForCalendar,
  clearOfflineAccessMarker,
  commitOfflineAccessMarker,
  formatOfflineCachedAt,
  offlineAccessAllowsEvent,
} from "@/lib/offlineAccess";
import {
  clearOfflineCalendarCacheForUser,
  getOfflineCalendarPayload,
  OfflineCalendarStorageError,
  offlineCalendarStorageEnabled,
  setOfflineCalendarStorageEnabled,
  storeOfflineCalendarPayload,
} from "@/lib/offlineCalendarCache";
import {
  currentWorkingDate,
  DEFAULT_SCHEDULE_DAY_RANGE,
  normaliseScheduleDayRange,
  type ScheduleDayRange,
  workingDateForDateTime,
} from "@/lib/scheduleDays";
import type { PendingChange } from "@/components/ChangesModal";
import type { DraftEdit } from "@/components/TaskDetailModal";
import type { DraftNewTask } from "@/components/CreateTaskModal";
import {
  LogOut,
  ChevronLeft,
  ChevronRight,
  RotateCcw,
  List,
  CalendarDays,
  Plus,
  Eye,
  EyeOff,
  Contrast,
  Hash,
  ArrowLeft,
  AlertTriangle,
  Settings,
  PencilLine,
  SlidersHorizontal,
  UserRound,
  MoreHorizontal,
  Users,
  Megaphone,
  History,
  Share2,
  RefreshCw,
  Shield,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface Attendee {
  name: string;
  person_id: number;
}

interface Task {
  id: number;
  external_task_id: number;
  name: string;
  summary: string | null;
  description: string | null;
  start: string;
  end: string;
  working_date?: string;
  location_name: string | null;
  location_address: string | null;
  task_type_code: string | null;
  task_type_name: string | null;
  color: string | null;
  attendees: Attendee[];
  field_assignments: Record<string, Attendee[]> | null;
  field_values: Record<string, unknown> | null;
  field_definitions: Array<{
    id: string;
    name: string;
    type: string;
    purpose: string;
    visibility: "participant" | "organiser" | "public";
  }> | null;
  additional: Record<string, unknown> | null;
  sort_order: number;
  has_web_edit: boolean;
  web_edit_edited_at?: string | null;
  web_edit_edited_by?: string | null;
  web_edit_edited_by_user_id?: number | null;
  web_edit_change_summary?: string[] | null;
}

interface Person {
  id: number;
  external_person_id: number;
  first_name: string;
  last_name: string;
}

interface PublicScheduleCategory {
  id: number;
  name: string;
  sort_order: number;
}

interface PublicScheduleItem {
  id: number;
  external_session_element_id: number;
  title: string;
  date: string;
  working_date?: string;
  start_time: string;
  end_time: string;
  location_name: string | null;
  location_address: string | null;
  responsible: string | null;
  audience_teams: Array<{ name?: string; short_name?: string | null }>;
  description: string | null;
  category_id: number | null;
  category_name: string | null;
  type_name: string | null;
  colour: string | null;
  sort_order: number;
}

interface CalendarData {
  event_id: number;
  event_name: string;
  start_date: string | null;
  end_date: string | null;
  day_aliases: Record<string, string> | null;
  tasks: Task[];
  persons: Person[];
  public_schedule_views?: PublicScheduleCategory[];
  public_schedule_categories?: PublicScheduleCategory[];
  public_schedule_items?: PublicScheduleItem[];
  schedule_day_range?: ScheduleDayRange;
  unavailabilities?: PublishedUnavailability[];
  data_policy_version?: number | null;
  data_policy_sha256?: string | null;
  data_policy_acknowledged?: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTime(iso: string, workingDate?: string): string {
  const timePart = iso.split("T")[1] || "00:00";
  const [hours, minutes] = timePart.split(":").map(Number);
  const clock = `${hours.toString().padStart(2, "0")}:${minutes.toString().padStart(2, "0")}`;
  return workingDate && iso.split("T")[0] > workingDate ? `${clock} (+1)` : clock;
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00");
  return d.toLocaleDateString(undefined, {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

function datesBetween(start: string | null, end: string | null): string[] {
  if (!start || !end || start > end) return [];
  const dates: string[] = [];
  const current = new Date(`${start}T00:00:00Z`);
  const last = new Date(`${end}T00:00:00Z`);
  while (current <= last && dates.length < 3660) {
    dates.push(current.toISOString().slice(0, 10));
    current.setUTCDate(current.getUTCDate() + 1);
  }
  return dates;
}

function formatWorkingClock(actualDate: string, time: string, workingDate: string): string {
  return actualDate > workingDate ? `${time} (+1)` : time;
}

function calendarWorkingDates(payload: CalendarData): string[] {
  const range = normaliseScheduleDayRange(payload.schedule_day_range);
  const values = new Set(datesBetween(payload.start_date, payload.end_date));
  for (const task of payload.tasks) {
    values.add(task.working_date ?? workingDateForDateTime(task.start, range));
  }
  for (const item of payload.public_schedule_items ?? []) {
    values.add(item.working_date ?? item.date);
  }
  return Array.from(values).sort();
}

function formatCacheTime(timestamp: string | null): string | null {
  if (!timestamp) return null;
  return new Date(timestamp).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatFieldValue(val: unknown, fieldType: string): string {
  if (val === null || val === undefined) return "";
  if (fieldType === "persons_list") {
    if (Array.isArray(val))
      return val.map((p: { name?: string }) => p.name ?? String(p)).join(", ");
    return String(val);
  }
  if (typeof val === "object") return JSON.stringify(val);
  return String(val);
}

function getPersonFields(task: Task): { fieldName: string; names: string }[] {
  // First: use field_definitions for clean field names
  if (task.field_definitions) {
    const fromDefs = task.field_definitions
      .filter((def) => def.type === "persons_list")
      .map((def) => {
        const val =
          task.field_assignments?.[def.id] ?? task.field_values?.[def.id];
        if (!val) return { fieldName: def.name, names: "" };
        return {
          fieldName: def.name,
          names: formatFieldValue(val, "persons_list"),
        };
      })
      .filter((f) => f.names);
    if (fromDefs.length > 0) return fromDefs;
  }
  // Fallback: use field_assignments directly
  if (task.field_assignments) {
    const defMap = new Map(
      (task.field_definitions ?? []).map((d) => [d.id, d.name]),
    );
    return Object.entries(task.field_assignments)
      .filter(([, v]) => v.length > 0)
      .map(([key, attendees]) => ({
        fieldName: defMap.get(key) ?? key,
        names: attendees.map((a) => a.name).join(", "),
      }));
  }
  return [];
}

// ---------------------------------------------------------------------------
// Page (inner  -  reads searchParams)
// ---------------------------------------------------------------------------

type ViewMode = "list" | "calendar";
type HighlightMode = "off" | "opacity" | "greyed-out" | "hatched";

function CalendarContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const eventId = Number(searchParams.get("event"));
  const snapshotVersion = searchParams.get("snapshot")
    ? Number(searchParams.get("snapshot"))
    : null;
  const cachedMode = searchParams.get("mode") === "cached";
  const { isReady: serviceReady } = useServiceAvailability();
  const {
    user,
    logout,
    isLoggingOut,
    isLoading: authLoading,
    authStatus,
    offlineAccess,
    offlineAccessExpired,
  } = useAuth();
  const canReviewWebEdits =
    snapshotVersion === null &&
    !!user &&
    (user.is_admin || user.is_root_admin || user.is_issuer);

  const [data, setData] = useState<CalendarData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [offline, setOffline] = useState(false);
  const [offlineCachedAt, setOfflineCachedAt] = useState<string | null>(null);
  const [offlineStorageEnabled, setOfflineStorageEnabled] = useState(false);
  const [offlineStorageError, setOfflineStorageError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [filterPersonId, setFilterPersonId] = useState<number | null>(null);
  const [publicScheduleViewId, setPublicScheduleViewId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("calendar");
  const [highlightMode, setHighlightMode] = useState<HighlightMode>("opacity");
  const [detailTask, setDetailTask] = useState<Task | null>(null);
  const liveRecoveryInFlight = useRef(false);
  const leftCachedMode = useRef(false);

  useEffect(() => {
    setOfflineStorageEnabled(user ? offlineCalendarStorageEnabled(user.id) : false);
  }, [user]);

  const saveOfflineCalendar = useCallback(async (cachedAt: string) => {
    if (!user || !eventId) {
      throw new OfflineCalendarStorageError(
        "storage_write_failed",
        "The offline schedule cannot be saved without an active event session.",
      );
    }
    const response = await apiFetch(`/api/v1/calendar/${eventId}/offline`, {
      cache: "no-store",
    });
    if (!response.ok) {
      throw new OfflineCalendarStorageError(
        "storage_write_failed",
        "The offline calendar copy could not be downloaded.",
      );
    }
    const payload = (await response.json()) as CalendarData;
    if (payload.event_id !== eventId) {
      throw new OfflineCalendarStorageError(
        "unsafe_payload",
        "The offline schedule did not match the selected event.",
      );
    }
    const marker = buildOfflineAccessForCalendar(user, eventId, cachedAt);
    const stored = await storeOfflineCalendarPayload(
      user.id,
      eventId,
      payload,
      cachedAt,
      marker.valid_until,
    );
    if (!stored) {
      throw new OfflineCalendarStorageError(
        "storage_write_failed",
        "Offline schedule saving is not enabled for this account on this device.",
      );
    }
    commitOfflineAccessMarker(marker);
    setOfflineStorageError(null);
    return payload;
  }, [user, eventId]);

  // Draft state
  const [draftEdits, setDraftEdits] = useState<Map<number, DraftEdit>>(
    new Map(),
  );
  const [draftDeletions, setDraftDeletions] = useState<Set<number>>(new Set());
  const [draftCreations, setDraftCreations] = useState<DraftNewTask[]>([]);
  const [nextTempId, setNextTempId] = useState(-1);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showMobileFilters, setShowMobileFilters] = useState(false);
  const [showMobileMore, setShowMobileMore] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [acknowledgingPolicy, setAcknowledgingPolicy] = useState(false);

  // Schedule change notifications
  const [pendingChanges, setPendingChanges] = useState<PendingChange[]>([]);
  const [showChangesModal, setShowChangesModal] = useState(false);
  const [webEditSummary, setWebEditSummary] = useState<WebEditSummary | null>(
    null,
  );
  const [webEditLoading, setWebEditLoading] = useState(false);
  const [webEditReviewOpen, setWebEditReviewOpen] = useState(false);

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) {
      if (authStatus === "offline" || !serviceReady || cachedMode) return;
      router.replace("/login");
    }
  }, [authLoading, user, authStatus, serviceReady, cachedMode, router]);

  // Missing event ID
  useEffect(() => {
    if (!eventId) {
      setError("No event specified");
      setLoading(false);
    }
  }, [eventId]);

  // Fetch calendar data (live or snapshot)
  const fetchCalendar = useCallback(async () => {
    if (!eventId) return;
    const validOfflineAccess = offlineAccessAllowsEvent(offlineAccess, eventId)
      ? offlineAccess
      : null;
    const showOfflineEmptyState = () => {
      setData(null);
      setOffline(true);
      setOfflineCachedAt(null);
      setError(
        offlineAccessExpired
          ? "Offline access expired. Please reconnect and sign in again."
          : "You are offline and no cached schedule is available.",
      );
    };
    const loadCachedCalendar = async () => {
      if (!validOfflineAccess) {
        showOfflineEmptyState();
        return false;
      }
      let cached;
      try {
        cached = await getOfflineCalendarPayload<CalendarData>(
          validOfflineAccess.user_id,
          eventId,
        );
      } catch (storageError) {
        setOfflineStorageError(
          storageError instanceof Error
            ? storageError.message
            : "The saved offline schedule could not be read or removed.",
        );
        showOfflineEmptyState();
        return false;
      }
      if (!cached) {
        showOfflineEmptyState();
        return false;
      }
      if (cached.payload.event_id !== eventId) {
        showOfflineEmptyState();
        return false;
      }
      setData(cached.payload);
      setSelectedDate((current) => current ?? chooseInitialScheduleDate(
        calendarWorkingDates(cached.payload),
        currentWorkingDate(normaliseScheduleDayRange(cached.payload.schedule_day_range)),
      ));
      setError("");
      setOfflineStorageError(null);
      setOffline(true);
      setOfflineCachedAt(cached.cached_at);
      return true;
    };

    if (cachedMode || !serviceReady || (!user && authStatus === "offline")) {
      setLoading(true);
      await loadCachedCalendar();
      setLoading(false);
      return;
    }
    if (!user && !validOfflineAccess) return;
    setLoading(true);
    setOffline(false);
    setOfflineCachedAt(null);
    try {
      if (snapshotVersion !== null) {
        // Snapshot mode: fetch from history API
        const res = await apiFetch(
          `/api/v1/admin/events/${eventId}/history/${snapshotVersion}`,
        );
        if (!res.ok) {
          if (res.status === 404) throw new Error("Snapshot not found");
          throw new Error("Failed to load snapshot");
        }
        const snapData = await res.json();
        const snap = snapData.snapshot;
        const meta = snap.event_meta || {};
        let dayAliases: Record<string, string> | null = null;
        let scheduleDayRange = DEFAULT_SCHEDULE_DAY_RANGE;
        if (meta.metadata_json) {
          try {
            const parsed =
              typeof meta.metadata_json === "string"
                ? JSON.parse(meta.metadata_json)
                : meta.metadata_json;
            if (parsed?.day_aliases) dayAliases = parsed.day_aliases;
            scheduleDayRange = normaliseScheduleDayRange(parsed?.schedule_day_range);
          } catch {
            /* ignore */
          }
        }
        const tasks: Task[] = (snap.tasks || []).map(
          (t: Record<string, unknown>, idx: number) => ({
            id: idx + 1,
            external_task_id: (t.external_task_id as number) || 0,
            name: (t.name as string) || "",
            summary: (t.summary as string) || null,
            description: (t.description as string) || null,
            start: (t.start as string) || "",
            end: (t.end as string) || "",
            working_date:
              (t.working_date as string) ||
              workingDateForDateTime((t.start as string) || "", scheduleDayRange),
            location_name: (t.location_name as string) || null,
            location_address: (t.location_address as string) || null,
            task_type_code: (t.task_type_code as string) || null,
            task_type_name: (t.task_type_name as string) || null,
            color: (t.color as string) || null,
            attendees: (t.attendees as Attendee[]) || [],
            field_assignments:
              (t.field_assignments as Record<string, Attendee[]>) || null,
            field_values: (t.field_values as Record<string, unknown>) || null,
            field_definitions:
              (t.field_definitions as Array<{
                id: string;
                name: string;
                type: string;
                purpose: string;
                visibility: "participant" | "organiser" | "public";
              }>) || null,
            additional: (t.additional as Record<string, unknown>) || null,
            sort_order: (t.sort_order as number) || 0,
            has_web_edit: false,
            web_edit_edited_at: null,
            web_edit_edited_by: null,
            web_edit_edited_by_user_id: null,
            web_edit_change_summary: [],
          }),
        );
        const persons: Person[] = (snap.persons || []).map(
          (p: Record<string, unknown>) => ({
            id: (p.external_person_id as number) || 0,
            external_person_id: (p.external_person_id as number) || 0,
            first_name: (p.first_name as string) || "",
            last_name: (p.last_name as string) || "",
          }),
        );
        setData({
          event_id: eventId,
          event_name: (meta.name as string) || "Event",
          start_date: (meta.start_date as string) || null,
          end_date: (meta.end_date as string) || null,
          day_aliases: dayAliases,
          tasks,
          persons,
          schedule_day_range: scheduleDayRange,
          unavailabilities: (snap.unavailabilities || []).map(
            (interval: Record<string, unknown>) => ({
              person_id: Number(interval.external_person_id),
              working_date: String(interval.working_date || ""),
              start: String(interval.start_datetime || ""),
              end: String(interval.end_datetime || ""),
            }),
          ),
        });
        setOfflineCachedAt(null);
      } else {
        // Normal mode: fetch live calendar
        const res = await apiFetch(`/api/v1/calendar/${eventId}`);
        if ([502, 503, 504].includes(res.status)) {
          await loadCachedCalendar();
          return;
        }
        if (res.status === 401) {
          hardNavigate("/login");
          return;
        }
        if (!res.ok) {
          if (res.status === 403) throw new Error("No access to this event");
          if (res.status === 404) throw new Error("Event not found");
          throw new Error("Failed to load calendar");
        }
        const calData: CalendarData = await res.json();
        const responseIsOffline =
          authStatus === "offline" ||
          (typeof navigator !== "undefined" && !navigator.onLine);
        setData(calData);
        setError("");
        setOffline(responseIsOffline);
        if (responseIsOffline) {
          setOfflineCachedAt(validOfflineAccess?.cached_at ?? null);
        } else {
          setOfflineCachedAt(null);
        }
        if (user && !responseIsOffline && offlineStorageEnabled) {
          const cachedAt = new Date().toISOString();
          try {
            await saveOfflineCalendar(cachedAt);
          } catch (storageError) {
            setOfflineStorageError(
              storageError instanceof Error
                ? storageError.message
                : "The offline schedule could not be updated.",
            );
          }
        }
      }
    } catch (err) {
      const networkOffline =
        !serviceReady ||
        authStatus === "offline" ||
        (typeof navigator !== "undefined" && !navigator.onLine) ||
        err instanceof TypeError;
      if (
        snapshotVersion === null &&
        networkOffline &&
        (await loadCachedCalendar())
      ) {
        return;
      }
      setOffline(networkOffline);
      setOfflineCachedAt(null);
      if (networkOffline) {
        setError(
          offlineAccessExpired
            ? "Offline access expired. Please reconnect and sign in again."
            : "You are offline and no cached schedule is available.",
        );
      } else {
        setError(err instanceof Error ? err.message : "Failed to load");
      }
    } finally {
      setLoading(false);
    }
  }, [
    user,
    eventId,
    snapshotVersion,
    authStatus,
    cachedMode,
    serviceReady,
    offlineAccess,
    offlineAccessExpired,
    offlineStorageEnabled,
    saveOfflineCalendar,
  ]);

  useEffect(() => {
    fetchCalendar();
  }, [fetchCalendar]);

  // Cached mode is deliberately sticky: prove the actual calendar read before
  // replacing the route, because public readiness can recover first.
  useEffect(() => {
    if (!cachedMode || !serviceReady || !user || !eventId || leftCachedMode.current) return;

    const recoverLiveCalendar = async () => {
      if (liveRecoveryInFlight.current || leftCachedMode.current) return;
      liveRecoveryInFlight.current = true;
      try {
        const response = await apiFetch(`/api/v1/calendar/${eventId}`, { cache: "no-store" });
        if (!response.ok) return;
        const payload = (await response.json()) as CalendarData;
        if (payload.event_id !== eventId) return;
        const cachedAt = new Date().toISOString();
        if (offlineStorageEnabled) {
          try {
            await saveOfflineCalendar(cachedAt);
          } catch (storageError) {
            setOfflineStorageError(
              storageError instanceof Error
                ? storageError.message
                : "The offline schedule could not be updated.",
            );
          }
        }
        setData(payload);
        setOffline(false);
        setOfflineCachedAt(null);
        setError("");
        setSelectedDate((current) => current ?? chooseInitialScheduleDate(
          calendarWorkingDates(payload),
          currentWorkingDate(normaliseScheduleDayRange(payload.schedule_day_range)),
        ));
        leftCachedMode.current = true;
        router.replace(`/calendar?event=${eventId}`);
      } catch {
        // Remain in cached mode and try again; no redirect is safer than a loop.
      } finally {
        liveRecoveryInFlight.current = false;
      }
    };

    void recoverLiveCalendar();
    const retry = window.setInterval(() => void recoverLiveCalendar(), 10_000);
    return () => window.clearInterval(retry);
  }, [cachedMode, serviceReady, user, eventId, router, offlineStorageEnabled, saveOfflineCalendar]);

  const fetchWebEditSummary = useCallback(async () => {
    if (!canReviewWebEdits || !eventId) {
      setWebEditSummary(null);
      setWebEditLoading(false);
      return;
    }

    setWebEditLoading(true);
    try {
      const res = await apiFetch(`/api/v1/admin/events/${eventId}/web-edits`);
      if (!res.ok) throw new Error("Failed to load web edit state");
      setWebEditSummary((await res.json()) as WebEditSummary);
    } catch {
      setWebEditSummary(null);
    } finally {
      setWebEditLoading(false);
    }
  }, [canReviewWebEdits, eventId]);

  useEffect(() => {
    void fetchWebEditSummary();
  }, [fetchWebEditSummary]);

  const refreshScheduleContext = useCallback(async () => {
    await fetchCalendar();
    await fetchWebEditSummary();
  }, [fetchCalendar, fetchWebEditSummary]);

  // Fetch pending schedule changes once calendar data is loaded
  useEffect(() => {
    if (!data || !user || !eventId || snapshotVersion !== null) return;
    (async () => {
      try {
        const res = await apiFetch(`/api/v1/notifications/changes/${eventId}`);
        if (res.ok) {
          const records: PendingChange[] = await res.json();
          if (records.length > 0) {
            setPendingChanges(records);
            setShowChangesModal(true);
          }
        }
      } catch {
        /* non-critical */
      }
    })();
  }, [data, user, eventId, snapshotVersion]);

  const handleDismissChanges = useCallback(async () => {
    setShowChangesModal(false);
    setPendingChanges([]);
    try {
      await apiFetch("/api/v1/notifications/changes/read", {
        method: "POST",
        body: JSON.stringify({ event_id: eventId }),
      });
    } catch {
      /* non-critical */
    }
  }, [eventId]);

  // Compute tasks with draft overlay applied
  const allTasks = useMemo(() => {
    if (!data) return [];
    // Start with server tasks, filter out draft deletions, apply draft edits
    const tasks: Task[] = data.tasks
      .filter((t) => !draftDeletions.has(t.id))
      .map((t) => {
        const edit = draftEdits.get(t.id);
        if (!edit) return t;
        const updated = {
          ...t,
          ...(edit.name !== undefined && { name: edit.name }),
          ...(edit.summary !== undefined && { summary: edit.summary }),
          ...(edit.description !== undefined && {
            description: edit.description,
          }),
          ...(edit.start !== undefined && { start: edit.start }),
          ...(edit.end !== undefined && { end: edit.end }),
          ...(edit.location_name !== undefined && {
            location_name: edit.location_name,
          }),
          ...(edit.location_address !== undefined && {
            location_address: edit.location_address,
          }),
          ...(edit.color !== undefined && { color: edit.color }),
          ...(edit.attendees !== undefined && { attendees: edit.attendees }),
          ...(edit.field_assignments !== undefined && {
            field_assignments: edit.field_assignments,
          }),
          ...(edit.field_values !== undefined && {
            field_values: edit.field_values,
          }),
        };
        return {
          ...updated,
          working_date: workingDateForDateTime(
            updated.start,
            normaliseScheduleDayRange(data.schedule_day_range),
          ),
        };
      });
    // Add draft creations as Task-like objects
    for (const c of draftCreations) {
      tasks.push({
        id: c.tempId,
        external_task_id: 0,
        name: c.name,
        summary: c.summary || null,
        description: c.description || null,
        start: c.start,
        end: c.end,
        working_date: workingDateForDateTime(
          c.start,
          normaliseScheduleDayRange(data.schedule_day_range),
        ),
        location_name: c.location_name || null,
        location_address: c.location_address || null,
        task_type_code: null,
        task_type_name: null,
        color: c.color || null,
        attendees: c.attendees || [],
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
      });
    }
    return tasks;
  }, [data, draftEdits, draftDeletions, draftCreations]);

  // Compute available dates
  const dates = useMemo(() => {
    const dateSet = new Set<string>();
    for (const date of datesBetween(data?.start_date ?? null, data?.end_date ?? null)) {
      dateSet.add(date);
    }
    for (const task of allTasks) {
      dateSet.add(
        task.working_date ?? workingDateForDateTime(
          task.start,
          normaliseScheduleDayRange(data?.schedule_day_range),
        ),
      );
    }
    for (const item of data?.public_schedule_items ?? []) {
      dateSet.add(item.working_date ?? item.date);
    }
    return Array.from(dateSet).sort();
  }, [
    allTasks,
    data?.end_date,
    data?.public_schedule_items,
    data?.schedule_day_range,
    data?.start_date,
  ]);

  // Auto-select the most relevant event day without overriding manual navigation.
  useEffect(() => {
    if (dates.length > 0 && !selectedDate) {
      const initialDate = chooseInitialScheduleDate(
        dates,
        currentWorkingDate(normaliseScheduleDayRange(data?.schedule_day_range)),
      );
      if (initialDate) setSelectedDate(initialDate);
    }
  }, [data?.schedule_day_range, dates, selectedDate]);

  // Resolve the highlighted person_id from the logged-in user's linked_person_id
  const highlightedPersonId = useMemo(() => {
    if (!user?.linked_person_id || !data) return null;
    // linked_person_id is the external_person_id (set by admin dropdown)
    // for matching against attendee.person_id
    const person = data.persons.find(
      (p) => p.external_person_id === user.linked_person_id,
    );
    return person ? person.external_person_id : null;
  }, [user, data]);

  // Filter tasks
  const visibleTasks = useMemo(() => {
    if (!selectedDate) return [];
    const range = normaliseScheduleDayRange(data?.schedule_day_range);
    let tasks = allTasks.filter((task) => (
      task.working_date ?? workingDateForDateTime(task.start, range)
    ) === selectedDate);
    if (filterPersonId !== null && publicScheduleViewId === null) {
      tasks = tasks.filter((t) =>
        t.attendees.some((a) => a.person_id === filterPersonId),
      );
    }
    return tasks.sort((a, b) => {
      const timeA = new Date(a.start).getTime();
      const timeB = new Date(b.start).getTime();
      if (timeA !== timeB) return timeA - timeB;
      return a.sort_order - b.sort_order;
    });
  }, [allTasks, data?.schedule_day_range, selectedDate, filterPersonId, publicScheduleViewId]);

  const publicScheduleCategories = useMemo(() => {
    const categories = data?.public_schedule_views ?? data?.public_schedule_categories ?? [];
    if (categories.length > 0) {
      return getOrderedPublicScheduleViews(
        categories.map((category) => ({
          id: String(category.id),
          name: category.name,
          sort_order: category.sort_order ?? 0,
        })),
      );
    }
    return (data?.public_schedule_items?.length ?? 0) > 0
      ? [{ id: "legacy", name: "Public Schedule", sort_order: 0 }]
      : [];
  }, [data?.public_schedule_categories, data?.public_schedule_items, data?.public_schedule_views]);

  useEffect(() => {
    if (
      publicScheduleViewId !== null &&
      !publicScheduleCategories.some((category) => category.id === publicScheduleViewId)
    ) {
      setPublicScheduleViewId(null);
    }
  }, [publicScheduleCategories, publicScheduleViewId]);

  const visiblePublicScheduleItems = useMemo(() => {
    return getPublicScheduleItemsForView(
      data?.public_schedule_items ?? [],
      selectedDate,
      publicScheduleViewId,
    );
  }, [data?.public_schedule_items, publicScheduleViewId, selectedDate]);

  const dateIndex = selectedDate ? dates.indexOf(selectedDate) : -1;
  const canPrev = dateIndex > 0;
  const canNext = dateIndex < dates.length - 1;
  const canManageEvent = Boolean(
    user && !user.is_root_admin && (user.is_admin || user.is_issuer),
  );
  const selectedPublicScheduleView = publicScheduleCategories.find(
    (category) => category.id === publicScheduleViewId,
  );
  const selectedPerson = filterPersonId === null
    ? null
    : data?.persons.find((person) => person.external_person_id === filterPersonId);
  const mobileScheduleLabel = selectedPublicScheduleView
    ? `Programme · ${selectedPublicScheduleView.name}`
    : selectedPerson
      ? `${selectedPerson.first_name} ${selectedPerson.last_name}`.trim()
      : "All tasks";

  const cycleHighlightMode = useCallback(() => {
    const modes: HighlightMode[] = [
      "off",
      "opacity",
      "greyed-out",
      "hatched",
    ];
    const index = modes.indexOf(highlightMode);
    setHighlightMode(modes[(index + 1) % modes.length]);
  }, [highlightMode]);

  // All tasks assigned to the current user, sorted chronologically
  const myTasks = useMemo(() => {
    if (!highlightedPersonId) return [];
    return allTasks
      .filter((t) =>
        t.attendees.some((a) => a.person_id === highlightedPersonId),
      )
      .sort((a, b) => {
        const timeA = new Date(a.start).getTime();
        const timeB = new Date(b.start).getTime();
        if (timeA !== timeB) return timeA - timeB;
        return a.sort_order - b.sort_order;
      });
  }, [allTasks, highlightedPersonId]);

  // Index of the currently-focused task within myTasks
  const [focusedMyTaskId, setFocusedMyTaskId] = useState<number | null>(null);

  const myTaskIndex = useMemo(() => {
    if (focusedMyTaskId === null) return -1;
    return myTasks.findIndex((t) => t.id === focusedMyTaskId);
  }, [myTasks, focusedMyTaskId]);

  const navigateMyTask = useCallback(
    (direction: "prev" | "next") => {
      if (myTasks.length === 0) return;

      let idx: number;
      if (myTaskIndex === -1) {
        // Nothing focused yet - pick the first task on the current date, or the very first
        idx = myTasks.findIndex((task) => (
          task.working_date ?? workingDateForDateTime(
            task.start,
            normaliseScheduleDayRange(data?.schedule_day_range),
          )
        ) === selectedDate);
        if (idx === -1) idx = 0;
      } else {
        idx =
          direction === "next"
            ? Math.min(myTaskIndex + 1, myTasks.length - 1)
            : Math.max(myTaskIndex - 1, 0);
      }

      const task = myTasks[idx];
      setFocusedMyTaskId(task.id);

      // Switch to the task's date if needed
      const taskDate = task.working_date ?? workingDateForDateTime(
        task.start,
        normaliseScheduleDayRange(data?.schedule_day_range),
      );
      if (taskDate !== selectedDate) {
        setSelectedDate(taskDate);
      }

      setDetailTask(task);
    },
    [data?.schedule_day_range, myTasks, myTaskIndex, selectedDate],
  );

  // --- Keyboard navigation: Left/Right arrows to navigate between dates ---
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't intercept when typing in an input/textarea/select or inside a modal
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

      if (e.key === "ArrowLeft" && canPrev) {
        e.preventDefault();
        setSelectedDate(dates[dateIndex - 1]);
      } else if (e.key === "ArrowRight" && canNext) {
        e.preventDefault();
        setSelectedDate(dates[dateIndex + 1]);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [canPrev, canNext, dates, dateIndex]);

  // --- Swipe gestures: horizontal swipe to navigate between dates ---
  useEffect(() => {
    let touchStartX = 0;
    let touchStartY = 0;

    const handleTouchStart = (e: TouchEvent) => {
      touchStartX = e.touches[0].clientX;
      touchStartY = e.touches[0].clientY;
    };

    const handleTouchEnd = (e: TouchEvent) => {
      const dx = e.changedTouches[0].clientX - touchStartX;
      const dy = e.changedTouches[0].clientY - touchStartY;
      // Only register horizontal swipes (dx large enough, more horizontal than vertical)
      if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy)) return;

      if (dx < 0 && canNext) {
        setSelectedDate(dates[dateIndex + 1]);
      } else if (dx > 0 && canPrev) {
        setSelectedDate(dates[dateIndex - 1]);
      }
    };

    window.addEventListener("touchstart", handleTouchStart, { passive: true });
    window.addEventListener("touchend", handleTouchEnd, { passive: true });
    return () => {
      window.removeEventListener("touchstart", handleTouchStart);
      window.removeEventListener("touchend", handleTouchEnd);
    };
  }, [canPrev, canNext, dates, dateIndex]);

  const isSnapshotMode = snapshotVersion !== null;

  const canEdit =
    !isSnapshotMode &&
    !offline &&
    !!user &&
    (user.can_edit || user.is_admin || user.is_root_admin);

  const acknowledgeDataPolicy = async () => {
    if (!user || !eventId) return;
    if (!data?.data_policy_version || !data.data_policy_sha256) {
      setError("The exact permitted-data policy identity is unavailable. Refresh before acknowledging it.");
      return;
    }
    setAcknowledgingPolicy(true);
    try {
      const scope = user.is_issuer
        ? "head_organiser"
        : user.is_admin
          ? "field_visibility_administrator"
          : "authorised_editor";
      const response = await apiFetch("/api/v1/user/data-policy/acknowledge", {
        method: "POST",
        body: JSON.stringify({
          event_id: eventId,
          scope,
          policy_version: data.data_policy_version,
          policy_sha256: data.data_policy_sha256,
        }),
      });
      if (!response.ok) throw new Error("The acknowledgement could not be recorded.");
      setData((current) => current ? { ...current, data_policy_acknowledged: true } : current);
    } catch (policyError) {
      setError(policyError instanceof Error ? policyError.message : "The acknowledgement could not be recorded.");
    } finally {
      setAcknowledgingPolicy(false);
    }
  };

  const handleLogout = async () => {
    if (await logout()) router.replace("/login");
  };

  // Open a task and sync the my-tasks navigator
  const openTaskDetail = useCallback(
    (task: Task) => {
      setDetailTask(task);
      if (myTasks.some((t) => t.id === task.id)) {
        setFocusedMyTaskId(task.id);
      } else {
        setFocusedMyTaskId(null);
      }
    },
    [myTasks],
  );

  // --- Draft handlers ---

  const handleDraftEdit = useCallback((taskId: number, changes: DraftEdit) => {
    setDraftEdits((prev) => {
      const updated = new Map(prev);
      const existing = updated.get(taskId) || {};
      updated.set(taskId, { ...existing, ...changes });
      return updated;
    });
  }, []);

  const handleDraftDelete = useCallback((taskId: number) => {
    // If it's a draft-created task (negative ID), remove from creations
    if (taskId < 0) {
      setDraftCreations((prev) => prev.filter((c) => c.tempId !== taskId));
    } else {
      setDraftDeletions((prev) => new Set(prev).add(taskId));
      // Remove any pending edits for this task
      setDraftEdits((prev) => {
        const updated = new Map(prev);
        updated.delete(taskId);
        return updated;
      });
    }
  }, []);

  const handleDraftCreate = useCallback((task: DraftNewTask) => {
    setDraftCreations((prev) => [...prev, task]);
    setNextTempId((prev) => prev - 1);
  }, []);

  const handleDiscardDrafts = useCallback(() => {
    setDraftEdits(new Map());
    setDraftDeletions(new Set());
    setDraftCreations([]);
  }, []);

  const handleCommitDrafts = useCallback(async () => {
    if (!eventId) return;
    if (offline) {
      setError("Offline: editing is unavailable until connection is restored.");
      return;
    }
    setCommitting(true);
    try {
      const edits = Array.from(draftEdits.entries()).map(
        ([taskId, changes]) => ({
          task_id: taskId,
          name: changes.name,
          summary: changes.summary,
          description: changes.description,
          start: changes.start,
          end: changes.end,
          location_name: changes.location_name,
          location_address: changes.location_address,
          color: changes.color,
          attendees: changes.attendees,
          field_assignments: changes.field_assignments,
          field_values: changes.field_values,
        }),
      );
      const deletions = Array.from(draftDeletions);
      const creations = draftCreations.map((c) => ({
        name: c.name,
        summary: c.summary,
        description: c.description,
        start: c.start,
        end: c.end,
        location_name: c.location_name,
        location_address: c.location_address,
        color: c.color,
        attendees: c.attendees,
      }));

      const res = await apiFetch(`/api/v1/calendar/${eventId}/tasks/commit`, {
        method: "POST",
        body: JSON.stringify({ edits, deletions, creations }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || "Failed to commit changes");
      }

      // Clear drafts and refresh
      handleDiscardDrafts();
      await refreshScheduleContext();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to commit");
    } finally {
      setCommitting(false);
    }
  }, [
    eventId,
    offline,
    draftEdits,
    draftDeletions,
    draftCreations,
    handleDiscardDrafts,
    refreshScheduleContext,
  ]);

  const handleRemoveDraftEdit = useCallback((taskId: number) => {
    setDraftEdits((prev) => {
      const updated = new Map(prev);
      updated.delete(taskId);
      return updated;
    });
  }, []);

  const handleRemoveDraftDeletion = useCallback((taskId: number) => {
    setDraftDeletions((prev) => {
      const updated = new Set(prev);
      updated.delete(taskId);
      return updated;
    });
  }, []);

  const handleRemoveDraftCreation = useCallback((tempId: number) => {
    setDraftCreations((prev) => prev.filter((c) => c.tempId !== tempId));
  }, []);

  // Map of task IDs to names for DraftChangesPanel display
  const taskNamesMap = useMemo(() => {
    const map = new Map<number, string>();
    if (data) {
      for (const t of data.tasks) {
        map.set(t.id, t.name);
      }
    }
    for (const c of draftCreations) {
      map.set(c.tempId, c.name);
    }
    return map;
  }, [data, draftCreations]);

  const offlineCachedAtLabel =
    formatCacheTime(offlineCachedAt) ?? formatOfflineCachedAt(offlineAccess);

  if (authLoading || loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900">
        <p className="text-gray-500 dark:text-gray-400">Loading...</p>
      </div>
    );
  }

  if (error) {
    const isOfflineError = offline || authStatus === "offline" || !serviceReady;
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900 p-4">
        {!serviceReady ? (
          <ServiceStatusPanel
            offlineAccess={offlineAccess}
            offlineAccessExpired={offlineAccessExpired}
            savedScheduleAvailable={false}
          />
        ) : <Card className="p-8 max-w-md w-full text-center">
          <p
            className={
              isOfflineError
                ? "text-amber-700 dark:text-amber-300 mb-4"
                : "text-red-600 dark:text-red-400 mb-4"
            }
          >
            {error}
          </p>
          <Button
            variant="outline"
            onClick={() => (isOfflineError ? fetchCalendar() : hardNavigate("/login"))}
          >
            {isOfflineError ? "Retry" : "Back to Login"}
          </Button>
        </Card>}
      </div>
    );
  }

  return (
    <div className={`min-h-screen flex flex-col bg-gray-50 dark:bg-gray-900 ${user?.is_root_admin ? "" : "mobile-page-with-nav"}`}>
      <DynamicPWA eventName={data?.event_name} />
      {/* Header */}
      <header className="sticky top-0 z-50 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 shadow-sm">
        <div className="max-w-5xl mx-auto px-4 py-2.5 md:py-3 flex items-center justify-between">
          <div className="flex min-w-0 items-center gap-3">
            <span className="hidden sm:inline-flex"><Logo height={32} href="https://info.mp-opt.net" /></span>
            <div className="min-w-0">
              <h1 className="truncate text-lg font-semibold text-gray-900 dark:text-gray-100 md:text-xl">
                {data?.event_name}
              </h1>
              {user?.display_name && (
                <p className="truncate text-xs text-gray-500 dark:text-gray-400 md:text-sm">
                  {user.display_name}
                </p>
              )}
            </div>
          </div>
          <div className="hidden items-center gap-2 md:flex">
            {(user?.is_admin || user?.is_root_admin) && (
              <button
                onClick={() => router.push("/admin")}
                className="p-2 rounded-lg text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800 transition-colors"
                aria-label="Back to admin"
                title="Back to admin"
              >
                <ArrowLeft size={20} />
              </button>
            )}
            {user?.is_issuer && !user?.is_admin && !user?.is_root_admin && (
              <button
                onClick={() => router.push("/admin")}
                className="p-2 rounded-lg text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800 transition-colors"
                aria-label="Manage users & announcements"
                title="Manage users & announcements"
              >
                <Settings size={20} />
              </button>
            )}
            {serviceReady && eventId > 0 &&
              ((!user?.is_admin && !user?.is_root_admin) ||
                user?.is_issuer) && <NotificationBell eventId={eventId} />}
            <ThemeToggle />
            {user && (
              <button
                onClick={() => router.push("/account/security")}
                className="p-2 rounded-lg text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800 transition-colors"
                aria-label="Account security"
                title="Account security"
              >
                <Shield size={20} />
              </button>
            )}
            {user && (
              <button
                onClick={handleLogout}
                disabled={isLoggingOut}
                aria-busy={isLoggingOut}
                className="p-2 rounded-lg text-gray-500 hover:bg-gray-100 disabled:cursor-wait disabled:opacity-60 dark:text-gray-400 dark:hover:bg-gray-800 transition-colors"
                aria-label={isLoggingOut ? "Logging out" : "Logout"}
                title={isLoggingOut ? "Logging out…" : "Logout"}
              >
                {isLoggingOut ? <RefreshCw size={20} className="animate-spin" /> : <LogOut size={20} />}
              </button>
            )}
          </div>
        </div>
      </header>

      <main className="flex-1 max-w-5xl mx-auto px-4 py-4 md:py-6 w-full">
        {/* Snapshot banner */}
        {isSnapshotMode && (
          <div className="mb-4 flex items-center gap-3 bg-amber-50 dark:bg-amber-900/20 border-2 border-amber-400 dark:border-amber-600 rounded-lg px-4 py-3">
            <AlertTriangle
              size={20}
              className="text-amber-600 dark:text-amber-400 shrink-0"
            />
            <div className="flex-1">
              <p className="font-semibold text-amber-800 dark:text-amber-200 text-sm">
                Snapshot - Version {snapshotVersion}
              </p>
              <p className="text-xs text-amber-700 dark:text-amber-300">
                You are viewing a historical snapshot. This is read-only.
              </p>
            </div>
            <button
              onClick={() => router.push("/admin")}
              className="px-3 py-1.5 text-xs font-medium bg-amber-200 dark:bg-amber-800 text-amber-800 dark:text-amber-200 rounded-lg hover:bg-amber-300 dark:hover:bg-amber-700 transition-colors whitespace-nowrap"
            >
              Back to Admin
            </button>
          </div>
        )}

        {/* Announcements */}
        {serviceReady && eventId > 0 && !isSnapshotMode && (
          <AnnouncementBanner eventId={eventId} />
        )}

        {user && (user.can_edit || user.is_admin || user.is_root_admin || user.is_issuer) && !data?.data_policy_acknowledged && (
          <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100">
            <p className="font-semibold">Permitted-data acknowledgement required</p>
            <p className="mt-1">Do not enter health, dietary, safeguarding, political, religious, disciplinary or unrelated private information. Optional fields must be necessary for event scheduling.</p>
            <div className="mt-3 flex flex-wrap gap-3">
              <a className="rounded border border-amber-500 px-3 py-2 font-medium" href={data?.data_policy_version ? `${getApiUrl()}/api/v1/governance/public/versions/${data.data_policy_version}/data-policy.html` : "/data-policy"}>Read the permanent exact policy</a>
              {!offline && serviceReady && <button type="button" disabled={acknowledgingPolicy} onClick={acknowledgeDataPolicy} className="rounded bg-amber-800 px-3 py-2 font-medium text-white disabled:opacity-60">{acknowledgingPolicy ? "Recording..." : "I understand the permitted-data rules"}</button>}
            </div>
          </div>
        )}
        {user && (user.can_edit || user.is_admin || user.is_root_admin || user.is_issuer) && data?.data_policy_acknowledged && (
          <p className="mb-4 text-right text-xs text-gray-500 dark:text-gray-400"><a className="underline" href={data.data_policy_version ? `${getApiUrl()}/api/v1/governance/public/versions/${data.data_policy_version}/data-policy.html` : "/data-policy"}>Permitted-data policy{data.data_policy_version ? ` v${data.data_policy_version}` : ""}</a>{data.data_policy_sha256 && <span className="ml-2 font-mono">{data.data_policy_sha256.slice(0, 12)}...</span>}</p>
        )}

        {/* Offline indicator */}
        {user && !isSnapshotMode && (
          <div className="mb-4 space-y-3 rounded-lg border border-gray-200 bg-white px-4 py-3 text-sm dark:border-gray-700 dark:bg-gray-800">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div><p className="font-medium text-gray-900 dark:text-gray-100">Offline schedule on this device</p><p className="text-xs text-gray-500 dark:text-gray-400">Optional. Stores the calendar, at most your linked participant identity and your own published unavailability in IndexedDB until the server-bounded expiry, logout or successful removal. Other participant identities, organiser-only task fields and edit history are excluded. <a className="underline" href="/privacy">Privacy details</a>.</p></div>
              <button type="button" className={`rounded px-3 py-2 font-medium ${offlineStorageEnabled ? "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200" : "bg-blue-600 text-white"}`} onClick={async () => {
                setOfflineStorageError(null);
                if (!offlineStorageEnabled) {
                  setOfflineCalendarStorageEnabled(user.id, true);
                  try {
                    await saveOfflineCalendar(new Date().toISOString());
                    setOfflineStorageEnabled(true);
                  } catch (storageError) {
                    setOfflineCalendarStorageEnabled(user.id, false);
                    setOfflineStorageEnabled(false);
                    setOfflineStorageError(
                      storageError instanceof Error
                        ? storageError.message
                        : "The offline schedule could not be enabled.",
                    );
                  }
                  return;
                }
                try {
                  await clearOfflineCalendarCacheForUser(user.id);
                  setOfflineCalendarStorageEnabled(user.id, false);
                  clearOfflineAccessMarker();
                  setOfflineStorageEnabled(false);
                } catch (storageError) {
                  setOfflineStorageError(
                    storageError instanceof Error
                      ? storageError.message
                      : "The offline schedule could not be removed.",
                  );
                }
              }}>{offlineStorageEnabled ? "Remove offline copy" : "Enable offline copy"}</button>
            </div>
            {offlineStorageError && (
              <p role="alert" className="rounded border border-red-300 bg-red-50 px-3 py-2 text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-100">
                {offlineStorageError} No offline copy is being claimed unless storage completed successfully.
              </p>
            )}
          </div>
        )}
        {!serviceReady && <ServiceStatusBanner cachedAt={offlineCachedAtLabel} />}
        {offline && serviceReady && (
          <div className="mb-4 flex items-start gap-2 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg px-4 py-2 text-amber-700 dark:text-amber-300 text-sm">
            <span className="mt-1 inline-block h-2 w-2 rounded-full bg-amber-500" />
            <div>
              <p>
                {offlineCachedAtLabel
                  ? `Offline - showing cached schedule from ${offlineCachedAtLabel}. Updates are unavailable.`
                  : "Offline - showing cached schedule. Updates are unavailable."}
              </p>
              <p className="mt-1 text-xs">
                Offline: editing is unavailable until connection is restored.
              </p>
            </div>
          </div>
        )}

        {/* Date navigation + controls */}
        {dates.length > 0 && (
          <div className="sticky top-[57px] z-30 -mx-4 mb-4 flex items-center justify-between border-b border-gray-200 bg-gray-50/95 px-3 py-2 backdrop-blur md:static md:mx-0 md:border-0 md:bg-transparent md:px-0 md:py-0 dark:border-gray-700 dark:bg-gray-900/95 md:dark:bg-transparent">
            <button
              onClick={() => canPrev && setSelectedDate(dates[dateIndex - 1])}
              disabled={!canPrev}
              className="flex h-11 w-11 items-center justify-center rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-30 transition-colors"
              aria-label="Previous day"
            >
              <ChevronLeft size={20} />
            </button>
            <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 text-center">
              {selectedDate ? formatDate(selectedDate) : ""}
              {selectedDate && data?.day_aliases?.[selectedDate] && (
                <span className="block text-sm font-normal text-gray-500 dark:text-gray-400">
                  {data.day_aliases[selectedDate]}
                </span>
              )}
            </h2>
            <button
              onClick={() => canNext && setSelectedDate(dates[dateIndex + 1])}
              disabled={!canNext}
              className="flex h-11 w-11 items-center justify-center rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-30 transition-colors"
              aria-label="Next day"
            >
              <ChevronRight size={20} />
            </button>
          </div>
        )}

        {/* Filters + view toggle */}
        <div className="mb-4 flex items-center justify-between gap-2 md:hidden">
          <button
            type="button"
            onClick={() => setShowMobileFilters(true)}
            className="flex min-h-11 min-w-0 flex-1 items-center justify-center gap-2 rounded-lg border border-gray-300 bg-white px-3 text-sm font-medium text-gray-700 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200"
            aria-label={`Change schedule view. Current view: ${mobileScheduleLabel}`}
          >
            <SlidersHorizontal className="shrink-0" size={17} />
            <span className="truncate">{mobileScheduleLabel}</span>
            {(filterPersonId !== null || publicScheduleViewId !== null) && (
              <span className="h-2 w-2 shrink-0 rounded-full bg-blue-600" aria-label="Filter active" />
            )}
          </button>
          {data && publicScheduleViewId === null && selectedDate && (
            <DailyUnavailabilityIndicator
              people={data.persons}
              intervals={data.unavailabilities ?? []}
              selectedDate={selectedDate}
              variant="touch"
            />
          )}
          {canEdit && publicScheduleViewId === null && (
            <button
              type="button"
              onClick={() => setShowCreateModal(true)}
              className="flex h-11 w-11 items-center justify-center rounded-lg bg-green-600 text-white hover:bg-green-700"
              aria-label="Create new task"
            >
              <Plus size={18} />
            </button>
          )}
        </div>

        <div className="mb-4 hidden items-center justify-between gap-3 md:flex md:flex-wrap">
          {/* Person / public schedule view selector */}
          {data && (data.persons.length > 0 || publicScheduleCategories.length > 0) && (
            <div className="flex items-center gap-2">
            <select
              value={
                publicScheduleViewId
                  ? `schedule:${publicScheduleViewId}`
                  : filterPersonId !== null
                    ? `person:${filterPersonId}`
                    : ""
              }
              onChange={(e) => {
                const value = e.target.value;
                if (value.startsWith("schedule:")) {
                  setPublicScheduleViewId(value.replace("schedule:", ""));
                  setFilterPersonId(null);
                  return;
                }
                setPublicScheduleViewId(null);
                setFilterPersonId(value.startsWith("person:") ? Number(value.replace("person:", "")) : null);
              }}
              className="px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 text-sm"
            >
              <option value="">All tasks</option>
              {data.persons.map((p) => (
                <option key={p.id} value={`person:${p.external_person_id}`}>
                  {p.first_name} {p.last_name}
                </option>
              ))}
              {publicScheduleCategories.length > 0 && (
                <optgroup label="Schedules">
                  {publicScheduleCategories.map((category) => (
                    <option key={category.id} value={`schedule:${category.id}`}>
                      Schedule ({category.name})
                    </option>
                  ))}
                </optgroup>
              )}
            </select>
            {publicScheduleViewId === null && selectedDate && (
              <DailyUnavailabilityIndicator
                people={data.persons}
                intervals={data.unavailabilities ?? []}
                selectedDate={selectedDate}
              />
            )}
            </div>
          )}

          {/* Highlight mode */}
          <button
            onClick={cycleHighlightMode}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border transition-colors ${
              highlightMode === "off"
                ? "border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
                : "border-blue-600 bg-blue-600 text-white"
            }`}
            title={
              {
                off: "Highlight off - click to cycle",
                opacity: "Dim others - click to cycle",
                "greyed-out": "Grey out others - click to cycle",
                hatched: "Hatched (colourblind) - click to cycle",
              }[highlightMode]
            }
          >
            {highlightMode === "off" && <Eye size={16} />}
            {highlightMode === "opacity" && <EyeOff size={16} />}
            {highlightMode === "greyed-out" && <Contrast size={16} />}
            {highlightMode === "hatched" && <Hash size={16} />}
          </button>

          {/* View toggle */}
          <div className="flex items-center gap-2">
            {canReviewWebEdits && (
              <ScheduleWebEditIndicator
                eventId={eventId}
                summary={webEditSummary}
                loading={webEditLoading}
                onReview={() => setWebEditReviewOpen(true)}
              />
            )}
            {canEdit && publicScheduleViewId === null && (
              <button
                onClick={() => setShowCreateModal(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-green-600 text-white hover:bg-green-700 transition-colors"
                title="Create new task"
              >
                <Plus size={16} />
                <span className="hidden sm:inline">New Task</span>
              </button>
            )}
            <div className="flex items-center border border-gray-300 dark:border-gray-600 rounded-lg overflow-hidden">
              <button
                onClick={() => setViewMode("calendar")}
                className={`flex items-center gap-1.5 px-3 py-1.5 text-sm transition-colors ${
                  viewMode === "calendar"
                    ? "bg-blue-600 text-white"
                    : "bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
                }`}
                title="Calendar view"
              >
                <CalendarDays size={16} />
                <span className="hidden sm:inline">Calendar</span>
              </button>
              <button
                onClick={() => setViewMode("list")}
                className={`flex items-center gap-1.5 px-3 py-1.5 text-sm transition-colors ${
                  viewMode === "list"
                    ? "bg-blue-600 text-white"
                    : "bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
                }`}
                title="List view"
              >
                <List size={16} />
                <span className="hidden sm:inline">List</span>
              </button>
            </div>
          </div>
        </div>

        {/* Content */}
        {publicScheduleViewId !== null ? (
          visiblePublicScheduleItems.length === 0 ? (
            <p className="text-gray-500 dark:text-gray-400 text-center py-12">
              No items have been published in {selectedPublicScheduleView?.name ?? "this programme"} for this day.
            </p>
          ) : viewMode === "calendar" ? (
            <div className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-800">
              <PublicScheduleCalendarGrid
                items={visiblePublicScheduleItems}
                selectedDate={selectedDate!}
                scheduleDayRange={normaliseScheduleDayRange(data?.schedule_day_range)}
              />
            </div>
          ) : (
            <div className="space-y-3 print:space-y-2">
              {visiblePublicScheduleItems.map((item) => {
                const audience = item.audience_teams
                  .map((team) => team.short_name || team.name)
                  .filter(Boolean)
                  .join(", ");
                return (
                  <Card key={item.id} className="overflow-hidden">
                    <div
                      className="h-1"
                      style={{ backgroundColor: item.colour || "#7dd3fc" }}
                    />
                    <div className="p-4">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-gray-500 dark:text-gray-400">
                            {formatWorkingClock(item.date, item.start_time, item.working_date ?? item.date)} - {formatWorkingClock(item.date, item.end_time, item.working_date ?? item.date)}
                            {item.location_name ? ` - ${item.location_name}` : ""}
                          </p>
                          <h3 className="mt-1 text-lg font-semibold text-gray-900 dark:text-gray-100">
                            {item.title}
                          </h3>
                        </div>
                        {item.type_name && (
                          <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs text-gray-600 dark:bg-gray-800 dark:text-gray-300">
                            {item.type_name}
                          </span>
                        )}
                      </div>
                      {audience && (
                        <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                          {audience}
                        </p>
                      )}
                      {item.description && (
                        <p className="mt-3 whitespace-pre-wrap text-sm text-gray-600 dark:text-gray-400">
                          {item.description}
                        </p>
                      )}
                    </div>
                  </Card>
                );
              })}
            </div>
          )
        ) : visibleTasks.length === 0 ? (
          <p className="text-gray-500 dark:text-gray-400 text-center py-12">
            No tasks for this day.
          </p>
        ) : viewMode === "calendar" ? (
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden">
            <CalendarGrid
              tasks={visibleTasks}
              selectedDate={selectedDate!}
              highlightedPersonId={highlightedPersonId}
              highlightMode={highlightMode}
              onTaskDoubleClick={(task) => openTaskDetail(task)}
              scheduleDayRange={normaliseScheduleDayRange(data?.schedule_day_range)}
            />
          </div>
        ) : (
          <div className="space-y-3">
            {visibleTasks.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                canEdit={canEdit}
                eventId={eventId}
                isHighlighted={
                  highlightMode === "off" || highlightedPersonId === null
                    ? null
                    : task.attendees.some(
                        (a) => a.person_id === highlightedPersonId,
                      )
                }
                highlightMode={highlightMode}
                onDoubleClick={() => openTaskDetail(task)}
                onReverted={refreshScheduleContext}
              />
            ))}
          </div>
        )}
      </main>

      {/* Task detail modal */}
      {detailTask && (
        <TaskDetailModal
          task={detailTask}
          canEdit={canEdit}
          eventId={eventId}
          persons={data?.persons || []}
          onClose={() => setDetailTask(null)}
          onDataChanged={refreshScheduleContext}
          onDraftEdit={handleDraftEdit}
          onDraftDelete={handleDraftDelete}
          dataPolicyAcknowledged={data?.data_policy_acknowledged ?? true}
          dataPolicyVersion={data?.data_policy_version}
          dataPolicySha256={data?.data_policy_sha256}
          isDraftNew={detailTask.id < 0}
          onNavigatePrev={myTaskIndex > 0 ? () => navigateMyTask("prev") : null}
          onNavigateNext={
            myTaskIndex >= 0 && myTaskIndex < myTasks.length - 1
              ? () => navigateMyTask("next")
              : null
          }
        />
      )}

      <WebEditReviewModal
        open={webEditReviewOpen}
        eventId={eventId}
        summary={webEditSummary}
        loading={webEditLoading}
        canRevert={canReviewWebEdits}
        onClose={() => setWebEditReviewOpen(false)}
        onRefresh={refreshScheduleContext}
      />

      {/* Create task modal */}
      {showCreateModal && selectedDate && canEdit && (
        <CreateTaskModal
          persons={data?.persons || []}
          defaultDate={selectedDate}
          onAdd={handleDraftCreate}
          onClose={() => setShowCreateModal(false)}
          nextTempId={nextTempId}
          dataPolicyAcknowledged={data?.data_policy_acknowledged ?? true}
          dataPolicyVersion={data?.data_policy_version}
          dataPolicySha256={data?.data_policy_sha256}
        />
      )}

      {/* Draft changes panel */}
      {!isSnapshotMode && (
        <DraftChangesPanel
          edits={draftEdits}
          deletions={draftDeletions}
          creations={draftCreations}
          taskNames={taskNamesMap}
          onCommit={handleCommitDrafts}
          onDiscard={handleDiscardDrafts}
          onRemoveEdit={handleRemoveDraftEdit}
          onRemoveDeletion={handleRemoveDraftDeletion}
          onRemoveCreation={handleRemoveDraftCreation}
          committing={committing}
          commitDisabled={offline}
          commitDisabledMessage="Offline: editing is unavailable until connection is restored."
        />
      )}

      {/* Schedule changes modal */}
      {showChangesModal && pendingChanges.length > 0 && (
        <ChangesModal
          changes={pendingChanges}
          onDismiss={handleDismissChanges}
          dayAliases={data?.day_aliases}
        />
      )}

      <Footer />

      {!user?.is_root_admin && (
        <MobileBottomNavigation
          elevated={draftEdits.size + draftDeletions.size + draftCreations.length > 0}
          items={
            canManageEvent
              ? [
                  {
                    id: "schedule",
                    label: "Schedule",
                    icon: <CalendarDays size={19} />,
                    active: true,
                    onSelect: () => {
                      setFilterPersonId(null);
                      setPublicScheduleViewId(null);
                    },
                  },
                  {
                    id: "people",
                    label: "People",
                    icon: <Users size={19} />,
                    onSelect: () => router.push(`/admin?tab=users&event=${eventId}`),
                  },
                  {
                    id: "updates",
                    label: "Updates",
                    icon: <Megaphone size={19} />,
                    onSelect: () => router.push(`/admin?tab=announcements&event=${eventId}`),
                  },
                  {
                    id: "more",
                    label: "More",
                    icon: <MoreHorizontal size={20} />,
                    active: showMobileMore,
                    onSelect: () => setShowMobileMore(true),
                  },
                ]
              : [
                  {
                    id: "schedule",
                    label: "Schedule",
                    icon: <CalendarDays size={19} />,
                    active: filterPersonId === null && publicScheduleViewId === null,
                    onSelect: () => {
                      setFilterPersonId(null);
                      setPublicScheduleViewId(null);
                    },
                  },
                  ...(highlightedPersonId
                    ? [{
                        id: "mine",
                        label: "Mine",
                        icon: <UserRound size={19} />,
                        active: filterPersonId === highlightedPersonId,
                        onSelect: () => {
                          setFilterPersonId(highlightedPersonId);
                          setPublicScheduleViewId(null);
                        },
                      }]
                    : []),
                  ...(publicScheduleCategories.length > 0
                    ? [{
                        id: "programme",
                        label: "Programme",
                        icon: <List size={19} />,
                        active: publicScheduleViewId !== null,
                        onSelect: () => {
                          setPublicScheduleViewId(publicScheduleViewId ?? publicScheduleCategories[0].id);
                          setFilterPersonId(null);
                        },
                      }]
                    : []),
                  {
                    id: "more",
                    label: "More",
                    icon: <MoreHorizontal size={20} />,
                    active: showMobileMore,
                    onSelect: () => setShowMobileMore(true),
                  },
                ]
          }
        />
      )}

      <MobileActionSheet
        open={showMobileFilters}
        onClose={() => setShowMobileFilters(false)}
        title="View and filters"
        description="Choose what is shown without leaving the selected day."
      >
        <div className="space-y-5">
          {data && (data.persons.length > 0 || publicScheduleCategories.length > 0) && (
            <div className="space-y-4">
              <div>
                <p className="mb-2 text-sm font-medium text-gray-700 dark:text-gray-200">Schedule content</p>
                <div className={`grid gap-2 ${publicScheduleCategories.length > 0 ? "grid-cols-2" : "grid-cols-1"}`}>
                  <button
                    type="button"
                    onClick={() => setPublicScheduleViewId(null)}
                    className={`min-h-11 rounded-lg border px-3 text-sm font-medium ${publicScheduleViewId === null ? "border-blue-600 bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-200" : "border-gray-300 dark:border-gray-600"}`}
                  >
                    Tasks
                  </button>
                  {publicScheduleCategories.length > 0 && (
                    <button
                      type="button"
                      onClick={() => {
                        setPublicScheduleViewId(publicScheduleViewId ?? publicScheduleCategories[0].id);
                        setFilterPersonId(null);
                      }}
                      className={`min-h-11 rounded-lg border px-3 text-sm font-medium ${publicScheduleViewId !== null ? "border-blue-600 bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-200" : "border-gray-300 dark:border-gray-600"}`}
                    >
                      Public programme
                    </button>
                  )}
                </div>
              </div>
              {publicScheduleViewId === null ? (
                <label className="block space-y-1.5 text-sm font-medium text-gray-700 dark:text-gray-200">
                  People
                  <select
                    value={filterPersonId !== null ? `person:${filterPersonId}` : ""}
                    onChange={(event) => {
                      const value = event.target.value;
                      setFilterPersonId(value.startsWith("person:") ? Number(value.replace("person:", "")) : null);
                    }}
                    className="min-h-11 w-full rounded-lg border border-gray-300 bg-white px-3 text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                  >
                    <option value="">All tasks</option>
                    {data.persons.map((person) => (
                      <option key={person.id} value={`person:${person.external_person_id}`}>
                        {person.first_name} {person.last_name}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <label className="block space-y-1.5 text-sm font-medium text-gray-700 dark:text-gray-200">
                  Programme view
                  <select
                    value={publicScheduleViewId}
                    onChange={(event) => setPublicScheduleViewId(event.target.value)}
                    className="min-h-11 w-full rounded-lg border border-gray-300 bg-white px-3 text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                  >
                    {publicScheduleCategories.map((category) => (
                      <option key={category.id} value={category.id}>
                        {category.name}
                      </option>
                    ))}
                  </select>
                  <span className="block text-xs font-normal text-gray-500 dark:text-gray-400">
                    Views remain selected while you move between days.
                  </span>
                </label>
              )}
            </div>
          )}
          <div>
            <p className="mb-2 text-sm font-medium text-gray-700 dark:text-gray-200">Presentation</p>
            <div className="grid grid-cols-2 gap-2">
              <button type="button" onClick={() => setViewMode("calendar")} className={`min-h-11 rounded-lg border px-3 text-sm font-medium ${viewMode === "calendar" ? "border-blue-600 bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-200" : "border-gray-300 dark:border-gray-600"}`}>Time grid</button>
              <button type="button" onClick={() => setViewMode("list")} className={`min-h-11 rounded-lg border px-3 text-sm font-medium ${viewMode === "list" ? "border-blue-600 bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-200" : "border-gray-300 dark:border-gray-600"}`}>List</button>
            </div>
          </div>
          <button type="button" onClick={cycleHighlightMode} className="flex min-h-11 w-full items-center justify-between rounded-lg border border-gray-300 px-3 text-sm dark:border-gray-600">
            <span>Assignment emphasis</span>
            <span className="font-medium capitalize">{highlightMode.replace("-", " ")}</span>
          </button>
          <Button fullWidth onClick={() => setShowMobileFilters(false)}>Done</Button>
        </div>
      </MobileActionSheet>

      <MobileActionSheet
        open={showMobileMore}
        onClose={() => setShowMobileMore(false)}
        title="More"
        description="Event tools and account settings."
      >
        <div className="space-y-2">
          {canManageEvent && (
            <>
              <button type="button" onClick={() => router.push(`/admin?tab=history&event=${eventId}`)} className="flex min-h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-800"><History size={18} /> History</button>
              <button type="button" onClick={() => router.push(`/admin?tab=public-links&event=${eventId}`)} className="flex min-h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-800"><Share2 size={18} /> Public links</button>
            </>
          )}
          {serviceReady && eventId > 0 && <div className="flex min-h-11 items-center justify-between rounded-lg px-3"><span className="text-sm">Notifications</span><NotificationBell eventId={eventId} /></div>}
          <div className="flex min-h-11 items-center justify-between rounded-lg px-3"><span className="text-sm">Appearance</span><ThemeToggle /></div>
          {user && <button type="button" onClick={() => router.push("/account/security")} className="flex min-h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-800"><Shield size={18} /> Account security</button>}
          {user ? (
            <button type="button" onClick={handleLogout} disabled={isLoggingOut} aria-busy={isLoggingOut} className="flex min-h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-sm text-red-600 hover:bg-red-50 disabled:cursor-wait disabled:opacity-60 dark:text-red-400 dark:hover:bg-red-900/20">{isLoggingOut ? <RefreshCw size={18} className="animate-spin" /> : <LogOut size={18} />} {isLoggingOut ? "Logging out…" : "Log out"}</button>
          ) : (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-900/20 dark:text-amber-200">Offline read-only access is active.</p>
          )}
        </div>
      </MobileActionSheet>
    </div>
  );
}

// ---------------------------------------------------------------------------
// TaskCard
// ---------------------------------------------------------------------------
function TaskCard({
  task,
  canEdit,
  eventId,
  isHighlighted,
  highlightMode,
  onDoubleClick,
  onReverted,
}: {
  task: Task;
  canEdit: boolean;
  eventId: number;
  isHighlighted: boolean | null;
  highlightMode: HighlightMode;
  onDoubleClick: () => void;
  onReverted: () => void;
}) {
  const [reverting, setReverting] = useState(false);

  const handleRevert = async () => {
    setReverting(true);
    try {
      await apiFetch(`/api/v1/calendar/${eventId}/tasks/${task.id}/edits`, {
        method: "DELETE",
      });
      onReverted();
    } catch {
      // ignore
    } finally {
      setReverting(false);
    }
  };

  const isDimmed = isHighlighted === false;
  const isGreyed = isDimmed && highlightMode === "greyed-out";
  const isHatched = isDimmed && highlightMode === "hatched";
  const webEditDescription = task.has_web_edit ? describeWebEditTask(task) : "";

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onDoubleClick}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onDoubleClick();
        }
      }}
      aria-label={`Open ${task.name}`}
      style={isGreyed ? { filter: "grayscale(1)", opacity: 0.6 } : undefined}
    >
      <Card
        className={`relative cursor-pointer overflow-hidden p-4 transition-[border-color,box-shadow] hover:border-gray-300 hover:shadow-sm dark:hover:border-gray-600${isDimmed && highlightMode === "opacity" ? " opacity-40" : ""}`}
      >
        {task.has_web_edit && (
          <span
            className="absolute right-3 top-3 z-20 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-amber-500/10 text-amber-700 ring-1 ring-amber-500/20 dark:text-amber-300"
            title={webEditDescription}
            aria-label={webEditDescription}
          >
            <PencilLine className="h-2.5 w-2.5" />
          </span>
        )}
        {isHatched && (
          <div
            className="absolute inset-0 pointer-events-none z-10"
            style={{
              backgroundImage:
                "repeating-linear-gradient(135deg, transparent, transparent 4px, rgba(107,114,128,0.18) 4px, rgba(107,114,128,0.18) 6px)",
            }}
          />
        )}
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              {task.color && (
                <span
                  className="w-3 h-3 rounded-full flex-shrink-0"
                  style={{ backgroundColor: task.color }}
                />
              )}
              <h3 className="font-semibold text-gray-900 dark:text-gray-100 truncate pr-4">
                {task.name}
              </h3>
            </div>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              {formatTime(task.start, task.working_date)} - {formatTime(task.end, task.working_date)}
              {task.location_name && ` · ${task.location_name}`}
            </p>
            {task.summary && (
              <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                {task.summary}
              </p>
            )}
            {/* Assigned persons */}
            {(() => {
              // Web-edited tasks: show attendees directly (field structures not updated by edits)
              if (task.has_web_edit && task.attendees.length > 0) {
                return (
                  <p className="text-xs text-gray-400 dark:text-gray-500 mt-2">
                    {task.attendees.map((a) => a.name).join(", ")}
                  </p>
                );
              }
              const pf = getPersonFields(task);
              if (pf.length > 1) {
                return (
                  <div className="text-xs text-gray-400 dark:text-gray-500 mt-2 space-y-0.5">
                    {pf.map((f) => (
                      <div key={f.fieldName}>
                        <span className="font-medium">{f.fieldName}:</span>{" "}
                        {f.names}
                      </div>
                    ))}
                  </div>
                );
              }
              if (pf.length === 1) {
                return (
                  <p className="text-xs text-gray-400 dark:text-gray-500 mt-2">
                    {pf[0].names}
                  </p>
                );
              }
              if (task.attendees.length > 0) {
                return (
                  <p className="text-xs text-gray-400 dark:text-gray-500 mt-2">
                    {task.attendees.map((a) => a.name).join(", ")}
                  </p>
                );
              }
              return null;
            })()}
          </div>
          {canEdit && task.has_web_edit && (
            <button
              onClick={handleRevert}
              disabled={reverting}
              className="p-1.5 rounded text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
              title="Revert to published version"
            >
              <RotateCcw size={16} />
            </button>
          )}
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Wrapper with Suspense (required for useSearchParams in static export)
// ---------------------------------------------------------------------------
export default function CalendarPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900">
          <p className="text-gray-500 dark:text-gray-400">Loading...</p>
        </div>
      }
    >
      <CalendarContent />
    </Suspense>
  );
}

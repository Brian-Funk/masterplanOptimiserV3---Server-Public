"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { useServiceAvailability } from "@/contexts/ServiceAvailabilityContext";
import { apiFetch } from "@/lib/api";
import { getApiUrl } from "@/lib/environment";
import {
  deriveUsernameFromDisplayName,
  parseTagList,
} from "@/lib/adminUsers";
import {
  deriveActivationCampaignSummary,
  matchesActivationFilter,
  type ActivationCampaignActionTarget,
} from "@/lib/activationCampaign";
import { withReauth } from "@/lib/reauth";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Logo } from "@/components/Logo";
import { PasskeyManager } from "@/components/PasskeyManager";
import { ActivationCampaignCard } from "@/components/ActivationCampaignCard";
import { SnapshotComparisonModal } from "@/components/SnapshotComparisonModal";
import { MobileActionSheet } from "@/components/MobileActionSheet";
import { MobileBottomNavigation } from "@/components/MobileBottomNavigation";
import { ComplianceEvidenceTab } from "@/components/ComplianceEvidenceTab";
import { PermittedDataInputNotice } from "@/components/PermittedDataInputNotice";
import {
  canManagePublicScheduleLinks,
  PublicScheduleLinksTab,
} from "@/components/PublicScheduleLinksTab";
import {
  compareSnapshotToCurrent,
  createUnavailableSnapshotComparison,
  type SnapshotComparisonSummary,
} from "@/lib/snapshotComparison";
import {
  LogOut,
  Plus,
  Copy,
  RefreshCw,
  ChevronDown,
  UserPlus,
  Key,
  Link2,
  Trash2,
  Upload,
  Users,
  Megaphone,
  Send,
  QrCode,
  Search,
  History,
  RotateCcw,
  Eye,
  ArrowLeft,
  Tag,
  CheckSquare,
  Shield,
  Info,
  Download,
  ChevronRight,
  X,
  AlertTriangle,
  FileText,
  ChevronLeft,
  Lock,
  Unlock,
  Pencil,
  Check,
  Share2,
  CalendarDays,
  MoreHorizontal,
  Activity,
  Server,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type ActivationPurpose =
  | "initial_setup"
  | "additional_passkey"
  | "credential_reset";

type ManagedPasskeyPurpose = Exclude<ActivationPurpose, "initial_setup">;

/** Return a human-readable label for a stored activation purpose. */
function activationPurposeLabel(purpose: ActivationPurpose | string): string {
  if (purpose === "additional_passkey") return "Additional passkey";
  if (purpose === "credential_reset") return "Passkey reset";
  return "Account activation";
}

/** Format an ISO date string (YYYY-MM-DD) or Date to Swiss format DD.MM.YYYY */
function fmtDate(value: string | null | undefined): string {
  if (!value) return "";
  const d = new Date(value);
  if (isNaN(d.getTime())) return value;
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const yyyy = d.getFullYear();
  return `${dd}.${mm}.${yyyy}`;
}

/** Format an ISO datetime string to Swiss format DD.MM.YYYY HH:MM */
function fmtDateTime(value: string | null | undefined): string {
  if (!value) return "";
  const d = new Date(value);
  if (isNaN(d.getTime())) return value;
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const yyyy = d.getFullYear();
  const hh = String(d.getHours()).padStart(2, "0");
  const min = String(d.getMinutes()).padStart(2, "0");
  return `${dd}.${mm}.${yyyy} ${hh}:${min}`;
}

function activationTokenFromUrl(value: string): string {
  const url = new URL(value, "https://activation.invalid");
  return (
    new URLSearchParams(url.hash.replace(/^#/, "")).get("token") ||
    url.searchParams.get("token") ||
    ""
  );
}

function activationQrPath(
  token: string,
  displayName: string,
  userId?: number,
  purpose: ActivationPurpose = "initial_setup",
): string {
  const params = new URLSearchParams({ token, name: displayName, purpose });
  if (userId) params.set("userId", String(userId));
  return `/activate/qr#${params.toString()}`;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface Event {
  id: number;
  name: string;
  location: string | null;
  start_date: string | null;
  end_date: string | null;
  status: string;
  created_at: string | null;
  purge_grace_days: number | null;
  purge_due_at: string | null;
  purge_case_request_id: string | null;
  purge_started_at: string | null;
}

interface AdminUser {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  is_root_admin: boolean;
  is_admin: boolean;
  is_issuer: boolean;
  can_edit: boolean;
  is_active: boolean;
  is_activated: boolean;
  has_activation_link: boolean;
  last_activation_link_created_at: string | null;
  last_activation_at: string | null;
  activation_email_status: string | null;
  activation_email_attempted_at: string | null;
  activation_email_accepted_at: string | null;
  activation_email_error_code: string | null;
  activation_email_error_message: string | null;
  activation_email_purpose: ActivationPurpose | null;
  has_valid_email: boolean;
  linked_person_id: number | null;
  event_id: number | null;
  tags: string[];
  last_login_at: string | null;
  created_at: string | null;
  deletion_requested_at: string | null;
}

interface ActivationDeliverySettings {
  configured: boolean;
  from_email: string | null;
  from_name: string | null;
  security: string | null;
  max_batch_size: number;
  expiry_hours: number;
}

interface ActivationEmailResult {
  user_id: number;
  display_name: string;
  email: string | null;
  status:
    | "sending"
    | "accepted"
    | "failed"
    | "unknown"
    | "skipped"
    | "not_attempted";
  message: string;
  delivery_id: number | null;
  error_code: string | null;
  expires_at: string | null;
  purpose: ActivationPurpose;
}

interface BatchActivationLinkSkipped {
  user_id: number;
  display_name: string;
  error_code: string;
  message: string;
}

export function responseMessage(data: unknown, fallback: string): string {
  if (!data || typeof data !== "object") return fallback;
  const record = data as Record<string, unknown>;
  if (typeof record.message === "string") return record.message;
  if (typeof record.detail === "string") return record.detail;
  if (record.detail && typeof record.detail === "object") {
    if (Array.isArray(record.detail)) {
      const validation = record.detail.find(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === "object" && typeof (item as Record<string, unknown>).msg === "string",
      );
      if (validation) {
        const location = Array.isArray(validation.loc) ? validation.loc : [];
        const field = location.length > 0 ? String(location[location.length - 1]) : "Input";
        const label = field.replaceAll("_", " ").replace(/^./, (value) => value.toUpperCase());
        return `${label}: ${String(validation.msg)}`;
      }
    }
    const detail = record.detail as Record<string, unknown>;
    if (typeof detail.message === "string") return detail.message;
  }
  return fallback;
}

interface PublishedPerson {
  id: number;
  external_person_id: number;
  first_name: string;
  last_name: string;
}

interface BulkUserDraft {
  id: string;
  display_name: string;
  username: string;
  email: string;
  can_edit: boolean;
  tags: string[];
  usernameTouched: boolean;
  error?: string;
}

interface BulkUserCreateError {
  index: number;
  username?: string | null;
  field: string;
  message: string;
}

interface BulkUserCreateResponse {
  created: AdminUser[];
  errors: BulkUserCreateError[];
}

/** Build a new editable row for the bulk user creation table. */
function createBulkUserDraft(): BulkUserDraft {
  return {
    id:
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : Math.random().toString(36).slice(2),
    display_name: "",
    username: "",
    email: "",
    can_edit: false,
    tags: [],
    usernameTouched: false,
  };
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

type AdminTab =
  | "events"
  | "users"
  | "announcements"
  | "history"
  | "public-links"
  | "security"
  | "privacy"
  | "ha"
  | "audit";

const ADMIN_TABS: AdminTab[] = [
  "events",
  "users",
  "announcements",
  "history",
  "public-links",
  "security",
  "privacy",
  "ha",
  "audit",
];

const EVENT_SCOPED_TABS: AdminTab[] = [
  "users",
  "announcements",
  "history",
  "public-links",
];

const ACTIVE_HA_SERVICE_STATES = new Set([
  "planned_handoff",
  "failover_wait",
  "promoting",
  "routing",
  "standby_shell",
]);

export default function AdminPage() {
  const router = useRouter();
  const {
    user,
    logout,
    isLoggingOut,
    isLoading: authLoading,
    authStatus,
  } = useAuth();

  const [events, setEvents] = useState<Event[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTabState] = useState<AdminTab>("events");
  const [showMobileMore, setShowMobileMore] = useState(false);
  const [showPasskeys, setShowPasskeys] = useState(false);
  const [selectedEvent, setSelectedEvent] = useState<number | "">("");

  const setTab = useCallback((nextTab: AdminTab) => {
    setTabState(nextTab);
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    url.searchParams.set("tab", nextTab);
    window.history.replaceState({}, "", url);
  }, []);

  // Auth guard  -  admin or issuer
  useEffect(() => {
    if (!authLoading && authStatus === "unauthenticated") {
      router.replace("/login");
      return;
    }
    if (
      !authLoading &&
      authStatus === "authenticated" &&
      user &&
      !user.is_admin &&
      !user.is_root_admin &&
      !user.is_issuer
    ) {
      router.replace(user.event_id ? `/calendar?event=${user.event_id}` : "/unassigned");
    }
  }, [authLoading, authStatus, user, router]);

  const isIssuerOnly =
    user?.is_issuer && !user?.is_admin && !user?.is_root_admin;
  const publicLinksEventId = user?.is_root_admin
    ? selectedEvent || null
    : user?.is_issuer
      ? user.event_id
      : null;

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const requestedTab = params.get("tab") as AdminTab | null;
    if (requestedTab && ADMIN_TABS.includes(requestedTab)) {
      setTabState(requestedTab);
    }
  }, []);

  const fetchData = useCallback(async (showInitialLoading = false) => {
    if (showInitialLoading) setLoading(true);
    try {
      if (isIssuerOnly) {
        // Issuers only fetch users (event-scoped on server side)
        const usRes = await apiFetch("/api/v1/admin/users");
        if (usRes.ok) setUsers(await usRes.json());
      } else {
        const [evRes, usRes] = await Promise.all([
          apiFetch("/api/v1/admin/events"),
          apiFetch("/api/v1/admin/users"),
        ]);
        if (evRes.ok) setEvents(await evRes.json());
        if (usRes.ok) setUsers(await usRes.json());
      }
    } catch (err) {
      console.error("Failed to load admin data:", err);
    } finally {
      if (showInitialLoading) setLoading(false);
    }
  }, [isIssuerOnly]);

  useEffect(() => {
    if (user && (user.is_admin || user.is_root_admin || user.is_issuer)) {
      fetchData(true);
    }
  }, [user, fetchData]);

  useEffect(() => {
    if (typeof window === "undefined" || selectedEvent) return;
    const eventParam = Number(new URLSearchParams(window.location.search).get("event"));
    if (eventParam > 0 && events.some((event) => event.id === eventParam)) {
      setSelectedEvent(eventParam);
    }
  }, [events, selectedEvent]);

  // Default tab for issuers (they don't have access to Events tab)
  useEffect(() => {
    if (isIssuerOnly && tab === "events") {
      setTab("users");
      return;
    }
    if (user && !user.is_root_admin && ["security", "privacy", "ha"].includes(tab)) {
      setTab(isIssuerOnly ? "users" : "events");
    }
  }, [isIssuerOnly, tab, user, setTab]);

  // Auto-select issuer's own event
  useEffect(() => {
    if (isIssuerOnly && user?.event_id && selectedEvent !== user.event_id) {
      setSelectedEvent(user.event_id);
    }
  }, [isIssuerOnly, user, selectedEvent]);

  const handleLogout = async () => {
    if (await logout()) router.replace("/login");
  };

  if (authLoading || loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900">
        <p className="text-gray-500 dark:text-gray-400">Loading...</p>
      </div>
    );
  }

  return (
    <div className={`min-h-screen bg-gray-50 dark:bg-gray-900 ${user?.is_root_admin ? "" : "mobile-page-with-nav"}`}>
      {/* Header */}
      <header className="sticky top-0 z-10 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 shadow-sm">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-2.5 sm:py-3 flex items-center justify-between">
          <div className="flex min-w-0 items-center gap-3">
            <span className="hidden sm:inline-flex"><Logo height={32} href="https://info.mp-opt.net" /></span>
            <div className="min-w-0">
              <h1 className="truncate text-lg font-semibold text-gray-900 dark:text-gray-100 leading-tight">
                {isIssuerOnly ? "Issuer Dashboard" : "Admin Dashboard"}
              </h1>
              {user && (
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  {user.display_name}
                </p>
              )}
            </div>
          </div>
          <div className={`items-center gap-1 ${user?.is_root_admin ? "flex" : "hidden md:flex"}`}>
            {isIssuerOnly && user?.event_id && (
              <button
                onClick={() => router.push(`/calendar?event=${user.event_id}`)}
                className="p-2 rounded-lg text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700 transition-colors"
                aria-label="Back to calendar"
                title="Back to calendar"
              >
                <ArrowLeft size={18} />
              </button>
            )}
            <ThemeToggle />
            <button
              onClick={() => router.push("/account/security")}
              className="p-2 rounded-lg text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700 transition-colors"
              aria-label="Account security"
              title="Account security"
            >
              <Shield size={18} />
            </button>
            {!isIssuerOnly && (
              <button
                onClick={() => setShowPasskeys(true)}
                className="p-2 rounded-lg text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700 transition-colors"
                aria-label="Manage passkeys"
                title="Manage passkeys"
              >
                <Key size={18} />
              </button>
            )}
            {user?.is_root_admin && (
              <button
                onClick={() => router.push("/admin/governance")}
                className="p-2 rounded-lg text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700 transition-colors"
                aria-label="Instance governance and legal notice"
                title="Instance governance and legal notice"
              >
                <FileText size={18} />
              </button>
            )}
            <button
              onClick={handleLogout}
              disabled={isLoggingOut}
              aria-busy={isLoggingOut}
              className="p-2 rounded-lg text-gray-500 hover:bg-gray-100 disabled:cursor-wait disabled:opacity-60 dark:text-gray-400 dark:hover:bg-gray-700 transition-colors"
              aria-label={isLoggingOut ? "Logging out" : "Logout"}
              title={isLoggingOut ? "Logging out…" : "Logout"}
            >
              {isLoggingOut ? <RefreshCw size={18} className="animate-spin" /> : <LogOut size={18} />}
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-4 sm:px-6 py-4 md:py-6 space-y-5 md:space-y-6">
        {user?.is_root_admin && (
          <Card className="flex flex-col gap-3 border-blue-200 bg-blue-50/60 p-4 dark:border-blue-800 dark:bg-blue-900/10 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="font-medium text-gray-900 dark:text-gray-100">
                Instance governance and public legal notice
              </p>
              <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
                Configure and publish the real data controller before inviting
                users. Publishing creates an immutable, signed policy version.
              </p>
            </div>
            <Button
              variant="outline"
              onClick={() => router.push("/admin/governance")}
            >
              Review governance
            </Button>
          </Card>
        )}
        {/* Full-width tab bar */}
        <div className={`${user?.is_root_admin ? "flex" : "hidden md:flex"} flex-wrap gap-1`}>
          {(
            [
                { key: "events", icon: <Plus size={15} />, label: "Events" },
                { key: "users", icon: <Users size={15} />, label: "Users" },
                {
                  key: "announcements",
                  icon: <Megaphone size={15} />,
                  label: "Announcements",
                },
                {
                  key: "history",
                  icon: <History size={15} />,
                  label: "History",
                },
                {
                  key: "public-links",
                  icon: <Share2 size={15} />,
                  label: "Public Links",
                },
                {
                  key: "security",
                  icon: <Shield size={15} />,
                  label: "Security",
                },
                {
                  key: "privacy",
                  icon: <Shield size={15} />,
                  label: "Deletion Evidence",
                },
                {
                  key: "ha",
                  icon: <Activity size={15} />,
                  label: "High Availability",
                },
                {
                  key: "audit",
                  icon: <FileText size={15} />,
                  label: "Audit Log",
                },
            ] as const
          )
            .filter((t) => {
                if (isIssuerOnly) {
                  return ["users", "announcements", "history", "public-links"].includes(t.key);
                }
                if (t.key === "public-links") {
                  return canManagePublicScheduleLinks(user);
                }
                if (t.key === "security" || t.key === "privacy" || t.key === "ha") {
                  return !!user?.is_root_admin;
                }
                return true;
            })
            .map((t) => (
                <button
                  key={t.key}
                  onClick={() => setTab(t.key)}
                  className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg whitespace-nowrap transition-colors ${
                    tab === t.key
                      ? "bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300"
                      : "text-gray-500 hover:text-gray-700 hover:bg-gray-100 dark:text-gray-400 dark:hover:text-gray-200 dark:hover:bg-gray-800"
                  }`}
                >
                  {t.icon}
                  {t.label}
                </button>
            ))}
        </div>

        {/* Event context is separate so it never compresses the root tab bar. */}
        {!isIssuerOnly && EVENT_SCOPED_TABS.includes(tab) &&
          !(tab === "public-links" && user?.is_issuer && !user?.is_root_admin) && (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-800 sm:justify-end">
            <label
              htmlFor="admin-event-context"
              className="text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400"
            >
              Event context
            </label>
            <select
              id="admin-event-context"
              value={selectedEvent}
              onChange={(e) => {
                const value = e.target.value ? Number(e.target.value) : "";
                setSelectedEvent(value);
                const url = new URL(window.location.href);
                if (value) {
                  url.searchParams.set("event", String(value));
                } else {
                  url.searchParams.delete("event");
                }
                window.history.replaceState({}, "", url);
              }}
              className="min-w-0 flex-1 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 sm:max-w-xs"
            >
              <option value="">
                {events.length === 0 ? "No events" : "All events"}
              </option>
              {events.map((ev) => (
                <option key={ev.id} value={ev.id}>
                  {ev.name}
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Tab content */}
        {tab === "events" && (
          <EventsTab events={events} onRefresh={fetchData} />
        )}
        {tab === "users" && (
          <UsersTab
            users={users}
            events={events}
            onRefresh={fetchData}
            selectedEventId={selectedEvent}
            isIssuerOnly={!!isIssuerOnly}
            isRootAdmin={!!user?.is_root_admin}
          />
        )}
        {tab === "announcements" && (
          <AnnouncementsTab selectedEventId={selectedEvent} />
        )}
        {tab === "history" && (
          <HistoryTab selectedEventId={selectedEvent} />
        )}
        {tab === "public-links" && (
          <PublicScheduleLinksTab eventId={publicLinksEventId || null} />
        )}
        {tab === "security" && user?.is_root_admin && <SecurityTab />}
        {tab === "privacy" && user?.is_root_admin && <ComplianceEvidenceTab events={events} />}
        {tab === "ha" && user?.is_root_admin && <HighAvailabilityTab />}
        {tab === "audit" && <AuditTab />}
      </main>

      <PasskeyManager
        open={showPasskeys}
        onClose={() => setShowPasskeys(false)}
      />

      {!user?.is_root_admin && (
        <MobileBottomNavigation
          items={[
            {
              id: "schedule",
              label: "Schedule",
              icon: <CalendarDays size={19} />,
              onSelect: () => {
                const targetEvent = user?.event_id || selectedEvent;
                router.push(targetEvent ? `/calendar?event=${targetEvent}` : "/calendar");
              },
            },
            {
              id: "people",
              label: "People",
              icon: <Users size={19} />,
              active: tab === "users",
              onSelect: () => setTab("users"),
            },
            {
              id: "updates",
              label: "Updates",
              icon: <Megaphone size={19} />,
              active: tab === "announcements",
              onSelect: () => setTab("announcements"),
            },
            {
              id: "more",
              label: "More",
              icon: <MoreHorizontal size={20} />,
              active: showMobileMore || !["users", "announcements"].includes(tab),
              onSelect: () => setShowMobileMore(true),
            },
          ]}
        />
      )}

      <MobileActionSheet
        open={showMobileMore}
        onClose={() => setShowMobileMore(false)}
        title="More"
        description="Management sections and account settings."
      >
        <div className="space-y-1">
          {!isIssuerOnly && (
            <button type="button" onClick={() => { setTab("events"); setShowMobileMore(false); }} className="flex min-h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-800"><Plus size={18} /> Events</button>
          )}
          <button type="button" onClick={() => { setTab("history"); setShowMobileMore(false); }} className="flex min-h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-800"><History size={18} /> History</button>
          {canManagePublicScheduleLinks(user) && (
            <button type="button" onClick={() => { setTab("public-links"); setShowMobileMore(false); }} className="flex min-h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-800"><Share2 size={18} /> Public links</button>
          )}
          {!isIssuerOnly && (
            <button type="button" onClick={() => { setTab("audit"); setShowMobileMore(false); }} className="flex min-h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-800"><FileText size={18} /> Audit log</button>
          )}
          <div className="my-2 border-t border-gray-200 dark:border-gray-700" />
          <div className="flex min-h-11 items-center justify-between rounded-lg px-3"><span className="text-sm">Appearance</span><ThemeToggle /></div>
          <button type="button" onClick={() => router.push("/account/security")} className="flex min-h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-800"><Shield size={18} /> Account security</button>
          <button type="button" onClick={handleLogout} disabled={isLoggingOut} aria-busy={isLoggingOut} className="flex min-h-11 w-full items-center gap-3 rounded-lg px-3 text-left text-sm text-red-600 hover:bg-red-50 disabled:cursor-wait disabled:opacity-60 dark:text-red-400 dark:hover:bg-red-900/20">{isLoggingOut ? <RefreshCw size={18} className="animate-spin" /> : <LogOut size={18} />} {isLoggingOut ? "Logging out…" : "Log out"}</button>
        </div>
      </MobileActionSheet>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Events Tab
// ---------------------------------------------------------------------------
function EventsTab({
  events,
  onRefresh,
}: {
  events: Event[];
  onRefresh: () => void;
}) {
  const [showCreate, setShowCreate] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [newEvent, setNewEvent] = useState({
    name: "",
    location: "",
    start_date: "",
    end_date: "",
  });
  const [createdSecret, setCreatedSecret] = useState("");
  const [eventPolicyAcknowledged, setEventPolicyAcknowledged] = useState(false);
  const [eventPolicy, setEventPolicy] = useState<{
    version: number;
    sha256: string;
    controller: string;
    purpose: string;
    allowed: string[];
    unsupported: string[];
  } | null>(null);
  const [creating, setCreating] = useState(false);
  const [regeneratedSecrets, setRegeneratedSecrets] = useState<
    Record<number, string>
  >({});
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [importResult, setImportResult] = useState<{
    secret: string;
    users: { id: number; display_name: string; activation_url: string }[];
  } | null>(null);
  const [importError, setImportError] = useState("");
  const [eventError, setEventError] = useState("");
  const [importLoading, setImportLoading] = useState(false);
  const router = useRouter();

  useEffect(() => {
    apiFetch("/api/v1/governance/public")
      .then((response) => response.ok ? response.json() : null)
      .then((policy) => {
        if (policy?.configured && policy.version && policy.content_sha256) {
          setEventPolicy({
            version: policy.version,
            sha256: policy.content_sha256,
            controller: policy.controller_legal_name || "The configured controller",
            purpose: policy.permitted_data?.purpose || "Operational event scheduling and access management",
            allowed: policy.permitted_data?.allowed || [],
            unsupported: policy.permitted_data?.unsupported || [],
          });
        }
      })
      .catch(() => setEventPolicy(null));
  }, []);

  const handleCreate = async () => {
    if (!newEvent.name.trim()) return;
    setEventError("");
    setCreating(true);
    try {
      const res = await apiFetch("/api/v1/admin/events", {
        method: "POST",
        body: JSON.stringify({
          name: newEvent.name,
          location: newEvent.location || null,
          start_date: newEvent.start_date || null,
          end_date: newEvent.end_date || null,
          policy_version: eventPolicyAcknowledged ? eventPolicy?.version : null,
          policy_sha256: eventPolicyAcknowledged ? eventPolicy?.sha256 : null,
        }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        setEventError(
          responseMessage(
            data,
            `The event could not be safely created (${res.status}).`,
          ),
        );
        return;
      }
      setCreatedSecret(data.publish_secret);
      setNewEvent({ name: "", location: "", start_date: "", end_date: "" });
      setEventPolicyAcknowledged(false);
      onRefresh();
    } catch {
      setEventError("The event could not be created. Please try again.");
    } finally {
      setCreating(false);
    }
  };

  const handleRegenerate = async (eventId: number) => {
    setEventError("");
    try {
      const res = await withReauth(() =>
        apiFetch(`/api/v1/admin/events/${eventId}/regenerate-secret`, {
          method: "POST",
          body: JSON.stringify({}),
        }),
      );
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        setEventError(
          responseMessage(
            data,
            `The publisher token could not be safely rotated (${res.status}).`,
          ),
        );
        return;
      }
      setRegeneratedSecrets((prev) => ({
        ...prev,
        [eventId]: data.publish_secret,
      }));
    } catch {
      setEventError(
        "Publisher token rotation was cancelled or reauthentication failed.",
      );
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
  };

  const handleDeleteEvent = async (eventId: number) => {
    try {
      const res = await withReauth(() =>
        apiFetch(`/api/v1/admin/events/${eventId}`, { method: "DELETE" }),
      );
      if (res.ok) {
        setConfirmDeleteId(null);
        onRefresh();
      }
    } catch {
      // User cancelled passkey prompt or re-auth failed
    }
  };

  const handleImportFile = async (file: File) => {
    setImportError("");
    setImportLoading(true);
    try {
      const text = await file.text();
      const payload = JSON.parse(text);
      const res = await withReauth(() =>
        apiFetch("/api/v1/admin/import-setup", {
          method: "POST",
          body: JSON.stringify(payload),
        }),
      );
      if (res.ok) {
        const data = await res.json();
        setImportResult({
          secret: data.publish_secret,
          users: data.users.map(
            (u: {
              user: { id: number; display_name: string };
              activation_url: string;
            }) => ({
              id: u.user.id,
              display_name: u.user.display_name,
              activation_url: window.location.origin + u.activation_url,
            }),
          ),
        });
        setShowImport(false);
        onRefresh();
      } else {
        const errData = await res.json().catch(() => null);
        let msg = responseMessage(errData, `Import failed (${res.status})`);
        if (errData?.detail) {
          if (typeof errData.detail === "string") {
            msg = errData.detail;
          } else if (Array.isArray(errData.detail)) {
            msg = errData.detail
              .map((e: { msg?: string; loc?: string[] }) =>
                e.msg
                  ? `${(e.loc || []).join(" → ")}: ${e.msg}`
                  : JSON.stringify(e),
              )
              .join("; ");
          }
        }
        setImportError(msg);
      }
    } catch (err) {
      setImportError(
        err instanceof Error ? err.message : "Failed to parse file",
      );
    } finally {
      setImportLoading(false);
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
          Manage Events
        </h2>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowImport(!showImport)}
          >
            <Upload size={14} /> Import Setup
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => setShowCreate(!showCreate)}
          >
            <Plus size={14} /> New Event
          </Button>
        </div>
      </div>

      {eventError && (
        <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-lg p-3 mb-4">
          <p className="text-sm text-red-800 dark:text-red-200">
            {eventError}
          </p>
        </div>
      )}

      {/* Import Setup */}
      {showImport && (
        <Card className="p-4 mb-4">
          <p className="text-sm text-gray-600 dark:text-gray-400 mb-3">
            Upload a JSON file exported from the desktop app to create an event
            with users.
          </p>
          <input
            type="file"
            accept=".json"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) handleImportFile(file);
            }}
            className="text-sm text-gray-600 dark:text-gray-400"
          />
          {importLoading && (
            <p className="text-sm text-blue-600 dark:text-blue-400 mt-2">
              Importing...
            </p>
          )}
          {importError && (
            <p className="text-sm text-red-600 dark:text-red-400 mt-2">
              {importError}
            </p>
          )}
        </Card>
      )}

      {/* Import result banner */}
      {importResult && (
        <div className="bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-800 rounded-lg p-4 mb-4">
          <p className="text-sm font-medium text-green-800 dark:text-green-200 mb-1">
            Import successful! Publish secret (shown once - save it now):
          </p>
          <div className="flex items-center gap-2 mb-3">
            <code className="flex-1 text-xs bg-green-100 dark:bg-green-900/50 px-2 py-1 rounded break-all text-green-900 dark:text-green-100">
              {importResult.secret}
            </code>
            <button
              onClick={() => copyToClipboard(importResult.secret)}
              className="p-1.5 rounded hover:bg-green-200 dark:hover:bg-green-800 transition-colors"
            >
              <Copy size={16} />
            </button>
          </div>
          {importResult.users.length > 0 && (
            <div>
              <p className="text-xs font-medium text-green-800 dark:text-green-200 mb-1">
                Activation links:
              </p>
              {importResult.users.map((u, i) => (
                <div key={i} className="flex items-center gap-2 mb-1">
                  <span className="text-xs text-green-800 dark:text-green-200 min-w-[80px]">
                    {u.display_name}:
                  </span>
                  <code className="flex-1 text-xs break-all text-green-900 dark:text-green-100">
                    {u.activation_url}
                  </code>
                  <button
                    onClick={() => copyToClipboard(u.activation_url)}
                    className="p-1"
                  >
                    <Copy size={12} />
                  </button>
                  <button
                    onClick={() => {
                      window.open(
                        activationQrPath(
                          activationTokenFromUrl(u.activation_url),
                          u.display_name,
                          u.id,
                        ),
                        "_blank",
                      );
                    }}
                    className="p-1"
                    title="Show QR code"
                  >
                    <QrCode size={12} />
                  </button>
                </div>
              ))}
            </div>
          )}
          <button
            onClick={() => setImportResult(null)}
            className="text-xs text-green-600 dark:text-green-400 mt-2 underline"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Create form */}
      {showCreate && (
        <Card className="p-4 mb-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
            <Input
              label="Participant-visible event name"
              value={newEvent.name}
              onChange={(e) =>
                setNewEvent((p) => ({ ...p, name: e.target.value }))
              }
              placeholder="e.g. Summer 2025"
            />
            <Input
              label="Participant-visible operational event location"
              value={newEvent.location}
              onChange={(e) =>
                setNewEvent((p) => ({ ...p, location: e.target.value }))
              }
              placeholder="Optional"
            />
            <Input
              label="Start date"
              type="date"
              value={newEvent.start_date}
              onChange={(e) =>
                setNewEvent((p) => ({ ...p, start_date: e.target.value }))
              }
            />
            <Input
              label="End date"
              type="date"
              value={newEvent.end_date}
              onChange={(e) =>
                setNewEvent((p) => ({ ...p, end_date: e.target.value }))
              }
            />
          </div>
          {eventPolicy && <section className="mb-3 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
            <h3 className="font-semibold">Permitted data for this event</h3>
            <p className="mt-1"><strong>Controller:</strong> {eventPolicy.controller}</p>
            <p className="mt-1"><strong>Purpose:</strong> {eventPolicy.purpose}. Optional fields must be necessary and justified by the controller.</p>
            <p className="mt-2 font-medium">Normally permitted</p>
            <ul className="list-disc pl-5">{eventPolicy.allowed.map((item) => <li key={item}>{item}</li>)}</ul>
            <p className="mt-2 font-medium">Unsupported</p>
            <ul className="list-disc pl-5">{eventPolicy.unsupported.map((item) => <li key={item}>{item}</li>)}</ul>
            <p className="mt-2">Field audiences are explicit: participant-visible, organisers only, or public. New events have no optional public, offline or integration feature enabled until separately configured.</p>
            <p className="mt-2">Policy version {eventPolicy.version}; SHA-256 <code className="break-all">{eventPolicy.sha256}</code>. <a className="underline" href={`${getApiUrl()}/api/v1/governance/public/versions/${eventPolicy.version}/data-policy.html`}>Open the permanent exact policy</a>.</p>
            <label className="mt-3 flex items-start gap-2 font-medium">
              <input type="checkbox" className="mt-1" checked={eventPolicyAcknowledged} onChange={(event) => setEventPolicyAcknowledged(event.target.checked)} />
              <span>I reviewed the applicable rules and will enter operational information only.</span>
            </label>
          </section>}
          <Button
            variant="primary"
            size="sm"
            onClick={handleCreate}
            disabled={creating || !newEvent.name.trim() || (Boolean(eventPolicy) && !eventPolicyAcknowledged)}
          >
            {creating ? "Creating..." : "Create Event"}
          </Button>
        </Card>
      )}

      {/* Created secret banner */}
      {createdSecret && (
        <div className="bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-800 rounded-lg p-4 mb-4">
          <p className="text-sm font-medium text-green-800 dark:text-green-200 mb-1">
            Publish secret (shown once - save it now):
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 text-xs bg-green-100 dark:bg-green-900/50 px-2 py-1 rounded break-all text-green-900 dark:text-green-100">
              {createdSecret}
            </code>
            <button
              onClick={() => copyToClipboard(createdSecret)}
              className="p-1.5 rounded hover:bg-green-200 dark:hover:bg-green-800 transition-colors"
            >
              <Copy size={16} />
            </button>
          </div>
          <button
            onClick={() => setCreatedSecret("")}
            className="text-xs text-green-600 dark:text-green-400 mt-2 underline"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Event list */}
      {events.length === 0 ? (
        <p className="text-gray-500 dark:text-gray-400 text-center py-8">
          No events yet.
        </p>
      ) : (
        <div className="space-y-3">
          {events.map((ev) => (
            <Card key={ev.id} className="p-4">
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="font-semibold text-gray-900 dark:text-gray-100">
                    {ev.name}
                  </h3>
                  <p className="text-sm text-gray-500 dark:text-gray-400">
                    {ev.location && `${ev.location} · `}
                    {ev.start_date && ev.end_date
                      ? `${fmtDate(ev.start_date)} → ${fmtDate(ev.end_date)}`
                      : fmtDate(ev.start_date) || "No dates"}
                  </p>
                  {ev.purge_case_request_id ? (
                    <p className="mt-1 text-xs font-medium text-amber-700 dark:text-amber-300">
                      Event deletion workflow queued {fmtDateTime(ev.purge_started_at)}.
                      Publishing is paused; continue in Privacy evidence.
                    </p>
                  ) : ev.purge_due_at ? (
                    <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                      Automatic deletion review is scheduled for {fmtDateTime(ev.purge_due_at)}
                      {ev.purge_grace_days ? ` after a ${ev.purge_grace_days}-day grace period.` : "."}
                    </p>
                  ) : (
                    <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                      Add an event end date to schedule automatic deletion review.
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => router.push(`/calendar?event=${ev.id}`)}
                  >
                    View
                  </Button>
                  <button
                    onClick={() => handleRegenerate(ev.id)}
                    className="p-1.5 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                    title="Regenerate publish secret"
                  >
                    <Key size={16} />
                  </button>
                  <button
                    onClick={() => setConfirmDeleteId(ev.id)}
                    className="p-1.5 rounded text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                    title="Delete event"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>
              {/* Delete confirmation */}
              {confirmDeleteId === ev.id && (
                <div className="mt-3 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded p-3">
                  <p className="text-sm text-red-800 dark:text-red-200 mb-2">
                    Delete &quot;{ev.name}&quot; and <strong>all</strong>{" "}
                    associated users, tasks, and edits? This cannot be undone.
                  </p>
                  <div className="flex gap-2">
                    <Button
                      variant="primary"
                      size="sm"
                      onClick={() => handleDeleteEvent(ev.id)}
                      className="!bg-red-600 hover:!bg-red-700"
                    >
                      Delete
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setConfirmDeleteId(null)}
                    >
                      Cancel
                    </Button>
                  </div>
                </div>
              )}
              {regeneratedSecrets[ev.id] && (
                <div className="mt-3 bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-800 rounded p-3">
                  <p className="text-xs font-medium text-amber-800 dark:text-amber-200 mb-1">
                    New publish secret (shown once):
                  </p>
                  <div className="flex items-center gap-2">
                    <code className="flex-1 text-xs break-all text-amber-900 dark:text-amber-100">
                      {regeneratedSecrets[ev.id]}
                    </code>
                    <button
                      onClick={() => copyToClipboard(regeneratedSecrets[ev.id])}
                      className="p-1"
                    >
                      <Copy size={14} />
                    </button>
                  </div>
                </div>
              )}
            </Card>
          ))}
        </div>
      )}

    </div>
  );
}

// ---------------------------------------------------------------------------
// Users Tab
// ---------------------------------------------------------------------------
function UsersTab({
  users,
  events,
  onRefresh,
  selectedEventId,
  isIssuerOnly,
  isRootAdmin,
}: {
  users: AdminUser[];
  events: Event[];
  onRefresh: () => void;
  selectedEventId: number | "";
  isIssuerOnly: boolean;
  isRootAdmin: boolean;
}) {
  const [showCreate, setShowCreate] = useState(false);
  const [newUser, setNewUser] = useState({
    username: "",
    display_name: "",
    email: "",
    event_id: "",
    can_edit: false,
    tags: "",
    usernameTouched: false,
  });
  const [createdLink, setCreatedLink] = useState("");
  const [createdLinkExpiresAt, setCreatedLinkExpiresAt] = useState("");
  const [createdUserId, setCreatedUserId] = useState<number | null>(null);
  const [createdUserName, setCreatedUserName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const [activationLinks, setActivationLinks] = useState<
    Record<number, string>
  >({});
  const [activationLinkPurposes, setActivationLinkPurposes] = useState<
    Record<number, ActivationPurpose>
  >({});
  const [activationLinkExpiries, setActivationLinkExpiries] = useState<
    Record<number, string>
  >({});
  const [linkInfo, setLinkInfo] = useState<
    Record<
      number,
      Array<{
        id: number;
        purpose: string;
        status: string;
        created_at: string | null;
        expires_at: string | null;
        used_at: string | null;
      }>
    >
  >({});
  const [expandedLinkUser, setExpandedLinkUser] = useState<number | null>(null);
  const [expandedDetailsUser, setExpandedDetailsUser] = useState<number | null>(
    null,
  );
  const [emailConfirmUserId, setEmailConfirmUserId] = useState<number | null>(
    null,
  );
  const [managedPasskeyPurpose, setManagedPasskeyPurpose] =
    useState<ManagedPasskeyPurpose | null>(null);
  const [emailBusy, setEmailBusy] = useState<Set<number>>(new Set());
  const [linkBusy, setLinkBusy] = useState<Set<number>>(new Set());
  const [emailDrafts, setEmailDrafts] = useState<Record<number, string>>({});
  const [emailResults, setEmailResults] = useState<
    Record<number, ActivationEmailResult>
  >({});
  const [emailActionErrors, setEmailActionErrors] = useState<
    Record<number, string>
  >({});
  const [deliverySettings, setDeliverySettings] =
    useState<ActivationDeliverySettings | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [removalError, setRemovalError] = useState<string | null>(null);
  const [tagInput, setTagInput] = useState<Record<number, string>>({});
  const [filterTag, setFilterTag] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [massTagInput, setMassTagInput] = useState("");
  const [massTagBusy, setMassTagBusy] = useState(false);
  const [tagManagerOpen, setTagManagerOpen] = useState(false);
  const [editingTag, setEditingTag] = useState<string | null>(null);
  const [replacementTag, setReplacementTag] = useState("");
  const [deletingTag, setDeletingTag] = useState<string | null>(null);
  const [recentlyUpdated, setRecentlyUpdated] = useState<{
    userId: number;
    message: string;
  } | null>(null);
  const [sortBy, setSortBy] = useState<"name" | "status" | "recent">("name");
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [batchLinks, setBatchLinks] = useState<
    Array<{
      user_id: number;
      username: string;
      display_name: string;
      activation_url: string;
      expires_at: string;
    }>
  >([]);
  const [batchLoading, setBatchLoading] = useState(false);
  const [batchWizardOpen, setBatchWizardOpen] = useState(false);
  const [batchStep, setBatchStep] = useState<"preview" | "format" | "result">(
    "preview",
  );
  const [batchFormat, setBatchFormat] = useState<"list" | "qr" | "email">(
    "list",
  );
  const [batchEmailResults, setBatchEmailResults] = useState<
    ActivationEmailResult[]
  >([]);
  const [batchLinkSkipped, setBatchLinkSkipped] = useState<
    BatchActivationLinkSkipped[]
  >([]);
  const [batchActionError, setBatchActionError] = useState("");
  const [batchQrBusy, setBatchQrBusy] = useState(false);
  const [bulkCreateOpen, setBulkCreateOpen] = useState(false);
  const [bulkCreateRows, setBulkCreateRows] = useState<BulkUserDraft[]>([
    createBulkUserDraft(),
    createBulkUserDraft(),
    createBulkUserDraft(),
  ]);
  const [bulkCreateEventId, setBulkCreateEventId] = useState<string>(
    selectedEventId ? String(selectedEventId) : "",
  );
  const [bulkTagText, setBulkTagText] = useState("");
  const [bulkCreateSaving, setBulkCreateSaving] = useState(false);
  const [bulkCreateResult, setBulkCreateResult] = useState<{
    created: number;
    errors: number;
  } | null>(null);

  const [persons, setPersons] = useState<Record<number, PublishedPerson[]>>({});

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    setSearchQuery(params.get("user_q") || "");
    setFilterTag(params.get("user_filter") || "");
    const requestedSort = params.get("user_sort");
    if (requestedSort === "status" || requestedSort === "recent") setSortBy(requestedSort);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (searchQuery) url.searchParams.set("user_q", searchQuery);
    else url.searchParams.delete("user_q");
    if (filterTag) url.searchParams.set("user_filter", filterTag);
    else url.searchParams.delete("user_filter");
    if (sortBy !== "name") url.searchParams.set("user_sort", sortBy);
    else url.searchParams.delete("user_sort");
    window.history.replaceState({}, "", url);
  }, [searchQuery, filterTag, sortBy]);

  useEffect(() => {
    setSelectedIds(new Set());
    setBatchWizardOpen(false);
    setBatchLinks([]);
    setBatchEmailResults([]);
    setBatchLinkSkipped([]);
    setBatchActionError("");
  }, [selectedEventId]);

  useEffect(() => {
    let cancelled = false;
    apiFetch("/api/v1/admin/activation-delivery/settings")
      .then(async (response) => {
        if (!response.ok || cancelled) return;
        const data: ActivationDeliverySettings = await response.json();
        if (!cancelled) setDeliverySettings(data);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  // Fetch persons for each event (for person linking dropdown)
  useEffect(() => {
    let cancelled = false;
    const eventIds = [
      ...new Set(users.map((u) => u.event_id).filter(Boolean)),
    ] as number[];
    void Promise.all(
      eventIds.map(async (eid) => {
        const res = await apiFetch(`/api/v1/calendar/${eid}/persons`);
        if (!res.ok) return null;
        const data: PublishedPerson[] = await res.json();
        return [eid, data] as const;
      }),
    ).then((results) => {
      if (cancelled) return;
      setPersons(Object.fromEntries(results.filter((entry) => entry !== null)));
    });
    return () => {
      cancelled = true;
    };
  }, [users]);

  const handleCreate = async () => {
    if (
      !newUser.username.trim() ||
      !newUser.display_name.trim() ||
      (!isIssuerOnly && !isRootAdmin && !newUser.event_id)
    )
      return;
    setCreating(true);
    setCreateError("");
    try {
      const payload: Record<string, unknown> = {
        username: newUser.username,
        display_name: newUser.display_name,
        email: newUser.email || null,
        is_admin: false,
        can_edit: newUser.can_edit,
        tags: parseTagList(newUser.tags),
      };
      if (!isIssuerOnly) {
        payload.event_id = newUser.event_id ? Number(newUser.event_id) : null;
      }
      const res = await apiFetch("/api/v1/admin/users", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => null);
      if (res.ok) {
        setCreatedLink(window.location.origin + data.activation_url);
        setCreatedLinkExpiresAt(data.expires_at || "");
        setCreatedUserId(data.user.id);
        setCreatedUserName(data.user.display_name || "New user");
        setNewUser({
          username: "",
          display_name: "",
          email: "",
          event_id: "",
          can_edit: false,
          tags: "",
          usernameTouched: false,
        });
        setShowCreate(false);
        onRefresh();
      } else {
        setCreateError(responseMessage(data, `The user could not be created (${res.status}).`));
      }
    } catch {
      setCreateError("The user could not be created. Please try again.");
    } finally {
      setCreating(false);
    }
  };

  /** Generate a purpose-bound manual access link after any required re-authentication. */
  const handleNewActivationLink = async (
    userId: number,
    purpose?: ManagedPasskeyPurpose,
  ): Promise<boolean> => {
    setLinkBusy((current) => new Set(current).add(userId));
    try {
      const requestLink = () =>
        apiFetch(`/api/v1/admin/users/${userId}/activation-link`, {
          method: "POST",
          body: JSON.stringify(purpose ? { purpose } : {}),
        });
      const target = users.find((candidate) => candidate.id === userId);
      const res = target?.is_activated
        ? await withReauth(requestLink)
        : await requestLink();
      if (res.ok) {
        const data = await res.json();
        setActivationLinks((prev) => ({
          ...prev,
          [userId]: window.location.origin + data.activation_url,
        }));
        setActivationLinkPurposes((current) => ({
          ...current,
          [userId]: data.purpose || purpose || "initial_setup",
        }));
        setActivationLinkExpiries((current) => ({
          ...current,
          [userId]: data.expires_at || "",
        }));
        return true;
      }
    } catch {
      // The passkey prompt was cancelled or re-authentication failed.
    } finally {
      setLinkBusy((current) => {
        const next = new Set(current);
        next.delete(userId);
        return next;
      });
    }
    return false;
  };

  const handleToggleActivationLink = (userId: number) => {
    if (activationLinks[userId]) {
      // Collapse: remove the link from state
      setActivationLinks((prev) => {
        const next = { ...prev };
        delete next[userId];
        return next;
      });
      setActivationLinkExpiries((prev) => {
        const next = { ...prev };
        delete next[userId];
        return next;
      });
    } else {
      // Expand: generate a new link
      handleNewActivationLink(userId);
    }
  };

  /** Send or retry one user's activation email after explicit confirmation. */
  const handleSendActivationEmail = async (
    user: AdminUser,
    requestedPurpose?: ManagedPasskeyPurpose,
  ) => {
    setEmailBusy((current) => new Set(current).add(user.id));
    setEmailActionErrors((current) => {
      const next = { ...current };
      delete next[user.id];
      return next;
    });
    let completed = false;
    try {
      const purpose: ActivationPurpose = user.is_activated
        ? requestedPurpose || "credential_reset"
        : "initial_setup";
      const previous = emailResults[user.id];
      const send = () =>
        apiFetch(`/api/v1/admin/users/${user.id}/activation-email`, {
          method: "POST",
          body: JSON.stringify({
            retry_of_delivery_id:
              previous &&
              previous.status !== "accepted" &&
              previous.purpose === purpose
                ? previous.delivery_id
                : undefined,
            purpose: user.is_activated ? purpose : undefined,
          }),
        });
      const response = user.is_activated ? await withReauth(send) : await send();
      const data: unknown = await response.json().catch(() => ({}));
      if (response.ok) {
        const result = data as ActivationEmailResult;
        setEmailResults((current) => ({ ...current, [user.id]: result }));
        setActivationLinks((current) => {
          const next = { ...current };
          delete next[user.id];
          return next;
        });
        if (createdUserId === user.id) setCreatedLink("");
        setRecentlyUpdated({
          userId: user.id,
          message: result.status === "accepted"
            ? `Email accepted for ${user.display_name}.`
            : `${user.display_name}: ${result.message}`,
        });
        onRefresh();
        completed = true;
      } else {
        setEmailActionErrors((current) => ({
          ...current,
          [user.id]: responseMessage(
            data,
            "The activation email could not be sent. Try again.",
          ),
        }));
      }
    } catch (error: unknown) {
      setEmailActionErrors((current) => ({
        ...current,
        [user.id]:
          error instanceof Error
            ? error.message
            : "The activation email could not be sent. Try again.",
      }));
    } finally {
      setEmailBusy((current) => {
        const next = new Set(current);
        next.delete(user.id);
        return next;
      });
      if (completed) {
        setEmailConfirmUserId(null);
        setManagedPasskeyPurpose(null);
      }
    }
  };

  const handleDeleteUser = async (userId: number) => {
    setRemovalError(null);
    try {
      const res = await withReauth(() =>
        apiFetch(`/api/v1/admin/users/${userId}`, {
          method: "DELETE",
          body: JSON.stringify({}),
        }),
      );
      if (res.ok) {
        setConfirmDeleteId(null);
        onRefresh();
      } else {
        const data = await res.json().catch(() => ({}));
        const detail = data?.detail;
        setRemovalError(
          typeof detail === "object" && typeof detail?.message === "string"
            ? detail.message
            : responseMessage(data, "The account could not be removed."),
        );
      }
    } catch {
      // User cancelled passkey prompt or re-auth failed
    }
  };

  const [gdprBusy, setGdprBusy] = useState<Record<number, boolean>>({});

  const handleGdprExport = async (userId: number, displayName: string) => {
    setGdprBusy((p) => ({ ...p, [userId]: true }));
    try {
      const res = await withReauth(() =>
        apiFetch(`/api/v1/admin/users/${userId}/export`),
      );
      if (res.ok) {
        const data = await res.json();
        const blob = new Blob([JSON.stringify(data, null, 2)], {
          type: "application/json",
        });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `gdpr-export-${displayName.replace(/\s+/g, "_")}-${userId}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    } finally {
      setGdprBusy((p) => ({ ...p, [userId]: false }));
    }
  };

  const [gdprConfirmId, setGdprConfirmId] = useState<number | null>(null);
  const [gdprInfoOpen, setGdprInfoOpen] = useState(false);

  const handleGdprAnonymise = async (userId: number) => {
    try {
      const res = await withReauth(() =>
        apiFetch(`/api/v1/admin/users/${userId}/gdpr-delete`, {
          method: "DELETE",
          body: JSON.stringify({}),
        }),
      );
      if (res.ok) {
        setGdprConfirmId(null);
        onRefresh();
      }
    } catch {
      // User cancelled passkey prompt or re-auth failed
    }
  };

  const handleDismissDeletion = async (userId: number) => {
    const res = await apiFetch(
      `/api/v1/admin/users/${userId}/deletion-request`,
      { method: "DELETE", body: JSON.stringify({}) },
    );
    if (res.ok) {
      setGdprConfirmId(null);
      onRefresh();
    }
  };

  const handleLinkPerson = async (userId: number, personId: number | null) => {
    await apiFetch(`/api/v1/admin/users/${userId}/link-person`, {
      method: "PUT",
      body: JSON.stringify({ person_id: personId }),
    });
    onRefresh();
  };

  const handleToggleCanEdit = async (userId: number, canEdit: boolean) => {
    await apiFetch(`/api/v1/admin/users/${userId}`, {
      method: "PUT",
      body: JSON.stringify({ can_edit: canEdit }),
    });
    onRefresh();
  };

  /** Update one user's delivery address from the collapsed account details. */
  const handleUpdateUserEmail = async (user: AdminUser) => {
    const email = (emailDrafts[user.id] ?? user.email ?? "").trim();
    const response = await apiFetch(`/api/v1/admin/users/${user.id}`, {
      method: "PUT",
      body: JSON.stringify({ email: email || null }),
    });
    if (response.ok) {
      setEmailDrafts((current) => {
        const next = { ...current };
        delete next[user.id];
        return next;
      });
      onRefresh();
    }
  };

  const handleToggleIssuer = async (userId: number, isIssuer: boolean) => {
    try {
      const res = await withReauth(() =>
        apiFetch(`/api/v1/admin/users/${userId}`, {
          method: "PUT",
          body: JSON.stringify({ is_issuer: isIssuer }),
        }),
      );
      if (res.ok) onRefresh();
    } catch {
      // The passkey prompt was cancelled or re-authentication failed.
    }
  };

  const handleUpdateUserEvent = async (userId: number, eventId: number | null) => {
    try {
      const response = await withReauth(() =>
        apiFetch(`/api/v1/admin/users/${userId}`, {
          method: "PUT",
          body: JSON.stringify({ event_id: eventId }),
        }),
      );
      if (response.ok) onRefresh();
    } catch {
      // User cancelled passkey re-authentication or the reassignment failed.
    }
  };

  const handleAddTag = async (userId: number, tag: string) => {
    if (!tag.trim()) return;
    await apiFetch("/api/v1/admin/user-tags/actions", {
      method: "PUT",
      body: JSON.stringify({ action: "add", tag: tag.trim(), user_ids: [userId] }),
    });
    onRefresh();
  };

  const handleRemoveTag = async (userId: number, tag: string) => {
    await apiFetch("/api/v1/admin/user-tags/actions", {
      method: "PUT",
      body: JSON.stringify({ action: "remove", tag, user_ids: [userId] }),
    });
    onRefresh();
  };

  const handleShowLinkInfo = async (userId: number) => {
    if (expandedLinkUser === userId) {
      setExpandedLinkUser(null);
      return;
    }
    const res = await apiFetch(
      `/api/v1/admin/users/${userId}/activation-links`,
    );
    if (res.ok) {
      const data = await res.json();
      setLinkInfo((prev) => ({ ...prev, [userId]: data }));
      setExpandedLinkUser(userId);
    }
  };

  const handleInvalidateLink = async (userId: number, linkId: number) => {
    const res = await apiFetch(
      `/api/v1/admin/users/${userId}/activation-links/${linkId}`,
      { method: "DELETE" },
    );
    if (res.ok) {
      // Refresh link info
      handleShowLinkInfo(userId);
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
  };

  const handleBatchActivation = async () => {
    setBatchLoading(true);
    setBatchActionError("");
    setBatchLinkSkipped([]);
    try {
      const pendingUsers = batchWizardTargets.filter((u) => !u.is_activated);
      const targetIds = pendingUsers.map((u) => u.id);
      if (targetIds.length === 0) return;
      const res = await apiFetch("/api/v1/admin/batch-activation-links", {
        method: "POST",
        body: JSON.stringify({
          user_ids: targetIds,
        }),
      });
      const data: unknown = await res.json().catch(() => ({}));
      if (res.ok) {
        const result = data as {
          links: typeof batchLinks;
          skipped?: BatchActivationLinkSkipped[];
        };
        setBatchLinks(result.links);
        setBatchLinkSkipped(result.skipped ?? []);
        setBatchStep("result");
      } else {
        setBatchActionError(
          responseMessage(data, "The activation links could not be generated."),
        );
      }
    } catch (error: unknown) {
      setBatchActionError(
        error instanceof Error
          ? error.message
          : "The activation links could not be generated.",
      );
    } finally {
      setBatchLoading(false);
    }
  };

  /** Email fresh activation links to the explicitly selected pending users. */
  const handleBatchActivationEmail = async (overrideIds?: number[]) => {
    const targetIds =
      overrideIds ?? batchWizardTargets.map((user) => user.id);
    if (targetIds.length === 0 || targetIds.length > 50) return;
    setBatchLoading(true);
    setBatchActionError("");
    if (!overrideIds) setBatchEmailResults([]);
    try {
      const response = await apiFetch("/api/v1/admin/batch-activation-emails", {
        method: "POST",
        body: JSON.stringify({ user_ids: targetIds }),
      });
      const payload: unknown = await response.json().catch(() => ({}));
      if (response.ok) {
        const data = payload as { results: ActivationEmailResult[] };
        setBatchEmailResults((current) => {
          if (!overrideIds) return data.results;
          const replacements = new Map(
            data.results.map((result) => [result.user_id, result]),
          );
          const merged = current.map(
            (result) => replacements.get(result.user_id) ?? result,
          );
          data.results.forEach((result) => {
            if (!merged.some((existing) => existing.user_id === result.user_id)) {
              merged.push(result);
            }
          });
          return merged;
        });
        setEmailResults((current) => {
          const next = { ...current };
          data.results.forEach((result) => {
            next[result.user_id] = result;
          });
          return next;
        });
        setActivationLinks((current) => {
          const next = { ...current };
          data.results.forEach((result) => delete next[result.user_id]);
          return next;
        });
        setBatchStep("result");
        onRefresh();
      } else {
        setBatchActionError(
          responseMessage(payload, "The email batch could not be sent."),
        );
      }
    } catch (error: unknown) {
      setBatchActionError(
        error instanceof Error
          ? error.message
          : "The email batch could not be sent.",
      );
    } finally {
      setBatchLoading(false);
    }
  };

  const handleCopyAllLinks = () => {
    const text = batchLinks
      .map(
        (l) =>
          `${l.display_name}\t${window.location.origin}${l.activation_url}`,
      )
      .join("\n");
    navigator.clipboard.writeText(text);
  };

  /** Download canonical server-rendered QR cards for the generated links. */
  const handleDownloadQrZip = async () => {
    if (batchLinks.length === 0) return;
    setBatchQrBusy(true);
    setBatchActionError("");
    try {
      const response = await apiFetch("/api/v1/admin/activation-qr-codes", {
        method: "POST",
        body: JSON.stringify({
          items: batchLinks.map((link) => ({
            user_id: link.user_id,
            token: activationTokenFromUrl(link.activation_url),
          })),
        }),
      });
      if (!response.ok) {
        const payload: unknown = await response.json().catch(() => ({}));
        throw new Error(
          responseMessage(payload, "The QR cards could not be downloaded."),
        );
      }

      const zipBlob = await response.blob();
      const objectUrl = URL.createObjectURL(zipBlob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = "activation-qr-codes.zip";
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
    } catch (error: unknown) {
      setBatchActionError(
        error instanceof Error
          ? error.message
          : "The QR cards could not be downloaded.",
      );
    } finally {
      setBatchQrBusy(false);
    }
  };

  const handleMassAddTag = async (targets: AdminUser[], tag: string) => {
    if (!tag || targets.length === 0) return;
    setMassTagBusy(true);
    try {
      await apiFetch("/api/v1/admin/user-tags/actions", {
        method: "PUT",
        body: JSON.stringify({ action: "add", tag, user_ids: targets.map((user) => user.id) }),
      });
      setMassTagInput("");
      onRefresh();
    } finally {
      setMassTagBusy(false);
    }
  };

  const handleMassRemoveTag = async (targets: AdminUser[], tag: string) => {
    if (!tag || targets.length === 0) return;
    setMassTagBusy(true);
    try {
      await apiFetch("/api/v1/admin/user-tags/actions", {
        method: "PUT",
        body: JSON.stringify({ action: "remove", tag, user_ids: targets.map((user) => user.id) }),
      });
      setMassTagInput("");
      onRefresh();
    } finally {
      setMassTagBusy(false);
    }
  };

  const handleEventTagAction = async (action: "rename" | "delete", tag: string) => {
    const eventId = selectedEventId || (isIssuerOnly ? activationEventUsers[0]?.event_id : null);
    if (!eventId) return;
    setMassTagBusy(true);
    try {
      const response = await apiFetch("/api/v1/admin/user-tags/actions", {
        method: "PUT",
        body: JSON.stringify({
          action,
          tag,
          event_id: Number(eventId),
          ...(action === "rename" ? { replacement: replacementTag.trim() } : {}),
        }),
      });
      if (response.ok) {
        if (filterTag === tag) setFilterTag(action === "rename" ? replacementTag.trim() : "");
        setEditingTag(null);
        setDeletingTag(null);
        setReplacementTag("");
        onRefresh();
      }
    } finally {
      setMassTagBusy(false);
    }
  };

  const openBulkCreate = () => {
    setBulkCreateRows([
      createBulkUserDraft(),
      createBulkUserDraft(),
      createBulkUserDraft(),
    ]);
    setBulkCreateEventId(selectedEventId ? String(selectedEventId) : "");
    setBulkTagText("");
    setBulkCreateResult(null);
    setBulkCreateOpen(true);
  };

  const updateBulkRow = (
    rowId: string,
    field: keyof Pick<
      BulkUserDraft,
      "display_name" | "username" | "email" | "can_edit" | "tags"
    >,
    value: string | boolean | string[],
  ) => {
    setBulkCreateRows((rows) =>
      rows.map((row) => {
        if (row.id !== rowId) return row;
        const next = { ...row, error: undefined };
        if (field === "display_name" && typeof value === "string") {
          next.display_name = value;
          if (!next.usernameTouched) {
            next.username = deriveUsernameFromDisplayName(value);
          }
        } else if (field === "username" && typeof value === "string") {
          next.username = value;
          next.usernameTouched = true;
        } else if (field === "email" && typeof value === "string") {
          next.email = value;
        } else if (field === "can_edit" && typeof value === "boolean") {
          next.can_edit = value;
        } else if (field === "tags" && Array.isArray(value)) {
          next.tags = value;
        }
        return next;
      }),
    );
  };

  const removeBulkRow = (rowId: string) => {
    setBulkCreateRows((rows) =>
      rows.length === 1
        ? [
            {
              ...rows[0],
              display_name: "",
              username: "",
              email: "",
              tags: [],
              error: undefined,
            },
          ]
        : rows.filter((row) => row.id !== rowId),
    );
  };

  const applyBulkTagsToRows = () => {
    const tags = parseTagList(bulkTagText);
    if (tags.length === 0) return;
    setBulkCreateRows((rows) =>
      rows.map((row) => ({
        ...row,
        tags: Array.from(new Set([...(row.tags || []), ...tags])),
      })),
    );
  };

  const handleBulkCreateUsers = async () => {
    const submittedRows = bulkCreateRows
      .map((row, originalIndex) => ({ row, originalIndex }))
      .filter(
        ({ row }) =>
          row.display_name.trim() || row.username.trim() || row.email.trim(),
      );
    const duplicateNames = new Set<string>();
    const seenNames = new Set<string>();
    submittedRows.forEach(({ row }) => {
      const username = row.username.trim();
      if (!username) return;
      if (seenNames.has(username)) duplicateNames.add(username);
      seenNames.add(username);
    });

    const localErrors = new Map<string, string>();
    bulkCreateRows.forEach((row) => {
      const username = row.username.trim();
      if (!(row.display_name.trim() || username || row.email.trim())) return;
      if (!row.display_name.trim()) {
        localErrors.set(row.id, "Display name is required.");
      } else if (!username) {
        localErrors.set(row.id, "Username is required.");
      } else if (duplicateNames.has(username)) {
        localErrors.set(row.id, "Username is duplicated in this batch.");
      }
    });
    setBulkCreateRows((rows) =>
      rows.map((row) => {
        return { ...row, error: localErrors.get(row.id) };
      }),
    );
    if (localErrors.size > 0 || submittedRows.length === 0) return;
    if (!isIssuerOnly && !bulkCreateEventId) return;

    setBulkCreateSaving(true);
    try {
      const res = await apiFetch("/api/v1/admin/users/bulk", {
        method: "POST",
        body: JSON.stringify({
          event_id: isIssuerOnly ? null : Number(bulkCreateEventId),
          bulk_tags: parseTagList(bulkTagText),
          users: submittedRows.map(({ row }) => ({
            username: row.username.trim(),
            display_name: row.display_name.trim(),
            email: row.email.trim() || null,
            can_edit: row.can_edit,
            tags: row.tags,
          })),
        }),
      });
      if (!res.ok) return;
      const data: BulkUserCreateResponse = await res.json();
      const failedOriginalIndexes = new Set(
        data.errors.map((error) => submittedRows[error.index]?.originalIndex),
      );
      const errorByOriginalIndex = new Map(
        data.errors.map((error) => [
          submittedRows[error.index]?.originalIndex,
          error.message,
        ]),
      );
      setBulkCreateRows((rows) => {
        const failedRows = rows
          .map((row, index) => ({ row, index }))
          .filter(({ index }) => failedOriginalIndexes.has(index))
          .map(({ row, index }) => ({
            ...row,
            error: errorByOriginalIndex.get(index),
          }));
        return failedRows.length > 0 ? failedRows : [createBulkUserDraft()];
      });
      setSelectedIds(new Set(data.created.map((user) => user.id)));
      setBulkCreateResult({
        created: data.created.length,
        errors: data.errors.length,
      });
      onRefresh();
      if (data.errors.length === 0) {
        setBulkCreateOpen(false);
      }
    } finally {
      setBulkCreateSaving(false);
    }
  };

  // Deterministic colour palette for tag pills
  const TAG_COLOURS = [
    "bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300",
    "bg-sky-100 dark:bg-sky-900/40 text-sky-700 dark:text-sky-300",
    "bg-teal-100 dark:bg-teal-900/40 text-teal-700 dark:text-teal-300",
    "bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-300",
    "bg-orange-100 dark:bg-orange-900/40 text-orange-700 dark:text-orange-300",
    "bg-fuchsia-100 dark:bg-fuchsia-900/40 text-fuchsia-700 dark:text-fuchsia-300",
    "bg-lime-100 dark:bg-lime-900/40 text-lime-700 dark:text-lime-300",
    "bg-cyan-100 dark:bg-cyan-900/40 text-cyan-700 dark:text-cyan-300",
  ];
  const tagColour = (tag: string) => {
    let h = 0;
    for (let i = 0; i < tag.length; i++) h = (h * 31 + tag.charCodeAt(i)) | 0;
    return TAG_COLOURS[Math.abs(h) % TAG_COLOURS.length];
  };

  const activationEventUsers = users.filter(
    (u) => !selectedEventId || u.event_id === Number(selectedEventId),
  );
  const activationSummary =
    deriveActivationCampaignSummary(activationEventUsers);

  const handleActivationPrimaryAction = (
    target: ActivationCampaignActionTarget,
  ) => {
    if (target === "add_users") {
      setShowCreate(true);
      return;
    }
    if (target === "generate_missing_links") {
      const missingIds = activationEventUsers
        .filter((u) => !u.is_activated && !u.has_activation_link && u.is_active)
        .map((u) => u.id);
      setFilterTag("__needs_link");
      setSearchQuery("");
      setSelectedIds(new Set(missingIds));
      setBatchLinks([]);
      setBatchStep("preview");
      setBatchFormat("list");
      setBatchWizardOpen(true);
      return;
    }
    setFilterTag("__pending");
    setSearchQuery("");
  };

  // Pre-compute filtered user list for mass-tag bar and rendering
  const filteredUsers = users.filter((u) => {
    if (selectedEventId && u.event_id !== Number(selectedEventId)) return false;
    if (
      [
        "__activated",
        "__pending",
        "__needs_link",
        "__has_link",
        "__not_linked",
        "__editor",
        "__attention",
        "__email_failed",
        "__missing_email",
      ].includes(filterTag)
    ) {
      if (!matchesActivationFilter(u, filterTag)) return false;
    } else if (filterTag === "__deletion" && !u.deletion_requested_at)
      return false;
    else if (
      filterTag &&
      !filterTag.startsWith("__") &&
      !(u.tags || []).includes(filterTag)
    )
      return false;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      const haystack = [u.display_name, u.username, u.email, ...(u.tags || [])]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  }).sort((left, right) => {
    if (sortBy === "recent") {
      return (Date.parse(right.created_at || "") || 0) - (Date.parse(left.created_at || "") || 0);
    }
    if (sortBy === "status") {
      const rank = (candidate: AdminUser) => !candidate.is_active ? 3
        : candidate.deletion_requested_at ? 2
          : !candidate.is_activated ? 1 : 0;
      const difference = rank(right) - rank(left);
      if (difference) return difference;
    }
    return left.display_name.localeCompare(right.display_name, undefined, { sensitivity: "base" });
  });

  const managedEventId = selectedEventId || (isIssuerOnly ? activationEventUsers[0]?.event_id : null);
  const managedEventUsers = managedEventId
    ? users.filter((candidate) => candidate.event_id === Number(managedEventId))
    : [];
  const managedTags = [...new Set(managedEventUsers.flatMap((candidate) => candidate.tags || []))]
    .sort()
    .map((tag) => ({
      tag,
      count: managedEventUsers.filter((candidate) => (candidate.tags || []).includes(tag)).length,
    }));
  const recentlyUpdatedVisible = recentlyUpdated
    ? filteredUsers.some((candidate) => candidate.id === recentlyUpdated.userId)
    : false;

  const toggleSelect = (id: number) =>
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const toggleSelectAll = () => {
    const ids = filteredUsers.map((u) => u.id);
    const allSelected =
      ids.length > 0 && ids.every((id) => selectedIds.has(id));
    setSelectedIds((current) => {
      const next = new Set(current);
      ids.forEach((id) => {
        if (allSelected) next.delete(id);
        else next.add(id);
      });
      return next;
    });
  };
  const massTargets =
    selectedIds.size > 0
      ? filteredUsers.filter((u) => selectedIds.has(u.id))
      : filteredUsers;

  // Batch actions are always explicit and preserve selections across filters.
  const batchWizardTargets = activationEventUsers.filter((user) =>
    selectedIds.has(user.id),
  );
  const batchPendingCount = batchWizardTargets.filter(
    (u) => !u.is_activated,
  ).length;

  const openBatchWizard = () => {
    if (selectedIds.size === 0) return;
    setBatchLinks([]);
    setBatchEmailResults([]);
    setBatchLinkSkipped([]);
    setBatchActionError("");
    setBatchStep("preview");
    setBatchFormat("list");
    setBatchWizardOpen(true);
  };

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            People
          </h2>
          <p className="mt-0.5 text-sm text-gray-500 dark:text-gray-400">
            Manage access and activation for {filteredUsers.length}
            {filteredUsers.length !== users.length ? ` of ${users.length}` : ""} people.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:flex sm:items-center">
          <Button
            variant="outline"
            size="sm"
            onClick={openBatchWizard}
            disabled={selectedIds.size === 0}
          >
            <Send size={14} /> Distribute
          </Button>
          <Button variant="outline" size="sm" onClick={openBulkCreate}>
            <Users size={14} /> Bulk Users
          </Button>
          <Button variant="outline" size="sm" onClick={() => setTagManagerOpen(true)}>
            <Tag size={14} /> Manage tags
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => {
              setNewUser((current) => ({
                ...current,
                event_id: current.event_id || (selectedEventId ? String(selectedEventId) : ""),
              }));
              setShowCreate(true);
            }}
          >
            <UserPlus size={14} /> New User
          </Button>
        </div>
      </div>

      {/* Create form */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center sm:p-4">
        <Card className="max-h-[92dvh] w-full max-w-2xl overflow-y-auto rounded-b-none p-5 sm:rounded-b-xl">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div><h3 className="text-base font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2"><UserPlus size={16} />New user</h3><p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Start with the person&apos;s name; the username is derived and remains editable.</p></div>
            <button type="button" onClick={() => setShowCreate(false)} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700" aria-label="Close new user"><X size={16} /></button>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
            <Input
              label="Display name"
              value={newUser.display_name}
              onChange={(e) => setNewUser((current) => ({
                ...current,
                display_name: e.target.value,
                username: current.usernameTouched
                  ? current.username
                  : deriveUsernameFromDisplayName(e.target.value),
              }))}
              placeholder="e.g. Jo Smith"
            />
            <Input
              label="Username"
              value={newUser.username}
              onChange={(e) => setNewUser((current) => ({ ...current, username: e.target.value, usernameTouched: true }))}
              placeholder="e.g. jo.smith"
            />
            <Input
              label="Email"
              type="email"
              value={newUser.email}
              onChange={(e) =>
                setNewUser((p) => ({ ...p, email: e.target.value }))
              }
              placeholder="Optional"
            />
            {!isIssuerOnly && (
              <div className="w-full">
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                  Event
                </label>
                <select
                  value={newUser.event_id}
                  onChange={(e) =>
                    setNewUser((p) => ({ ...p, event_id: e.target.value }))
                  }
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 text-sm"
                >
                  <option value="">
                    {isRootAdmin ? "No event yet (unassigned)" : "Select event..."}
                  </option>
                  {events.map((ev) => (
                    <option key={ev.id} value={ev.id}>
                      {ev.name}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <Input
              label="Initial tags"
              value={newUser.tags}
              onChange={(e) => setNewUser((current) => ({ ...current, tags: e.target.value }))}
              placeholder="team, role, shift"
            />
          </div>
          {createError && (
            <div role="alert" className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-800 dark:bg-red-900/20 dark:text-red-200">
              {createError}
            </div>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
              <input
                type="checkbox"
                checked={newUser.can_edit}
                onChange={(e) =>
                  setNewUser((p) => ({ ...p, can_edit: e.target.checked }))
                }
                className="rounded"
              />
              Can edit
            </label>
            <div className="flex gap-2"><Button variant="outline" size="sm" onClick={() => setShowCreate(false)}>Cancel</Button><Button
              variant="primary"
              size="sm"
              onClick={handleCreate}
              disabled={
                creating ||
                !newUser.username.trim() ||
                !newUser.display_name.trim() ||
                (!isIssuerOnly && !isRootAdmin && !newUser.event_id)
              }
            >
              {creating ? "Creating..." : "Create User"}
            </Button></div>
          </div>
        </Card>
        </div>
      )}

      {/* Created activation link banner */}
      {createdLink && (
        <div className="rounded-xl border border-green-200 bg-green-50/70 p-4 dark:border-green-800 dark:bg-green-900/20">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-sm font-semibold text-green-900 dark:text-green-100">
                Activation link ready
              </p>
              <p className="mt-0.5 text-xs text-green-800/80 dark:text-green-200/80">
                Copy the link or open its printable QR code. Treat either as a temporary account secret.
              </p>
            </div>
            <button
              type="button"
              onClick={() => {
                setCreatedLink("");
                setCreatedLinkExpiresAt("");
              }}
              className="shrink-0 rounded-md px-2 py-1 text-xs font-medium text-green-800 hover:bg-green-100 dark:text-green-200 dark:hover:bg-green-900/50"
            >
              Dismiss
            </button>
          </div>
          <code className="mt-3 block max-h-24 overflow-y-auto rounded-lg border border-green-200 bg-white/80 px-3 py-2 text-xs break-all text-green-950 dark:border-green-800 dark:bg-gray-950/30 dark:text-green-100">
              {createdLink}
          </code>
          {createdLinkExpiresAt && (
            <p className="mt-2 text-xs font-medium text-green-800 dark:text-green-200">
              Expires {fmtDateTime(createdLinkExpiresAt)} (your local time).
            </p>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => copyToClipboard(createdLink)}
            >
              <Copy size={14} /> Copy link
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                window.open(
                  activationQrPath(
                    activationTokenFromUrl(createdLink),
                    createdUserName,
                    createdUserId ?? undefined,
                  ),
                  "_blank",
                );
              }}
            >
              <QrCode size={14} /> Show QR code
            </Button>
            {createdUserId &&
              deliverySettings?.configured &&
              users.find((user) => user.id === createdUserId)?.has_valid_email && (
              <Button
                size="sm"
                onClick={() => setEmailConfirmUserId(createdUserId)}
              >
                <Send size={14} /> Email link and QR
              </Button>
              )}
          </div>
        </div>
      )}

      {recentlyUpdated && (
        <div className="flex flex-col gap-2 rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800 dark:border-green-800 dark:bg-green-900/20 dark:text-green-200 sm:flex-row sm:items-center sm:justify-between">
          <span><Check size={15} className="mr-1.5 inline" />{recentlyUpdated.message}</span>
          <div className="flex gap-3">
            {!recentlyUpdatedVisible && <button type="button" className="font-medium underline" onClick={() => { setFilterTag(""); setSearchQuery(""); window.setTimeout(() => document.getElementById(`user-row-${recentlyUpdated.userId}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 0); }}>Show person</button>}
            <button type="button" className="text-green-700/70 underline dark:text-green-300/70" onClick={() => setRecentlyUpdated(null)}>Dismiss</button>
          </div>
        </div>
      )}

      <ActivationCampaignCard
        summary={activationSummary}
        onPrimaryAction={handleActivationPrimaryAction}
      />

      {/* Toolbar: search, filter, mass tag */}
      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-3">
          {/* Search */}
          <div className="relative flex-1 min-w-[200px] max-w-sm">
            <Search
              size={15}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 dark:text-gray-500 pointer-events-none"
            />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search users..."
              className="w-full text-sm pl-9 pr-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          {/* Tag filter */}
          <select
            value={filterTag}
            onChange={(e) => setFilterTag(e.target.value)}
            className="text-sm px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          >
            <option value="">All users</option>
            <option value="__activated">Activated</option>
            <option value="__pending">Pending</option>
            <option value="__needs_link">Needs link</option>
            <option value="__has_link">Has link</option>
            <option value="__not_linked">Not linked</option>
            <option value="__editor">Can edit</option>
            <option value="__attention">Needs attention</option>
            <option value="__email_failed">Email delivery problems</option>
            <option value="__missing_email">Missing or invalid email</option>
            <option value="__deletion">
              Deletion requests{" "}
              {users.filter((u) => u.deletion_requested_at).length > 0 &&
                `(${users.filter((u) => u.deletion_requested_at).length})`}
            </option>
            {[...new Set(users.flatMap((u) => u.tags || []))]
              .sort()
              .map((tag) => (
                <option key={tag} value={tag}>
                  {tag}
                </option>
              ))}
          </select>
          <select
            value={sortBy}
            onChange={(event) => setSortBy(event.target.value as "name" | "status" | "recent")}
            aria-label="Sort users"
            className="text-sm px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          >
            <option value="name">Sort: name</option>
            <option value="status">Sort: attention first</option>
            <option value="recent">Sort: newest</option>
          </select>
          {(filterTag || searchQuery) && (
            <button
              onClick={() => {
                setFilterTag("");
                setSearchQuery("");
              }}
              className="text-sm text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 underline"
            >
              Clear
            </button>
          )}
        </div>

        {/* Mass tag bar */}
        {filteredUsers.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 mt-3 pt-3 border-t border-gray-200 dark:border-gray-700">
            <button
              onClick={toggleSelectAll}
              className={`inline-flex min-h-11 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium transition-colors sm:min-h-0 sm:py-1.5 ${
                selectedIds.size > 0 &&
                filteredUsers.every((u) => selectedIds.has(u.id))
                  ? "text-blue-500 bg-blue-50 dark:bg-blue-900/20"
                  : "text-gray-400 hover:text-blue-500 hover:bg-gray-100 dark:hover:bg-gray-700"
              }`}
              title={
                selectedIds.size > 0 &&
                filteredUsers.every((u) => selectedIds.has(u.id))
                  ? "Deselect all"
                  : "Select all"
              }
            >
              <CheckSquare size={15} />
              {selectedIds.size > 0 &&
              filteredUsers.every((user) => selectedIds.has(user.id))
                ? "Deselect visible"
                : "Select visible"}
            </button>
            {selectedIds.size > 0 && (
              <>
                <span className="text-xs font-medium text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20 px-2 py-1 rounded-lg">
                  {selectedIds.size} selected
                  {selectedIds.size - filteredUsers.filter((user) => selectedIds.has(user.id)).length > 0
                    ? `, ${selectedIds.size - filteredUsers.filter((user) => selectedIds.has(user.id)).length} hidden`
                    : ""}
                </span>
                <Button size="sm" onClick={openBatchWizard}>
                  <Send size={14} /> Distribute
                </Button>
                <button
                  type="button"
                  onClick={() => setSelectedIds(new Set())}
                  className="text-xs font-medium text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-100"
                >
                  Clear selection
                </button>
              </>
            )}
            {selectedIds.size > 0 && (
              <>
                <div className="h-5 w-px bg-gray-200 dark:bg-gray-700 mx-1" />
                <Tag size={14} className="text-gray-400 shrink-0" />
                <input
                  type="text"
                  value={massTagInput}
                  onChange={(e) => setMassTagInput(e.target.value)}
                  placeholder="Tag selected..."
                  className="text-sm px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 w-36 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && massTagInput.trim()) {
                      handleMassAddTag(massTargets, massTagInput.trim());
                    }
                  }}
                />
                <button
                  onClick={() => handleMassAddTag(massTargets, massTagInput.trim())}
                  disabled={!massTagInput.trim() || massTagBusy}
                  className="text-xs font-medium px-3 py-1.5 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40 transition-colors"
                >
                  {massTagBusy ? "Applying..." : "Add tag"}
                </button>
                <button
                  onClick={() =>
                    handleMassRemoveTag(massTargets, massTagInput.trim())
                  }
                  disabled={!massTagInput.trim() || massTagBusy}
                  className="text-xs font-medium px-3 py-1.5 rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/40 disabled:opacity-40 transition-colors"
                >
                  Remove tag
                </button>
              </>
            )}
          </div>
        )}
      </Card>

      {tagManagerOpen && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center sm:p-4">
          <Card className="max-h-[85dvh] w-full max-w-lg overflow-y-auto rounded-b-none p-5 sm:rounded-b-xl">
            <div className="flex items-start justify-between gap-3">
              <div><h3 className="font-semibold text-gray-900 dark:text-gray-100">Event tags</h3><p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Rename or remove a tag atomically for every person in one event.</p></div>
              <button type="button" onClick={() => setTagManagerOpen(false)} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700" aria-label="Close tag manager"><X size={16} /></button>
            </div>
            {!managedEventId ? (
              <p className="mt-5 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-900/20 dark:text-amber-200">Select one event in the page header before managing its tags.</p>
            ) : managedTags.length === 0 ? (
              <p className="py-8 text-center text-sm text-gray-500">This event has no tags yet.</p>
            ) : (
              <div className="mt-5 divide-y divide-gray-200 rounded-lg border border-gray-200 dark:divide-gray-700 dark:border-gray-700">
                {managedTags.map(({ tag, count }) => (
                  <div key={tag} className="p-3">
                    <div className="flex items-center justify-between gap-3">
                      <div><span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ${tagColour(tag)}`}>{tag}</span><span className="ml-2 text-xs text-gray-500">{count} {count === 1 ? "person" : "people"}</span></div>
                      <div className="flex gap-1"><button type="button" onClick={() => { setEditingTag(tag); setReplacementTag(tag); setDeletingTag(null); }} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 hover:text-blue-600 dark:hover:bg-gray-700" title="Rename tag"><Pencil size={14} /></button><button type="button" onClick={() => { setDeletingTag(tag); setEditingTag(null); }} className="rounded-lg p-2 text-gray-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-900/20" title="Delete tag"><Trash2 size={14} /></button></div>
                    </div>
                    {editingTag === tag && <div className="mt-3 flex gap-2"><input value={replacementTag} onChange={(event) => setReplacementTag(event.target.value)} className="min-w-0 flex-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100" /><Button size="sm" onClick={() => handleEventTagAction("rename", tag)} disabled={massTagBusy || !replacementTag.trim() || replacementTag.trim() === tag}>Rename</Button><Button variant="outline" size="sm" onClick={() => setEditingTag(null)}>Cancel</Button></div>}
                    {deletingTag === tag && <div className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-800 dark:bg-red-900/20 dark:text-red-200"><p>Remove <strong>{tag}</strong> from {count} {count === 1 ? "person" : "people"}? Accounts are not deleted.</p><div className="mt-3 flex gap-2"><Button variant="danger" size="sm" onClick={() => handleEventTagAction("delete", tag)} disabled={massTagBusy}>Remove tag</Button><Button variant="outline" size="sm" onClick={() => setDeletingTag(null)}>Cancel</Button></div></div>}
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      )}

      {/* Batch link wizard modal */}
      {batchWizardOpen && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center sm:p-4">
          <Card className="max-h-[92dvh] w-full max-w-xl overflow-y-auto rounded-b-none p-5 sm:rounded-b-xl">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
                <Link2 size={16} />
                Distribute activation
              </h3>
              <button
                onClick={() => setBatchWizardOpen(false)}
                className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 dark:hover:text-gray-300 dark:hover:bg-gray-700 transition-colors"
              >
                <X size={16} />
              </button>
            </div>

            {/* Step indicators */}
            <div className="flex items-center gap-2 mb-5 text-xs font-medium">
              {(
                [
                  { key: "preview", label: "1. Preview" },
                  { key: "format", label: "2. Method" },
                  { key: "result", label: "3. Result" },
                ] as const
              ).map((s, i) => (
                <div key={s.key} className="flex items-center gap-2">
                  {i > 0 && (
                    <ChevronRight
                      size={12}
                      className="text-gray-300 dark:text-gray-600"
                    />
                  )}
                  <span
                    className={
                      batchStep === s.key
                        ? "text-blue-600 dark:text-blue-400"
                        : batchLinks.length > 0 && s.key === "result"
                          ? "text-gray-500 dark:text-gray-400"
                          : "text-gray-400 dark:text-gray-500"
                    }
                  >
                    {s.label}
                  </span>
                </div>
              ))}
            </div>

            {batchActionError && (
              <p className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-800 dark:bg-red-900/20 dark:text-red-200">
                {batchActionError}
              </p>
            )}

            {/* Step 1: Preview */}
            {batchStep === "preview" && (
              <div>
                <p className="text-sm text-gray-600 dark:text-gray-400 mb-3">
                  Activation links will be generated for{" "}
                  <strong className="text-gray-900 dark:text-gray-100">
                    {batchPendingCount} pending
                  </strong>{" "}
                  user{batchPendingCount !== 1 ? "s" : ""}
                  from your selection. Activated and deactivated users are
                  reported without generating links.
                </p>
                <div className="max-h-48 overflow-y-auto border border-gray-200 dark:border-gray-700 rounded-lg">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 dark:bg-gray-800 sticky top-0">
                      <tr>
                        <th className="text-left px-3 py-2 text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                          Name
                        </th>
                        <th className="text-left px-3 py-2 text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                          Status
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                      {batchWizardTargets.map((u) => (
                        <tr
                          key={u.id}
                          className={u.is_activated ? "opacity-40" : ""}
                        >
                          <td className="px-3 py-2 text-gray-900 dark:text-gray-100">
                            {u.display_name}
                            <span className="text-gray-400 dark:text-gray-500 ml-1.5">
                              @{u.username}
                            </span>
                          </td>
                          <td className="px-3 py-2">
                            {!u.is_active ? (
                              <span className="text-xs text-gray-400">
                                Deactivated
                              </span>
                            ) : u.is_activated ? (
                              <span className="text-xs text-gray-400">
                                Already activated
                              </span>
                            ) : !u.has_valid_email ? (
                              <span className="text-xs text-gray-500 dark:text-gray-400">
                                Pending, no valid email
                              </span>
                            ) : (
                              <span className="text-xs font-medium text-amber-600 dark:text-amber-400">
                                Pending
                              </span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="flex justify-end mt-4">
                  <Button
                    size="sm"
                    onClick={() => setBatchStep("format")}
                    disabled={batchPendingCount === 0}
                  >
                    Next <ChevronRight size={14} />
                  </Button>
                </div>
              </div>
            )}

            {/* Step 2: Format */}
            {batchStep === "format" && (
              <div>
                <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
                  Choose how to distribute fresh activation links to the selected people.
                </p>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3 mb-4">
                  <button
                    onClick={() => setBatchFormat("email")}
                    disabled={
                      !deliverySettings?.configured ||
                      batchWizardTargets.length > 50
                    }
                    className={`p-4 rounded-lg border-2 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                      batchFormat === "email"
                        ? "border-blue-500 bg-blue-50 dark:bg-blue-900/20"
                        : "border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600"
                    }`}
                  >
                    <Send size={20} className={batchFormat === "email" ? "mb-2 text-blue-500" : "mb-2 text-gray-400"} />
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      Email link and QR
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                      Send immediately to each valid email address.
                    </p>
                  </button>
                  <button
                    onClick={() => setBatchFormat("list")}
                    className={`p-4 rounded-lg border-2 text-left transition-colors ${
                      batchFormat === "list"
                        ? "border-blue-500 bg-blue-50 dark:bg-blue-900/20"
                        : "border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600"
                    }`}
                  >
                    <Copy
                      size={20}
                      className={
                        batchFormat === "list"
                          ? "text-blue-500 mb-2"
                          : "text-gray-400 mb-2"
                      }
                    />
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      Copy as list
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                      Generate links and copy them to your clipboard as a
                      tab-separated list.
                    </p>
                  </button>
                  <button
                    onClick={() => setBatchFormat("qr")}
                    className={`p-4 rounded-lg border-2 text-left transition-colors ${
                      batchFormat === "qr"
                        ? "border-blue-500 bg-blue-50 dark:bg-blue-900/20"
                        : "border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600"
                    }`}
                  >
                    <QrCode
                      size={20}
                      className={
                        batchFormat === "qr"
                          ? "text-blue-500 mb-2"
                          : "text-gray-400 mb-2"
                      }
                    />
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      Download QR badges
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                      Generate a ZIP file with a branded QR code badge for each
                      user.
                    </p>
                  </button>
                </div>
                {!deliverySettings?.configured && (
                  <p className="mb-3 text-xs text-gray-500 dark:text-gray-400">
                    Email delivery is unavailable until a root administrator configures SMTP.
                  </p>
                )}
                {batchWizardTargets.length > 50 && (
                  <p className="mb-3 text-xs text-amber-700 dark:text-amber-300">
                    Email batches support up to 50 selected people. Reduce the selection to enable email.
                  </p>
                )}
                <div className="flex justify-between mt-4">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setBatchStep("preview")}
                  >
                    Back
                  </Button>
                  <Button
                    size="sm"
                    onClick={() =>
                      batchFormat === "email"
                        ? handleBatchActivationEmail()
                        : handleBatchActivation()
                    }
                    disabled={batchLoading}
                  >
                    {batchLoading
                      ? batchFormat === "email"
                        ? "Sending..."
                        : "Generating..."
                      : batchFormat === "email"
                        ? "Send emails"
                        : "Generate links"}{" "}
                    <ChevronRight size={14} />
                  </Button>
                </div>
              </div>
            )}

            {/* Step 3: Result */}
            {batchStep === "result" &&
              batchFormat === "email" &&
              batchEmailResults.length > 0 && (
                <div>
                  <div className="mb-4 grid grid-cols-2 gap-2 text-sm sm:grid-cols-5">
                    {(["accepted", "failed", "unknown", "not_attempted", "skipped"] as const).map((status) => (
                      <div key={status} className="rounded-lg bg-gray-50 px-3 py-2 dark:bg-gray-800">
                        <p className="text-xs capitalize text-gray-500 dark:text-gray-400">{status.replace("_", " ")}</p>
                        <p className="font-semibold text-gray-900 dark:text-gray-100">
                          {batchEmailResults.filter((result) => result.status === status).length}
                        </p>
                      </div>
                    ))}
                  </div>
                  <div className="max-h-64 overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-700">
                    {batchEmailResults.map((result) => (
                      <div key={result.user_id} className="border-b border-gray-100 px-3 py-3 last:border-0 dark:border-gray-700">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">{result.display_name}</p>
                            <p className={`mt-0.5 text-xs ${result.status === "accepted" ? "text-green-700 dark:text-green-300" : "text-red-700 dark:text-red-300"}`}>
                              {result.message}
                            </p>
                          </div>
                          {result.status !== "accepted" && result.status !== "skipped" && (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => handleBatchActivationEmail([result.user_id])}
                              disabled={batchLoading}
                            >
                              <RefreshCw size={13} /> Retry
                            </Button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    {batchEmailResults.some((result) => ["failed", "unknown", "not_attempted"].includes(result.status)) && (
                      <Button
                        size="sm"
                        onClick={() =>
                          handleBatchActivationEmail(
                            batchEmailResults
                              .filter((result) => ["failed", "unknown", "not_attempted"].includes(result.status))
                              .map((result) => result.user_id),
                          )
                        }
                        disabled={batchLoading}
                      >
                        <RefreshCw size={14} /> Retry unsuccessful
                      </Button>
                    )}
                    <Button variant="outline" size="sm" onClick={() => setBatchWizardOpen(false)}>
                      Done
                    </Button>
                  </div>
                </div>
              )}

            {batchStep === "result" &&
              batchFormat !== "email" &&
              (batchLinks.length > 0 || batchLinkSkipped.length > 0) && (
              <div>
                <div className="flex items-center gap-2 mb-4">
                  <span className="inline-flex items-center gap-1.5 text-sm font-medium text-green-700 dark:text-green-300 bg-green-50 dark:bg-green-900/20 px-3 py-1 rounded-lg">
                    {batchLinks.length} link
                    {batchLinks.length !== 1 ? "s" : ""} generated
                  </span>
                </div>

                {batchLinks.length === 0 && (
                  <p className="mb-4 text-sm text-gray-600 dark:text-gray-300">
                    No links were created for the selected users. Review the
                    reasons below and update the accounts before trying again.
                  </p>
                )}

                {batchLinks.length > 0 && (
                  <div className="max-h-48 overflow-y-auto border border-gray-200 dark:border-gray-700 rounded-lg mb-4">
                    {batchLinks.map((l) => (
                    <div
                      key={l.user_id}
                      className="flex items-center justify-between px-3 py-2 text-sm border-b border-gray-100 dark:border-gray-700 last:border-b-0"
                    >
                      <span className="font-medium text-gray-900 dark:text-gray-100 truncate mr-3">
                        {l.display_name}
                        <span className="block text-xs font-normal text-gray-500 dark:text-gray-400">
                          Expires {fmtDateTime(l.expires_at)} (your local time)
                        </span>
                      </span>
                      <div className="flex items-center gap-1 shrink-0">
                        <button
                          onClick={() =>
                            copyToClipboard(
                              window.location.origin + l.activation_url,
                            )
                          }
                          className="p-1.5 rounded-lg text-gray-400 hover:text-blue-500 hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors"
                          title="Copy link"
                        >
                          <Copy size={14} />
                        </button>
                        <button
                          onClick={() => {
                            window.open(
                              activationQrPath(
                                activationTokenFromUrl(l.activation_url),
                                l.display_name,
                                l.user_id,
                              ),
                              "_blank",
                            );
                          }}
                          className="p-1.5 rounded-lg text-gray-400 hover:text-blue-500 hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors"
                          title="Show QR page"
                        >
                          <QrCode size={14} />
                        </button>
                      </div>
                    </div>
                    ))}
                  </div>
                )}

                {batchLinkSkipped.length > 0 && (
                  <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-900/20">
                    {batchLinkSkipped.map((item) => (
                      <div
                        key={item.user_id}
                        className="border-b border-amber-100 px-3 py-2 text-sm last:border-0 dark:border-amber-800"
                      >
                        <span className="font-medium text-amber-900 dark:text-amber-100">
                          {item.display_name}
                        </span>
                        <span className="ml-2 text-amber-800 dark:text-amber-200">
                          {item.message}
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Action buttons */}
                <div className="flex flex-wrap items-center gap-2">
                  {batchLinks.length > 0 && batchFormat === "list" ? (
                    <Button size="sm" onClick={handleCopyAllLinks}>
                      <Copy size={14} /> Copy All Links
                    </Button>
                  ) : batchLinks.length > 0 ? (
                    <Button
                      size="sm"
                      onClick={handleDownloadQrZip}
                      disabled={batchQrBusy}
                    >
                      <Download size={14} />{" "}
                      {batchQrBusy
                        ? "Creating ZIP..."
                        : "Download QR Badges (.zip)"}
                    </Button>
                  ) : null}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setBatchWizardOpen(false)}
                  >
                    Done
                  </Button>
                </div>
              </div>
            )}
          </Card>
        </div>
      )}

      {/* Bulk user creation modal */}
      {bulkCreateOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <Card className="w-full max-w-5xl mx-4 p-5 max-h-[88vh] overflow-y-auto">
            <div className="flex items-center justify-between gap-3 mb-4">
              <div>
                <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
                  <Users size={16} />
                  Bulk Create Users
                </h3>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  Create ordinary event users first, then generate activation
                  links from the selected results.
                </p>
              </div>
              <button
                onClick={() => setBulkCreateOpen(false)}
                className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 dark:hover:text-gray-300 dark:hover:bg-gray-700 transition-colors"
                aria-label="Close bulk user creation"
              >
                <X size={16} />
              </button>
            </div>

            <div className="flex flex-wrap items-end gap-3 mb-4">
              {!isIssuerOnly && (
                <div className="w-56">
                  <label
                    htmlFor="bulk-create-event"
                    className="block text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-1.5"
                  >
                    Event
                  </label>
                  <select
                    id="bulk-create-event"
                    value={bulkCreateEventId}
                    onChange={(e) => setBulkCreateEventId(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 text-sm"
                  >
                    <option value="">Select event...</option>
                    {events.map((ev) => (
                      <option key={ev.id} value={ev.id}>
                        {ev.name}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <div className="flex-1 min-w-[220px]">
                <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-1.5">
                  Bulk tags
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={bulkTagText}
                    onChange={(e) => setBulkTagText(e.target.value)}
                    placeholder="e.g. board, late arrival"
                    className="w-full text-sm px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={applyBulkTagsToRows}
                    disabled={parseTagList(bulkTagText).length === 0}
                  >
                    <Tag size={14} /> Apply
                  </Button>
                </div>
              </div>
            </div>

            {bulkCreateResult && (
              <div
                className={`mb-4 rounded-lg border px-3 py-2 text-sm ${
                  bulkCreateResult.errors > 0
                    ? "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200"
                    : "border-green-200 bg-green-50 text-green-800 dark:border-green-800 dark:bg-green-900/20 dark:text-green-200"
                }`}
              >
                Created {bulkCreateResult.created} user
                {bulkCreateResult.created !== 1 ? "s" : ""}
                {bulkCreateResult.errors > 0
                  ? `; ${bulkCreateResult.errors} row${bulkCreateResult.errors !== 1 ? "s" : ""} need attention.`
                  : "."}
              </div>
            )}

            <div className="overflow-x-auto border border-gray-200 dark:border-gray-700 rounded-lg">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 dark:bg-gray-800">
                  <tr>
                    <th className="text-left px-3 py-2 text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Display name
                    </th>
                    <th className="text-left px-3 py-2 text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Username
                    </th>
                    <th className="text-left px-3 py-2 text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Email
                    </th>
                    <th className="text-left px-3 py-2 text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Tags
                    </th>
                    <th className="text-center px-3 py-2 text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Edit
                    </th>
                    <th className="w-10" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                  {bulkCreateRows.map((row) => (
                    <tr
                      key={row.id}
                      className={
                        row.error
                          ? "bg-amber-50/60 dark:bg-amber-900/10"
                          : ""
                      }
                    >
                      <td className="px-3 py-2 align-top">
                        <input
                          value={row.display_name}
                          onChange={(e) =>
                            updateBulkRow(row.id, "display_name", e.target.value)
                          }
                          placeholder="Alpha Tester"
                          className="w-48 text-sm px-2.5 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        />
                        {row.error && (
                          <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
                            {row.error}
                          </p>
                        )}
                      </td>
                      <td className="px-3 py-2 align-top">
                        <input
                          value={row.username}
                          onChange={(e) =>
                            updateBulkRow(row.id, "username", e.target.value)
                          }
                          placeholder="alpha.tester"
                          className="w-44 text-sm px-2.5 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        />
                      </td>
                      <td className="px-3 py-2 align-top">
                        <input
                          type="email"
                          value={row.email}
                          onChange={(e) =>
                            updateBulkRow(row.id, "email", e.target.value)
                          }
                          placeholder="Optional"
                          className="w-52 text-sm px-2.5 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        />
                      </td>
                      <td className="px-3 py-2 align-top">
                        <input
                          value={row.tags.join(", ")}
                          onChange={(e) =>
                            updateBulkRow(
                              row.id,
                              "tags",
                              parseTagList(e.target.value),
                            )
                          }
                          placeholder="Comma separated"
                          className="w-48 text-sm px-2.5 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        />
                      </td>
                      <td className="px-3 py-2 text-center align-top">
                        <input
                          type="checkbox"
                          checked={row.can_edit}
                          onChange={(e) =>
                            updateBulkRow(row.id, "can_edit", e.target.checked)
                          }
                          className="mt-2 rounded"
                          aria-label={`Can edit ${row.display_name || row.username || "bulk user"}`}
                        />
                      </td>
                      <td className="px-3 py-2 align-top">
                        <button
                          onClick={() => removeBulkRow(row.id)}
                          className="mt-0.5 p-1.5 rounded-lg text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                          aria-label={`Remove ${row.display_name || row.username || "row"}`}
                        >
                          <Trash2 size={15} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 mt-4">
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  setBulkCreateRows((rows) => [...rows, createBulkUserDraft()])
                }
              >
                <Plus size={14} /> Add Row
              </Button>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setBulkCreateOpen(false)}
                >
                  Cancel
                </Button>
                <Button
                  size="sm"
                  onClick={handleBulkCreateUsers}
                  disabled={
                    bulkCreateSaving || (!isIssuerOnly && !bulkCreateEventId)
                  }
                >
                  {bulkCreateSaving ? "Creating..." : "Create Users"}
                </Button>
              </div>
            </div>
          </Card>
        </div>
      )}

      {/* User list */}
      {filteredUsers.length === 0 ? (
        <p className="text-gray-500 dark:text-gray-400 text-center py-8">
          No users{filterTag || searchQuery ? " matching filters" : " yet"}.
        </p>
      ) : (
        <div className="space-y-1" role="table" aria-label="User accounts">
          <div role="row" className="hidden grid-cols-[2rem_minmax(0,1fr)_minmax(9rem,0.45fr)_auto] items-center gap-3 px-4 py-1.5 text-xs font-medium uppercase tracking-wide text-gray-400 sm:grid">
            <span aria-hidden="true" />
            <span role="columnheader">Person and tags</span>
            <span role="columnheader">Access</span>
            <span role="columnheader" className="text-right">Actions</span>
          </div>
          {filteredUsers.map((u) => (
            <Card
              key={u.id}
              id={`user-row-${u.id}`}
              role="row"
              className={`p-3 sm:px-4 sm:py-2.5 ${selectedIds.has(u.id) ? "ring-2 ring-blue-400 dark:ring-blue-500" : ""} ${recentlyUpdated?.userId === u.id ? "border-green-300 bg-green-50/40 dark:border-green-800 dark:bg-green-900/10" : ""}`}
            >
              {/* Top row: checkbox + name/badges + actions */}
              <div className="grid grid-cols-[auto_minmax(0,1fr)] items-start gap-3 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-center">
                <input
                  type="checkbox"
                  checked={selectedIds.has(u.id)}
                  onChange={() => toggleSelect(u.id)}
                  className="mt-1.5 h-4 w-4 rounded"
                  aria-label={`Select ${u.display_name}`}
                />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="font-semibold text-gray-900 dark:text-gray-100">
                      {u.display_name}
                    </h3>
                    {!u.is_active && (
                      <span className="text-xs font-medium bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300 px-2 py-0.5 rounded-full">
                        deactivated
                      </span>
                    )}
                    {u.is_admin && (
                      <span className="text-xs font-medium bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300 px-2 py-0.5 rounded-full">
                        admin
                      </span>
                    )}
                    {u.is_issuer && !u.is_admin && (
                      <span className="text-xs font-medium bg-purple-100 dark:bg-purple-900/40 text-purple-700 dark:text-purple-300 px-2 py-0.5 rounded-full">
                        issuer
                      </span>
                    )}
                    {u.can_edit && (
                      <span className="text-xs font-medium bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-300 px-2 py-0.5 rounded-full">
                        editor
                      </span>
                    )}
                    {!u.is_activated && (
                      <span className="text-xs font-medium bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 px-2 py-0.5 rounded-full">
                        pending
                      </span>
                    )}
                    {u.deletion_requested_at && (
                      <span
                        className="text-xs font-medium bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300 px-2 py-0.5 rounded-full"
                        title={`Requested ${fmtDateTime(u.deletion_requested_at)}`}
                      >
                        deletion requested
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                    @{u.username}
                    {expandedDetailsUser !== u.id && u.email && ` · ${u.email}`}
                  </p>
                  {expandedDetailsUser !== u.id && (u.tags || []).length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {(u.tags || []).map((tag) => (
                        <button key={tag} type="button" onClick={() => setFilterTag(tag)} className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${tagColour(tag)}`} title={`Show people tagged ${tag}`}>{tag}</button>
                      ))}
                    </div>
                  )}
                  {(emailResults[u.id]?.status || u.activation_email_status) && (
                    <p
                      className={`mt-1 text-xs ${
                        (emailResults[u.id]?.status || u.activation_email_status) === "accepted"
                          ? "text-green-700 dark:text-green-300"
                          : (emailResults[u.id]?.status || u.activation_email_status) === "sending"
                            ? "text-amber-700 dark:text-amber-300"
                            : "text-red-700 dark:text-red-300"
                      }`}
                    >
                      {emailResults[u.id]?.message ||
                        u.activation_email_error_message ||
                        ((emailResults[u.id]?.status || u.activation_email_status) === "accepted"
                          ? `Accepted by mail server${u.activation_email_accepted_at ? ` ${fmtDateTime(u.activation_email_accepted_at)}` : ""}`
                          : (emailResults[u.id]?.status || u.activation_email_status) === "sending"
                            ? "Email hand-off in progress."
                            : "Email delivery needs attention.")}
                    </p>
                  )}
                </div>
                <div className="col-span-2 flex w-full flex-wrap items-center justify-end gap-1 sm:col-span-1 sm:w-auto sm:shrink-0">
                  {u.is_activated ? (
                    <Button
                      variant={
                        ["failed", "unknown", "not_attempted"].includes(
                          emailResults[u.id]?.status || u.activation_email_status || "",
                        )
                          ? "outline"
                          : "primary"
                      }
                      size="sm"
                      onClick={() => {
                        setEmailActionErrors((current) => {
                          const next = { ...current };
                          delete next[u.id];
                          return next;
                        });
                        const previousPurpose =
                          emailResults[u.id]?.purpose ||
                          u.activation_email_purpose;
                        setManagedPasskeyPurpose(
                          previousPurpose === "additional_passkey" ||
                            previousPurpose === "credential_reset"
                            ? previousPurpose
                            : null,
                        );
                        setEmailConfirmUserId(u.id);
                      }}
                      disabled={emailBusy.has(u.id) || !u.is_active}
                      title={
                        u.is_active
                          ? "Add or reset passkeys"
                          : "This account is deactivated"
                      }
                    >
                      <Key size={14} /> Passkeys
                    </Button>
                  ) : (
                    <>
                      <Button
                        variant={
                          ["failed", "unknown", "not_attempted"].includes(
                            emailResults[u.id]?.status || u.activation_email_status || "",
                          )
                            ? "outline"
                            : "primary"
                        }
                        size="sm"
                        onClick={() => {
                          setManagedPasskeyPurpose(null);
                          setEmailConfirmUserId(u.id);
                        }}
                        disabled={
                          emailBusy.has(u.id) ||
                          !u.is_active ||
                          !u.has_valid_email ||
                          !deliverySettings?.configured
                        }
                        title={
                          !deliverySettings?.configured
                            ? "Email delivery is not configured"
                            : !u.has_valid_email
                              ? "Add a valid email address first"
                              : !u.is_active
                                ? "This account is deactivated"
                                : undefined
                        }
                      >
                        <Send size={14} />
                        {emailBusy.has(u.id) ? "Sending..." : "Send email"}
                      </Button>
                      <button
                        onClick={() => handleToggleActivationLink(u.id)}
                        disabled={!u.is_active}
                        className={`p-2 rounded-lg transition-colors ${
                          !u.is_active
                            ? "text-gray-200 dark:text-gray-700 cursor-not-allowed"
                            : activationLinks[u.id]
                              ? "text-blue-500 bg-blue-50 dark:bg-blue-900/20"
                              : "text-gray-400 hover:text-blue-500 hover:bg-gray-100 dark:hover:bg-gray-700"
                        }`}
                        title={
                          !u.is_active
                            ? "User is deactivated"
                            : activationLinks[u.id]
                              ? "Hide activation link"
                              : "Generate activation link"
                        }
                      >
                        <Link2 size={16} />
                      </button>
                    </>
                  )}
                  <button
                    onClick={() => handleShowLinkInfo(u.id)}
                    className={`p-2 rounded-lg transition-colors ${
                      expandedLinkUser === u.id
                        ? "text-blue-500 bg-blue-50 dark:bg-blue-900/20"
                        : "text-gray-400 hover:text-blue-500 hover:bg-gray-100 dark:hover:bg-gray-700"
                    }`}
                    title="Activation link history"
                  >
                    <Key size={16} />
                  </button>
                  <button
                    onClick={() =>
                      setExpandedDetailsUser((current) =>
                        current === u.id ? null : u.id,
                      )
                    }
                    className={`p-2 rounded-lg transition-colors ${
                      expandedDetailsUser === u.id
                        ? "text-blue-500 bg-blue-50 dark:bg-blue-900/20"
                        : "text-gray-400 hover:text-blue-500 hover:bg-gray-100 dark:hover:bg-gray-700"
                    }`}
                    title="Account details"
                    aria-expanded={expandedDetailsUser === u.id}
                  >
                    <MoreHorizontal size={16} />
                  </button>
                </div>
              </div>

              {expandedDetailsUser === u.id && (
                <div className="mt-3">
                  <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                    Account settings
                  </h4>
                  <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                    Edit contact, assignment, permissions, and tags in one place.
                  </p>
                </div>
              )}

              {/* Settings row: person link + can-edit */}
              {expandedDetailsUser === u.id && u.is_active && (
                <div className="mt-3">
                  <label className="text-xs font-medium text-gray-600 dark:text-gray-300">
                    Email address
                  </label>
                  <div className="mt-1.5 flex flex-col gap-2 sm:flex-row">
                    <input
                      type="email"
                      value={emailDrafts[u.id] ?? u.email ?? ""}
                      onChange={(event) =>
                        setEmailDrafts((current) => ({
                          ...current,
                          [u.id]: event.target.value,
                        }))
                      }
                      placeholder="name@example.com"
                      className="min-h-11 flex-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 sm:min-h-0"
                    />
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleUpdateUserEmail(u)}
                      disabled={
                        emailDrafts[u.id] === undefined ||
                        emailDrafts[u.id].trim() === (u.email ?? "")
                      }
                    >
                      Save email
                    </Button>
                  </div>
                </div>
              )}
              {expandedDetailsUser === u.id && u.is_active && isRootAdmin && !u.is_root_admin && (
                <div className="mt-3">
                  <label className="text-xs font-medium text-gray-600 dark:text-gray-300">
                    Event assignment
                  </label>
                  <select
                    value={u.event_id ?? ""}
                    onChange={(event) => void handleUpdateUserEvent(
                      u.id,
                      event.target.value ? Number(event.target.value) : null,
                    )}
                    className="mt-1.5 min-h-11 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 sm:min-h-0"
                  >
                    <option value="">No event yet (unassigned)</option>
                    {events.map((event) => (
                      <option key={event.id} value={event.id}>{event.name}</option>
                    ))}
                  </select>
                  <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    Changing the event requires your passkey again and revokes this user&apos;s active sessions.
                  </p>
                </div>
              )}
              {expandedDetailsUser === u.id && u.event_id && u.is_active && (
                <div className="mt-3 flex flex-wrap items-end gap-x-6 gap-y-3">
                  <div>
                    <p className="mb-1.5 text-xs font-medium text-gray-600 dark:text-gray-300">Schedule person</p>
                    <div className="flex items-center gap-2">
                    <Users size={14} className="text-gray-400 shrink-0" />
                    <select
                      value={u.linked_person_id ?? ""}
                      onChange={(e) =>
                        handleLinkPerson(
                          u.id,
                          e.target.value ? Number(e.target.value) : null,
                        )
                      }
                      className="text-sm px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    >
                      <option value="">No linked person</option>
                      {(persons[u.event_id] || []).map((p) => (
                        <option
                          key={p.external_person_id}
                          value={p.external_person_id}
                        >
                          {p.first_name} {p.last_name}
                        </option>
                      ))}
                    </select>
                    </div>
                  </div>
                  <label className="flex items-center gap-2 pb-1.5 text-sm text-gray-600 dark:text-gray-400 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={u.can_edit}
                      onChange={(e) =>
                        handleToggleCanEdit(u.id, e.target.checked)
                      }
                      className="rounded"
                    />
                    Can edit schedules
                  </label>
                  {isRootAdmin && !u.is_root_admin && (
                    <label className="flex items-center gap-2 pb-1.5 text-sm text-gray-600 dark:text-gray-400 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={u.is_issuer}
                        onChange={(e) =>
                          handleToggleIssuer(u.id, e.target.checked)
                        }
                        className="rounded"
                      />
                      Issuer access
                    </label>
                  )}
                </div>
              )}

              {/* Tags */}
              {expandedDetailsUser === u.id && u.is_active && (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <span className="mr-1 text-xs font-medium text-gray-600 dark:text-gray-300">Tags</span>
                  {(u.tags || []).map((tag) => (
                    <span
                      key={tag}
                      className={`inline-flex items-center gap-1 text-xs font-medium px-2.5 py-1 rounded-full ${tagColour(tag)}`}
                    >
                      {tag}
                      <button
                        onClick={() => handleRemoveTag(u.id, tag)}
                        className="opacity-60 hover:opacity-100 transition-opacity"
                      >
                        ×
                      </button>
                    </span>
                  ))}
                  <input
                    type="text"
                    value={tagInput[u.id] || ""}
                    onChange={(e) =>
                      setTagInput((prev) => ({
                        ...prev,
                        [u.id]: e.target.value,
                      }))
                    }
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && tagInput[u.id]?.trim()) {
                        handleAddTag(u.id, tagInput[u.id]);
                        setTagInput((prev) => ({ ...prev, [u.id]: "" }));
                      }
                    }}
                    placeholder="Add tag..."
                    className="text-xs px-3 py-1 border border-dashed border-gray-300 dark:border-gray-600 rounded-full bg-transparent text-gray-900 dark:text-gray-100 w-24 focus:w-36 focus:border-solid focus:border-blue-400 transition-all focus:outline-none"
                  />
                </div>
              )}
              {expandedDetailsUser === u.id && !u.is_active && (u.tags || []).length > 0 && (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <span className="mr-1 text-xs font-medium text-gray-600 dark:text-gray-300">Tags</span>
                  {u.tags.map((tag) => (
                    <span
                      key={tag}
                      className={`inline-flex items-center text-xs font-medium px-2.5 py-1 rounded-full opacity-50 ${tagColour(tag)}`}
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              )}

              {expandedDetailsUser === u.id && (
                <div className="mt-4 flex flex-wrap gap-2 border-t border-gray-100 pt-3 dark:border-gray-700">
                  {!isIssuerOnly && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleGdprExport(u.id, u.display_name)}
                      disabled={gdprBusy[u.id]}
                    >
                      <Download size={14} /> Export user data
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setRemovalError(null);
                      setConfirmDeleteId(u.id);
                    }}
                    className="text-red-600 hover:text-red-700 dark:text-red-400"
                  >
                    <Trash2 size={14} /> Remove or delete account
                  </Button>
                </div>
              )}

              {/* Expandable panels */}
              {confirmDeleteId === u.id && (
                <div className="mt-3 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-lg p-4">
                  <p className="text-sm text-red-800 dark:text-red-200 mb-3">
                    Remove <strong>{u.display_name}</strong>? Only an account
                    that was never activated, linked, or used can be removed
                    immediately. Accounts with history must use the signed
                    deletion-evidence workflow.
                  </p>
                  {removalError && (
                    <p className="mb-3 text-sm text-red-700 dark:text-red-300">
                      {removalError}
                    </p>
                  )}
                  <div className="flex gap-2">
                    <Button
                      variant="primary"
                      size="sm"
                      onClick={() => handleDeleteUser(u.id)}
                      className="!bg-red-600 hover:!bg-red-700"
                    >
                      Remove unused account
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setConfirmDeleteId(null)}
                    >
                      Cancel
                    </Button>
                  </div>
                </div>
              )}

              {/* GDPR deletion request banner + anonymise action */}
              {!isIssuerOnly && u.deletion_requested_at && (
                <div className="mt-3 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-lg p-4">
                  <div className="flex items-start gap-3">
                    <AlertTriangle
                      size={18}
                      className="text-red-600 dark:text-red-400 shrink-0 mt-0.5"
                    />
                    <div className="flex-1 space-y-2">
                      <p className="text-sm font-medium text-red-800 dark:text-red-200">
                        Deletion requested on{" "}
                        {fmtDateTime(u.deletion_requested_at)}
                      </p>
                      <p className="text-xs text-red-700 dark:text-red-300">
                        This user has requested their personal data be erased
                        (GDPR Art.&nbsp;17). Export their data first if needed,
                        then start the signed workflow. Live deletion is
                        followed by desktop, HA and backup accountability steps.
                      </p>
                      <button
                        type="button"
                        onClick={() => setGdprInfoOpen((o) => !o)}
                        className="flex items-center gap-1 text-xs text-red-600 dark:text-red-300 hover:text-red-800 dark:hover:text-red-100 transition-colors"
                      >
                        <Info size={13} />
                        {gdprInfoOpen
                          ? "Hide details"
                          : "What happens when I anonymise?"}
                      </button>
                      {gdprInfoOpen && (
                        <div className="text-xs text-red-700 dark:text-red-300 bg-red-100/60 dark:bg-red-900/40 border border-red-200 dark:border-red-800 rounded-lg px-3 py-2 space-y-1">
                          <p className="font-medium">
                            Anonymisation performs these steps in order:
                          </p>
                          <ol className="list-decimal pl-4 space-y-0.5">
                            <li>
                              All active sessions are revoked - the user is
                              logged out everywhere immediately.
                            </li>
                            <li>
                              Passkeys, challenges, exchange codes, and
                              activation links are permanently deleted.
                            </li>
                            <li>Push subscriptions are deleted.</li>
                            <li>
                              Task edits are preserved but the user link is
                              removed (edited_by set to null).
                            </li>
                            <li>
                              The link to any published person is removed.
                            </li>
                            <li>
                              The user record is anonymised: username becomes
                              &quot;deleted_N&quot;, display name becomes
                              &quot;Deleted User&quot;, email and tags are
                              cleared. The account is deactivated.
                            </li>
                          </ol>
                          <p>
                            The live user row is anonymised so structural
                            references remain valid. A separate non-identifying
                            receipt records verified steps and explicitly names
                            external or backup locations that could only be
                            addressed on a best-effort basis.
                          </p>
                        </div>
                      )}
                      {gdprConfirmId === u.id ? (
                        <div className="pt-1 space-y-2">
                          <p className="text-xs text-red-800 dark:text-red-200 font-medium">
                            This will permanently anonymise{" "}
                            <strong>{u.display_name}</strong>&apos;s personal
                            data: name, email, credentials, sessions, and push
                            subscriptions. This cannot be undone.
                          </p>
                          <div className="flex gap-2">
                            <Button
                              variant="primary"
                              size="sm"
                              onClick={() => handleGdprAnonymise(u.id)}
                              className="!bg-red-600 hover:!bg-red-700"
                            >
                              Anonymise &amp; Deactivate
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() =>
                                handleGdprExport(u.id, u.display_name)
                              }
                              disabled={gdprBusy[u.id]}
                            >
                              <Download size={14} /> Export First
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => setGdprConfirmId(null)}
                            >
                              Cancel
                            </Button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex gap-2 pt-1">
                          <Button
                            variant="primary"
                            size="sm"
                            onClick={() => setGdprConfirmId(u.id)}
                            className="!bg-red-600 hover:!bg-red-700"
                          >
                            Process Deletion
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() =>
                              handleGdprExport(u.id, u.display_name)
                            }
                            disabled={gdprBusy[u.id]}
                          >
                            <Download size={14} /> Export Data
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleDismissDeletion(u.id)}
                          >
                            Dismiss Request
                          </Button>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {!u.is_activated && activationLinks[u.id] && (
                <div className="mt-3 bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
                  <p className="text-xs font-medium text-blue-800 dark:text-blue-200 mb-2">
                    Activation link:
                  </p>
                  <div className="flex items-center gap-2">
                    <code className="flex-1 text-xs break-all bg-blue-100 dark:bg-blue-900/40 px-3 py-1.5 rounded-lg text-blue-900 dark:text-blue-100">
                      {activationLinks[u.id]}
                    </code>
                    <button
                      onClick={() => copyToClipboard(activationLinks[u.id])}
                      className="p-1.5 rounded-lg hover:bg-blue-200 dark:hover:bg-blue-800 transition-colors"
                      title="Copy link"
                    >
                      <Copy size={14} />
                    </button>
                    <button
                      onClick={() => {
                        const link = activationLinks[u.id];
                        window.open(
                          activationQrPath(
                            activationTokenFromUrl(link),
                            u.display_name,
                            u.id,
                            activationLinkPurposes[u.id] || "initial_setup",
                          ),
                          "_blank",
                        );
                      }}
                      className="p-1.5 rounded-lg hover:bg-blue-200 dark:hover:bg-blue-800 transition-colors"
                      title="Show QR code"
                    >
                      <QrCode size={14} />
                    </button>
                    <button
                      onClick={() => handleNewActivationLink(u.id)}
                      className="p-1.5 rounded-lg text-gray-400 hover:text-blue-500 hover:bg-blue-200 dark:hover:bg-blue-800 transition-colors"
                      title="Regenerate activation link"
                    >
                      <RefreshCw size={14} />
                    </button>
                  </div>
                  {activationLinkExpiries[u.id] && (
                    <p className="mt-2 text-xs text-blue-700 dark:text-blue-300">
                      Expires {fmtDateTime(activationLinkExpiries[u.id])} (your local time).
                    </p>
                  )}
                </div>
              )}

              {expandedLinkUser === u.id && linkInfo[u.id] && (
                <div className="mt-3 border border-gray-200 dark:border-gray-700 rounded-lg p-4 space-y-2">
                  <p className="text-xs font-semibold text-gray-600 dark:text-gray-300 uppercase tracking-wide">
                    Activation link history
                  </p>
                  {linkInfo[u.id].length === 0 ? (
                    <p className="text-xs text-gray-400">
                      No activation links created yet.
                    </p>
                  ) : (
                    linkInfo[u.id].map(
                      (link: {
                        id: number;
                        purpose: string;
                        status: string;
                        created_at: string | null;
                        expires_at: string | null;
                        used_at: string | null;
                      }) => (
                        <div
                          key={link.id}
                          className="flex items-center justify-between gap-2 text-xs bg-gray-50 dark:bg-gray-800 rounded-lg px-3 py-2"
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <span
                              className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                                link.status === "active"
                                  ? "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300"
                                  : link.status === "used"
                                    ? "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300"
                                    : link.status === "expired"
                                      ? "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300"
                                      : "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300"
                              }`}
                            >
                              {link.status}
                            </span>
                            <span className="text-gray-500 dark:text-gray-400 truncate">
                              {activationPurposeLabel(link.purpose)} &middot;{" "}
                              {link.created_at
                                ? fmtDate(link.created_at)
                                : " - "}
                              {link.used_at &&
                                ` · used ${fmtDate(link.used_at)}`}
                            </span>
                          </div>
                          {link.status === "active" && (
                            <button
                              onClick={() =>
                                handleInvalidateLink(u.id, link.id)
                              }
                              className="text-red-500 hover:text-red-700 dark:hover:text-red-400 whitespace-nowrap font-medium"
                            >
                              Invalidate
                            </button>
                          )}
                        </div>
                      ),
                    )
                  )}
                </div>
              )}
            </Card>
          ))}
        </div>
      )}

      {selectedIds.size > 0 && (
        <div className="fixed inset-x-4 bottom-20 z-40 flex items-center justify-between rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-lg dark:border-gray-700 dark:bg-gray-900 sm:hidden">
          <span className="text-sm font-medium text-gray-800 dark:text-gray-100">
            {selectedIds.size} selected
          </span>
          <Button size="sm" onClick={openBatchWizard}>
            Continue <ChevronRight size={14} />
          </Button>
        </div>
      )}

      <MobileActionSheet
        open={emailConfirmUserId !== null}
        title={
          users.find((user) => user.id === emailConfirmUserId)?.is_activated
            ? "Passkey access"
            : "Send activation email"
        }
        description={
          users.find((user) => user.id === emailConfirmUserId)?.is_activated
            ? "Add access or recover an account without crowding the user card."
            : "The email includes a secure link and a printable QR code."
        }
        onClose={() => {
          setEmailConfirmUserId(null);
          setManagedPasskeyPurpose(null);
        }}
      >
        {(() => {
          const user = users.find(
            (candidate) => candidate.id === emailConfirmUserId,
          );
          if (!user) return null;
          const previous = emailResults[user.id];
          const previousPurpose =
            previous?.purpose || user.activation_email_purpose;
          const isRetry = ["failed", "unknown", "not_attempted"].includes(
            previous?.status || user.activation_email_status || "",
          ) &&
            (!user.is_activated || previousPurpose === managedPasskeyPurpose);

          if (user.is_activated && managedPasskeyPurpose === null) {
            return (
              <div className="space-y-3">
                <div className="rounded-lg bg-gray-50 px-4 py-3 dark:bg-gray-800">
                  <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                    {user.display_name}
                  </p>
                  <p className="mt-0.5 text-sm text-gray-500 dark:text-gray-400">
                    Choose the outcome before selecting how to share the link.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setManagedPasskeyPurpose("additional_passkey")}
                  className="min-h-11 w-full rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-left transition-colors hover:border-blue-300 hover:bg-blue-100 dark:border-blue-800 dark:bg-blue-900/20 dark:hover:bg-blue-900/30"
                >
                  <span className="block text-sm font-semibold text-blue-900 dark:text-blue-100">
                    Add another passkey
                  </span>
                  <span className="mt-1 block text-xs text-blue-700 dark:text-blue-300">
                    Existing passkeys and signed-in sessions remain valid.
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => setManagedPasskeyPurpose("credential_reset")}
                  className="min-h-11 w-full rounded-lg border border-gray-200 px-4 py-3 text-left transition-colors hover:border-amber-300 hover:bg-amber-50 dark:border-gray-700 dark:hover:border-amber-800 dark:hover:bg-amber-900/20"
                >
                  <span className="block text-sm font-semibold text-gray-900 dark:text-gray-100">
                    Reset passkeys
                  </span>
                  <span className="mt-1 block text-xs text-gray-500 dark:text-gray-400">
                    The new passkey replaces every previous passkey and session.
                  </span>
                </button>
              </div>
            );
          }

          const selectedPurpose: ActivationPurpose = user.is_activated
            ? managedPasskeyPurpose || "credential_reset"
            : "initial_setup";
          const generatedLink =
            activationLinkPurposes[user.id] === selectedPurpose
              ? activationLinks[user.id]
              : undefined;
          return (
            <div className="space-y-4">
              <div className="rounded-lg bg-gray-50 px-4 py-3 dark:bg-gray-800">
                <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                  {user.display_name}
                </p>
                {user.email && (
                  <p className="mt-0.5 break-all text-sm text-gray-500 dark:text-gray-400">
                    {user.email}
                  </p>
                )}
              </div>
              {user.is_activated && (
                <button
                  type="button"
                  onClick={() => setManagedPasskeyPurpose(null)}
                  className="inline-flex min-h-11 items-center gap-1.5 text-sm font-medium text-blue-600 hover:text-blue-700 dark:text-blue-400 sm:min-h-0"
                >
                  <ChevronLeft size={15} /> Change operation
                </button>
              )}
              <div className="space-y-2 text-sm text-gray-600 dark:text-gray-300">
                {selectedPurpose === "additional_passkey" && (
                  <p className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-blue-800 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-200">
                    This adds one passkey. Existing passkeys and signed-in
                    sessions remain valid.
                  </p>
                )}
                {selectedPurpose === "credential_reset" && (
                  <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200">
                    After registration succeeds, every previous passkey and
                    signed-in session will stop working.
                  </p>
                )}
                <p>
                  The new link will be valid for{" "}
                  {deliverySettings?.expiry_hours ?? 24} hours.
                </p>
                <p>
                  Any existing activation link or previously printed QR code
                  for this user will stop working.
                </p>
                {isRetry && (
                  <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200">
                    This retry creates a fresh link. The unsuccessful link has
                    already been invalidated.
                  </p>
                )}
              </div>
              {generatedLink && (
                <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 dark:border-blue-800 dark:bg-blue-900/20">
                  <p className="mb-2 text-xs font-medium text-blue-900 dark:text-blue-100">
                    {activationPurposeLabel(selectedPurpose)} link ready
                  </p>
                  <code className="block break-all rounded bg-white/70 px-2 py-1.5 text-xs text-blue-900 dark:bg-gray-900/40 dark:text-blue-100">
                    {generatedLink}
                  </code>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => copyToClipboard(generatedLink)}
                    >
                      <Copy size={14} /> Copy link
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        window.open(
                          activationQrPath(
                            activationTokenFromUrl(generatedLink),
                            user.display_name,
                            user.id,
                            selectedPurpose,
                          ),
                          "_blank",
                        )
                      }
                    >
                      <QrCode size={14} /> Show QR
                    </Button>
                  </div>
                </div>
              )}
              {emailActionErrors[user.id] && (
                <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-800 dark:bg-red-900/20 dark:text-red-200">
                  {emailActionErrors[user.id]}
                </p>
              )}
              <div className="grid gap-2 sm:grid-cols-2">
                <Button
                  variant="outline"
                  onClick={() =>
                    handleNewActivationLink(
                      user.id,
                      user.is_activated
                        ? (selectedPurpose as ManagedPasskeyPurpose)
                        : undefined,
                    )
                  }
                  disabled={linkBusy.has(user.id) || emailBusy.has(user.id)}
                >
                  <Link2 size={15} />
                  {linkBusy.has(user.id) ? "Generating..." : "Generate link / QR"}
                </Button>
                <Button
                  variant={
                    selectedPurpose === "credential_reset" ? "danger" : "primary"
                  }
                  onClick={() =>
                    handleSendActivationEmail(
                      user,
                      user.is_activated
                        ? (selectedPurpose as ManagedPasskeyPurpose)
                        : undefined,
                    )
                  }
                  disabled={
                    emailBusy.has(user.id) ||
                    linkBusy.has(user.id) ||
                    !deliverySettings?.configured ||
                    !user.has_valid_email
                  }
                >
                  <Send size={15} />
                  {emailBusy.has(user.id)
                    ? "Sending..."
                    : isRetry
                      ? "Send fresh email"
                      : "Email link and QR"}
                </Button>
              </div>
              {(!deliverySettings?.configured || !user.has_valid_email) && (
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  {!deliverySettings?.configured
                    ? "Email delivery is not configured. You can still generate a link or QR code."
                    : "Add a valid email address before sending. You can still generate a link or QR code."}
                </p>
              )}
            </div>
          );
        })()}
      </MobileActionSheet>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Announcements Tab
// ---------------------------------------------------------------------------
interface AnnouncementItem {
  id: number;
  event_id: number;
  title: string;
  body: string | null;
  created_by: string | null;
  created_at: string;
}

function AnnouncementsTab({
  selectedEventId,
}: {
  selectedEventId: number | "";
}) {
  const [announcements, setAnnouncements] = useState<AnnouncementItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [sendPush, setSendPush] = useState(true);
  const [sending, setSending] = useState(false);
  const [policyState, setPolicyState] = useState<{
    acknowledged: boolean;
    version: number | null;
    sha256: string | null;
  }>({ acknowledged: false, version: null, sha256: null });

  const fetchAnnouncements = useCallback(async () => {
    if (!selectedEventId) return;
    setLoading(true);
    try {
      const res = await apiFetch(
        `/api/v1/notifications/announcements/${selectedEventId}`,
      );
      if (res.ok) setAnnouncements(await res.json());
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [selectedEventId]);

  useEffect(() => {
    fetchAnnouncements();
  }, [fetchAnnouncements]);

  useEffect(() => {
    if (!selectedEventId) return;
    void apiFetch(`/api/v1/calendar/${selectedEventId}`)
      .then(async (response) => response.ok ? response.json() : null)
      .then((calendar) => {
        if (!calendar) return;
        setPolicyState({
          acknowledged: calendar.data_policy_acknowledged ?? false,
          version: calendar.data_policy_version ?? null,
          sha256: calendar.data_policy_sha256 ?? null,
        });
      })
      .catch(() => setPolicyState({ acknowledged: false, version: null, sha256: null }));
  }, [selectedEventId]);

  const handleSend = async () => {
    if (!selectedEventId || !title.trim()) return;
    setSending(true);
    try {
      const res = await apiFetch("/api/v1/notifications/announcements", {
        method: "POST",
        body: JSON.stringify({
          event_id: selectedEventId,
          title: title.trim(),
          body: body.trim() || null,
          push: sendPush,
        }),
      });
      if (res.ok) {
        setTitle("");
        setBody("");
        fetchAnnouncements();
      }
    } catch {
      // ignore
    } finally {
      setSending(false);
    }
  };

  const handleDelete = async (id: number) => {
    const res = await apiFetch(`/api/v1/notifications/announcements/${id}`, {
      method: "DELETE",
    });
    if (res.ok) {
      setAnnouncements((prev) => prev.filter((a) => a.id !== id));
    }
  };

  if (!selectedEventId) {
    return (
      <div className="text-center py-12">
        <Megaphone
          size={32}
          className="mx-auto text-gray-300 dark:text-gray-600 mb-3"
        />
        <p className="text-gray-500 dark:text-gray-400 text-sm">
          Select an event above to manage announcements.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
        Announcements
      </h2>

      {/* New announcement form */}
      <Card className="p-4">
        <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-3 flex items-center gap-2">
          <Megaphone size={14} />
          New Announcement
        </h3>
        <div className="space-y-3">
          <PermittedDataInputNotice
            acknowledged={policyState.acknowledged}
            version={policyState.version}
            sha256={policyState.sha256}
          />
          <Input
            placeholder="Participant-visible announcement title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <textarea
            placeholder="Participant-visible operational announcement (optional)"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={2}
            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 text-sm resize-none"
          />
          <div className="flex items-center justify-between">
            <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
              <input
                type="checkbox"
                checked={sendPush}
                onChange={(e) => setSendPush(e.target.checked)}
                className="rounded"
              />
              Also send push notification
            </label>
            <Button
              onClick={handleSend}
              disabled={!title.trim() || sending}
              size="sm"
            >
              <Send size={14} className="mr-1.5" />
              {sending ? "Sending..." : "Post"}
            </Button>
          </div>
        </div>
      </Card>

      {/* Existing announcements */}
      {loading ? (
        <p className="text-gray-500 dark:text-gray-400 text-sm">Loading...</p>
      ) : announcements.length === 0 ? (
        <p className="text-gray-500 dark:text-gray-400 text-sm text-center py-8">
          No announcements yet.
        </p>
      ) : (
        <div className="space-y-2">
          {announcements.map((ann) => (
            <Card key={ann.id} className="p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <p className="font-medium text-gray-900 dark:text-gray-100 text-sm">
                    {ann.title}
                  </p>
                  {ann.body && (
                    <p className="text-gray-600 dark:text-gray-400 text-sm mt-0.5">
                      {ann.body}
                    </p>
                  )}
                  <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">
                    {ann.created_by && `${ann.created_by} · `}
                    {ann.created_at ? fmtDateTime(ann.created_at) : ""}
                  </p>
                </div>
                <button
                  onClick={() => handleDelete(ann.id)}
                  className="p-1.5 rounded text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                  title="Delete announcement"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// History Tab
// ---------------------------------------------------------------------------
interface SnapshotSummary {
  id: number;
  version: number;
  task_count: number;
  person_count: number;
  edits_count: number;
  source: string | null;
  label: string | null;
  frozen: boolean;
  created_at: string | null;
}

function HistoryTab({
  selectedEventId,
}: {
  selectedEventId: number | "";
}) {
  const router = useRouter();
  const [snapshots, setSnapshots] = useState<SnapshotSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [restoring, setRestoring] = useState<number | null>(null);
  const [confirmRestore, setConfirmRestore] = useState<number | null>(null);
  const [editingLabel, setEditingLabel] = useState<number | null>(null);
  const [labelDraft, setLabelDraft] = useState("");
  const [maxSnaps, setMaxSnaps] = useState(20);
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const [comparisonLoading, setComparisonLoading] = useState(false);
  const [comparisonSummary, setComparisonSummary] =
    useState<SnapshotComparisonSummary | null>(null);

  useEffect(() => {
    apiFetch("/api/v1/admin/settings")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.max_snapshots_per_event)
          setMaxSnaps(data.max_snapshots_per_event.value);
      })
      .catch(() => {});
  }, []);

  const fetchSnapshots = useCallback(async () => {
    if (!selectedEventId) return;
    setLoading(true);
    try {
      const res = await apiFetch(
        `/api/v1/admin/events/${selectedEventId}/history`,
      );
      if (res.ok) setSnapshots(await res.json());
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [selectedEventId]);

  useEffect(() => {
    fetchSnapshots();
  }, [fetchSnapshots]);

  const viewSnapshot = (version: number) => {
    router.push(`/calendar?event=${selectedEventId}&snapshot=${version}`);
  };

  const compareSnapshot = async (snap: SnapshotSummary) => {
    if (!selectedEventId) return;
    setComparisonOpen(true);
    setComparisonLoading(true);
    setComparisonSummary(null);
    try {
      const [snapshotRes, currentRes] = await Promise.all([
        apiFetch(
          `/api/v1/admin/events/${selectedEventId}/history/${snap.version}`,
        ),
        apiFetch(`/api/v1/calendar/${selectedEventId}`),
      ]);
      if (!snapshotRes.ok)
        throw new Error("The selected snapshot could not be loaded.");
      if (!currentRes.ok)
        throw new Error("The current live schedule could not be loaded.");
      const snapshotDetail = await snapshotRes.json();
      const currentSchedule = await currentRes.json();
      setComparisonSummary(
        compareSnapshotToCurrent(
          snapshotDetail?.snapshot?.tasks,
          currentSchedule?.tasks,
          {
            snapshotId: String(snap.version),
            snapshotLabel: snap.label || `Version ${snap.version}`,
            snapshotCreatedAt: snap.created_at,
          },
        ),
      );
    } catch (err) {
      setComparisonSummary(
        createUnavailableSnapshotComparison({
          snapshotId: String(snap.version),
          snapshotLabel: snap.label || `Version ${snap.version}`,
          reason:
            err instanceof Error
              ? err.message
              : "The schedules could not be compared.",
        }),
      );
    } finally {
      setComparisonLoading(false);
    }
  };

  const handleDelete = async (version: number) => {
    if (!selectedEventId) return;
    const res = await withReauth(() =>
      apiFetch(
        `/api/v1/admin/events/${selectedEventId}/history/${version}`,
        { method: "DELETE" },
      ),
    );
    if (res.ok) {
      setSnapshots((prev) => prev.filter((s) => s.version !== version));
    }
  };

  const handleRestore = async (version: number) => {
    if (!selectedEventId) return;
    setRestoring(version);
    try {
      const res = await withReauth(() =>
        apiFetch(
          `/api/v1/admin/events/${selectedEventId}/history/${version}/restore`,
          { method: "POST", body: JSON.stringify({}) },
        ),
      );
      if (res.ok) {
        fetchSnapshots();
      }
    } catch {
      // ignore
    } finally {
      setRestoring(null);
      setConfirmRestore(null);
    }
  };

  const patchSnapshot = async (
    version: number,
    body: { label?: string | null; frozen?: boolean },
  ) => {
    if (!selectedEventId) return;
    const res = await apiFetch(
      `/api/v1/admin/events/${selectedEventId}/history/${version}`,
      { method: "PATCH", body: JSON.stringify(body) },
    );
    if (res.ok) {
      const updated: SnapshotSummary = await res.json();
      setSnapshots((prev) =>
        prev.map((s) => (s.version === version ? updated : s)),
      );
    }
  };

  const startRename = (snap: SnapshotSummary) => {
    setEditingLabel(snap.version);
    setLabelDraft(snap.label || "");
  };

  const commitRename = (version: number) => {
    const trimmed = labelDraft.trim();
    patchSnapshot(version, { label: trimmed || null });
    setEditingLabel(null);
  };

  const cancelRename = () => {
    setEditingLabel(null);
  };

  const toggleFrozen = (snap: SnapshotSummary) => {
    patchSnapshot(snap.version, { frozen: !snap.frozen });
  };

  const frozenCount = snapshots.filter((s) => s.frozen).length;

  if (!selectedEventId) {
    return (
      <div className="text-center py-12">
        <History
          size={32}
          className="mx-auto text-gray-300 dark:text-gray-600 mb-3"
        />
        <p className="text-gray-500 dark:text-gray-400 text-sm">
          Select an event above to view publish history.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <SnapshotComparisonModal
        open={comparisonOpen}
        loading={comparisonLoading}
        summary={comparisonSummary}
        onClose={() => setComparisonOpen(false)}
      />

      {/* Header row */}
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
          Publish Snapshots
        </h2>
        <Button size="sm" variant="outline" onClick={fetchSnapshots}>
          <RefreshCw size={14} className="mr-1" />
          Refresh
        </Button>
      </div>

      {loading ? (
        <p className="text-gray-500 dark:text-gray-400 text-sm">Loading...</p>
      ) : snapshots.length === 0 ? (
        <p className="text-gray-500 dark:text-gray-400 text-sm text-center py-8">
          No publish snapshots yet.
        </p>
      ) : (
        <div className="space-y-2">
          {snapshots.map((snap) => (
            <Card
              key={snap.id}
              className={`p-3 ${snap.frozen ? "border-blue-300 dark:border-blue-700 bg-blue-50/50 dark:bg-blue-950/20" : ""}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div
                  className="flex-1 min-w-0 cursor-pointer"
                  onClick={() =>
                    editingLabel !== snap.version && viewSnapshot(snap.version)
                  }
                >
                  {editingLabel === snap.version ? (
                    <div className="flex items-center gap-1">
                      <input
                        type="text"
                        value={labelDraft}
                        onChange={(e) => setLabelDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") commitRename(snap.version);
                          if (e.key === "Escape") cancelRename();
                        }}
                        onBlur={() => commitRename(snap.version)}
                        maxLength={100}
                        autoFocus
                        placeholder={`Version ${snap.version}`}
                        className="text-sm font-medium bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded px-1.5 py-0.5 w-full outline-none focus:ring-1 focus:ring-blue-500"
                        onClick={(e) => e.stopPropagation()}
                      />
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          commitRename(snap.version);
                        }}
                        className="p-1 rounded text-green-600 hover:bg-green-50 dark:hover:bg-green-900/20"
                      >
                        <Check size={14} />
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1.5 group">
                      <p className="font-medium text-gray-900 dark:text-gray-100 text-sm">
                        {snap.label || `Version ${snap.version}`}
                      </p>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          startRename(snap);
                        }}
                        className="p-0.5 rounded text-gray-300 dark:text-gray-600 opacity-0 group-hover:opacity-100 hover:text-gray-500 dark:hover:text-gray-400 transition-opacity"
                        title="Rename"
                      >
                        <Pencil size={12} />
                      </button>
                    </div>
                  )}
                  {snap.label && (
                    <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                      Version {snap.version}
                    </p>
                  )}
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                    {snap.task_count} tasks · {snap.person_count} people
                    {snap.edits_count > 0 && ` · ${snap.edits_count} edits`}
                    {snap.source && ` · ${snap.source}`}
                  </p>
                  <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                    {snap.created_at ? fmtDateTime(snap.created_at) : ""}
                  </p>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => toggleFrozen(snap)}
                    className={`p-1.5 rounded transition-colors ${
                      snap.frozen
                        ? "text-blue-500 hover:text-blue-700 hover:bg-blue-50 dark:hover:bg-blue-900/20"
                        : frozenCount >= maxSnaps
                          ? "text-gray-300 dark:text-gray-600 cursor-not-allowed"
                          : "text-gray-400 hover:text-blue-500 hover:bg-blue-50 dark:hover:bg-blue-900/20"
                    }`}
                    title={
                      snap.frozen
                        ? "Unfreeze snapshot"
                        : frozenCount >= maxSnaps
                          ? "All slots frozen"
                          : "Freeze snapshot"
                    }
                    disabled={!snap.frozen && frozenCount >= maxSnaps}
                  >
                    {snap.frozen ? <Lock size={14} /> : <Unlock size={14} />}
                  </button>
                  <button
                    onClick={() => compareSnapshot(snap)}
                    className="p-1.5 rounded text-gray-400 hover:text-amber-600 hover:bg-amber-50 dark:hover:bg-amber-900/20 transition-colors"
                    title="Compare to current"
                  >
                    <FileText size={14} />
                  </button>
                  <button
                    onClick={() => viewSnapshot(snap.version)}
                    className="p-1.5 rounded text-gray-400 hover:text-blue-500 hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors"
                    title="View schedule"
                  >
                    <Eye size={14} />
                  </button>
                  {confirmRestore === snap.version ? (
                    <>
                      <Button
                        size="sm"
                        onClick={() => handleRestore(snap.version)}
                        disabled={restoring === snap.version}
                      >
                        <RotateCcw size={14} className="mr-1" />
                        {restoring === snap.version
                          ? "Restoring..."
                          : "Confirm"}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setConfirmRestore(null)}
                      >
                        Cancel
                      </Button>
                    </>
                  ) : (
                    <button
                      onClick={() => setConfirmRestore(snap.version)}
                      className="p-1.5 rounded text-gray-400 hover:text-blue-500 hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors"
                      title="Restore this version"
                    >
                      <RotateCcw size={14} />
                    </button>
                  )}
                  {
                    <button
                      onClick={() => !snap.frozen && handleDelete(snap.version)}
                      className={`p-1.5 rounded transition-colors ${
                        snap.frozen
                          ? "text-gray-300 dark:text-gray-600 cursor-not-allowed"
                          : "text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20"
                      }`}
                      title={
                        snap.frozen ? "Unfreeze to delete" : "Delete snapshot"
                      }
                      disabled={snap.frozen}
                    >
                      <Trash2 size={14} />
                    </button>
                  }
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// High Availability Tab
// ---------------------------------------------------------------------------
interface HAReplicationStatus {
  mode: "standalone" | "ha";
  state?: string;
  node_id?: string;
  peer_node_id?: string;
  holder_node_id?: string;
  generation?: number;
  automatic_failover?: boolean;
  interval_minutes?: number;
  job_id?: string;
  job_state?: string;
  last_success_at?: string;
  last_received_at?: string;
  potential_data_loss_seconds?: number | null;
  peer_reachable?: boolean | null;
  peer_compatible?: boolean | null;
  message?: string;
}

interface HAClusterStatus {
  cluster_id?: string | null;
  local_node_id?: string | null;
  peer_node_id?: string | null;
  holder_node_id?: string | null;
  generation?: number;
  routing_ready?: boolean;
  automatic_failover?: boolean;
  failover_delay_seconds?: number;
  witness_age_seconds?: number | null;
  lease_remaining_seconds?: number | null;
}

interface HATransitionStatus {
  phase: string;
  reason?: string | null;
  from_node_id?: string | null;
  to_node_id?: string | null;
  started_at?: string | null;
  last_contact_at?: string | null;
  detected_at?: string | null;
  decision_at?: string | null;
  routing_ready_at?: string | null;
  earliest_failover_at?: string | null;
  recovery_point_at?: string | null;
}

type HAIncidentCategory =
  | "automatic_failover"
  | "planned_handoff"
  | "primary_outage";

interface HAIncidentGroup {
  id: string;
  category: HAIncidentCategory;
  state: "open" | "routing" | "service_restored" | "resolved";
  node_id?: string | null;
  from_node_id?: string | null;
  to_node_id?: string | null;
  generation?: number | null;
  service_impact: boolean;
  started_at: string;
  last_contact_at?: string | null;
  detected_at?: string | null;
  safety_boundary_at?: string | null;
  recovery_point_at?: string | null;
  decision_at?: string | null;
  routing_ready_at?: string | null;
  service_restored_at?: string | null;
  redundancy_restored_at?: string | null;
  resolved_at?: string | null;
  downtime_seconds?: number | null;
  event_count?: number;
}

interface HADowntimeAggregate {
  incident_count: number;
  active_count: number;
  total_downtime_seconds: number;
  average_downtime_seconds: number | null;
}

interface HAIncidentSummary {
  retention_days: number;
  overall: HADowntimeAggregate;
  planned_handoff: HADowntimeAggregate;
  automatic_failover: HADowntimeAggregate;
  primary_outage: HADowntimeAggregate;
}

interface HANodeStatus {
  node_id: string;
  healthy?: boolean;
  is_holder?: boolean;
  last_heartbeat_at?: string | null;
  heartbeat_age_seconds?: number | null;
  release_hash?: string | null;
  bundle_id?: string | null;
  bundle_generation?: number | null;
  bundle_created_at?: string | null;
  smtp_configured?: boolean;
  smtp_ready?: boolean;
  smtp_checked_at?: string | null;
  smtp_error_code?: string | null;
  smtp_config_fingerprint?: string | null;
  critical_pending?: boolean;
}

interface HADashboard {
  format: "mp-opt-ha-dashboard-v1";
  observed_at: string;
  mode: "standalone" | "ha";
  cluster: HAClusterStatus;
  transition: HATransitionStatus;
  last_recovery?: {
    kind?: string;
    completed_at?: string | null;
    recovery_seconds?: number | null;
  } | null;
  nodes: HANodeStatus[];
  replication: HAReplicationStatus;
  recovery: Record<string, unknown>;
  incidents: Array<Record<string, unknown>>;
  incident_groups?: HAIncidentGroup[];
  incident_summary?: Partial<HAIncidentSummary>;
}

const ACTIVE_REPLICATION_STATES = [
  "queued", "capturing", "transferring", "verifying", "applying",
];

function formatDuration(totalSeconds: number | null | undefined): string {
  if (totalSeconds == null || !Number.isFinite(totalSeconds)) return "Unknown";
  const seconds = Math.max(0, Math.round(totalSeconds));
  if (seconds < 60) return `${seconds} second${seconds === 1 ? "" : "s"}`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"}`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return `${hours}h${remainder ? ` ${remainder}m` : ""}`;
}

function transitionReasonLabel(reason: string | null | undefined): string {
  if (reason === "planned_handoff") return "Planned handover";
  if (reason === "application_unhealthy") return "Primary health check failed";
  if (reason === "node_unreachable") return "Contact with the primary was lost";
  if (reason === "automatic_failover") return "Automatic failover";
  return "Service ownership is changing";
}

function incidentCategoryLabel(category: HAIncidentCategory): string {
  if (category === "automatic_failover") return "Automatic failover";
  if (category === "planned_handoff") return "Planned handover";
  return "Primary outage";
}

function incidentStateLabel(group: HAIncidentGroup): string {
  if (group.state === "routing") return "Restoring live service";
  if (group.state === "service_restored") return "Service restored · redundancy degraded";
  if (group.state === "open") {
    return group.service_impact ? "Live outage ongoing" : "Redundancy degraded";
  }
  return "Resolved";
}

interface HATimelineCheckpoint {
  label: string;
  at?: string | null;
  complete: boolean;
}

interface HATimelineSource {
  category?: HAIncidentCategory;
  state?: HAIncidentGroup["state"];
  reason?: string | null;
  started_at?: string | null;
  last_contact_at?: string | null;
  detected_at?: string | null;
  earliest_failover_at?: string | null;
  safety_boundary_at?: string | null;
  recovery_point_at?: string | null;
  decision_at?: string | null;
  routing_ready_at?: string | null;
  service_restored_at?: string | null;
  redundancy_restored_at?: string | null;
  resolved_at?: string | null;
}

function buildHATimelineCheckpoints(
  source: HATimelineSource,
  now: number,
  historical = false,
): HATimelineCheckpoint[] {
  const category = source.category || (
    source.reason === "planned_handoff" ? "planned_handoff" :
      source.reason === "automatic_failover" ? "automatic_failover" : undefined
  );
  const safetyAt = source.safety_boundary_at || source.earliest_failover_at;
  const safetyComplete = Boolean(
    source.decision_at || (safetyAt && now >= new Date(safetyAt).getTime()),
  );
  const optionalRecoveryPoint = source.recovery_point_at || !historical
    ? [{
        label: "Verified recovery point confirmed",
        at: source.recovery_point_at,
        complete: Boolean(source.recovery_point_at),
      }]
    : [];

  if (category === "planned_handoff") {
    return [
      { label: "Handover request accepted", at: source.detected_at || source.started_at, complete: Boolean(source.detected_at || source.started_at) },
      ...optionalRecoveryPoint,
      { label: "Writer ownership transferred", at: source.decision_at, complete: Boolean(source.decision_at) },
      { label: "Live routing restored", at: source.routing_ready_at || source.service_restored_at, complete: Boolean(source.routing_ready_at || source.service_restored_at) },
    ];
  }
  if (category === "automatic_failover" || category === "primary_outage" || source.reason === "node_unreachable" || source.reason === "application_unhealthy") {
    const checkpoints: HATimelineCheckpoint[] = [
      { label: "Last contact with previous primary", at: source.last_contact_at || source.started_at, complete: Boolean(source.last_contact_at || source.started_at) },
      { label: "Primary failure detected", at: source.detected_at || source.started_at, complete: Boolean(source.detected_at || source.started_at) },
    ];
    if (category === "automatic_failover") {
      checkpoints.push(
        { label: "Two-minute safety boundary", at: safetyAt, complete: safetyComplete },
        ...optionalRecoveryPoint,
        { label: "Writer ownership transferred", at: source.decision_at, complete: Boolean(source.decision_at) },
      );
    }
    checkpoints.push({
      label: "Live routing restored",
      at: source.routing_ready_at || source.service_restored_at,
      complete: Boolean(source.routing_ready_at || source.service_restored_at),
    });
    if (source.redundancy_restored_at || source.state === "service_restored") {
      checkpoints.push({
        label: "Previous node rejoined; redundancy restored",
        at: source.redundancy_restored_at,
        complete: Boolean(source.redundancy_restored_at),
      });
    }
    return checkpoints;
  }
  return [
    { label: "Node availability incident detected", at: source.detected_at || source.started_at, complete: Boolean(source.detected_at || source.started_at) },
    { label: "Node contact restored", at: source.redundancy_restored_at || source.resolved_at, complete: Boolean(source.redundancy_restored_at || source.resolved_at) },
  ];
}

function HATimeline({ checkpoints }: { checkpoints: HATimelineCheckpoint[] }) {
  const currentCheckpoint = checkpoints.findIndex((checkpoint) => !checkpoint.complete);
  return (
    <ol className="divide-y divide-gray-200 dark:divide-gray-700">
      {checkpoints.map((checkpoint, index) => {
        const isCurrent = index === currentCheckpoint;
        return (
          <li key={checkpoint.label} className="flex items-start gap-3 px-5 py-3 text-sm">
            <span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border ${checkpoint.complete ? "border-green-500 bg-green-500 text-white" : isCurrent ? "border-amber-500 bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-200" : "border-gray-300 text-gray-400 dark:border-gray-600"}`}>
              {checkpoint.complete ? <Check size={13} /> : <span className="h-1.5 w-1.5 rounded-full bg-current" />}
            </span>
            <div className="min-w-0 flex-1">
              <p className={isCurrent ? "font-semibold text-amber-800 dark:text-amber-200" : "font-medium text-gray-800 dark:text-gray-200"}>{checkpoint.label}</p>
              <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                {checkpoint.at ? fmtDateTime(checkpoint.at) : isCurrent ? "In progress" : "Pending"}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function summariseIncidentGroups(groups: HAIncidentGroup[]): HAIncidentSummary {
  const aggregate = (selected: HAIncidentGroup[]): HADowntimeAggregate => {
    const impacting = selected.filter((group) => group.service_impact);
    const total = impacting.reduce((sum, group) => sum + (group.downtime_seconds || 0), 0);
    return {
      incident_count: impacting.length,
      active_count: impacting.filter((group) => !group.service_restored_at).length,
      total_downtime_seconds: total,
      average_downtime_seconds: impacting.length ? Math.round(total / impacting.length) : null,
    };
  };
  return {
    retention_days: 90,
    overall: aggregate(groups),
    planned_handoff: aggregate(groups.filter((group) => group.category === "planned_handoff")),
    automatic_failover: aggregate(groups.filter((group) => group.category === "automatic_failover")),
    primary_outage: aggregate(groups.filter((group) => group.category === "primary_outage")),
  };
}

function legacyIncidentGroups(incidents: Array<Record<string, unknown>>): HAIncidentGroup[] {
  return incidents.map<HAIncidentGroup>((incident, index) => {
    const kind = String(incident.kind || "");
    const serviceImpact = incident.service_impact === true ||
      kind === "automatic_failover" || kind === "planned_handoff";
    const category: HAIncidentCategory = kind === "automatic_failover"
      ? "automatic_failover"
      : kind === "planned_handoff"
        ? "planned_handoff"
        : "primary_outage";
    const recoverySeconds = typeof incident.recovery_seconds === "number"
      ? incident.recovery_seconds : null;
    return {
      id: String(incident.id || `legacy-${index}`),
      category,
      state: String(incident.state) === "resolved" ? "resolved" : "open",
      node_id: typeof incident.node_id === "string" ? incident.node_id : null,
      from_node_id: typeof incident.from_node_id === "string" ? incident.from_node_id : null,
      to_node_id: typeof incident.to_node_id === "string" ? incident.to_node_id : null,
      generation: typeof incident.generation === "number" ? incident.generation : null,
      service_impact: serviceImpact,
      started_at: String(incident.started_at || incident.detected_at || ""),
      detected_at: typeof incident.detected_at === "string" ? incident.detected_at : null,
      decision_at: typeof incident.decision_at === "string" ? incident.decision_at : null,
      routing_ready_at: typeof incident.routing_ready_at === "string" ? incident.routing_ready_at : null,
      service_restored_at: typeof incident.routing_ready_at === "string" ? incident.routing_ready_at : null,
      resolved_at: typeof incident.resolved_at === "string" ? incident.resolved_at : null,
      downtime_seconds: recoverySeconds,
      event_count: 1,
    };
  }).filter((group) => Boolean(group.started_at) && group.service_impact);
}

function HighAvailabilityTab() {
  const { state: serviceState, status: publicStatus } = useServiceAvailability();
  const [dashboard, setDashboard] = useState<HADashboard | null>(null);
  const [interval, setIntervalValue] = useState(15);
  const [intervalMeta, setIntervalMeta] = useState<SettingMeta | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [expandedIncidentIds, setExpandedIncidentIds] = useState<string[]>([]);

  const refresh = useCallback(async () => {
    const statusResponse = await apiFetch("/api/v1/admin/ha/status");
    if (statusResponse.ok) setDashboard(await statusResponse.json());
  }, []);

  const refreshSettings = useCallback(async () => {
    const settingsResponse = await apiFetch("/api/v1/admin/settings");
    if (settingsResponse.ok) {
      const all: Record<string, SettingMeta> = await settingsResponse.json();
      const meta = all.ha_replication_interval_minutes;
      if (meta) {
        setIntervalMeta(meta);
        setIntervalValue(meta.value);
      }
    }
  }, []);

  const protectedTransitionActive = Boolean(
    dashboard?.transition && dashboard.transition.phase !== "stable",
  );
  const publicTransitionActive = ACTIVE_HA_SERVICE_STATES.has(serviceState);
  const transitionActive = protectedTransitionActive || publicTransitionActive;

  useEffect(() => { refresh().catch(() => undefined); }, [refresh]);
  useEffect(() => {
    if (serviceState === "ready") refreshSettings().catch(() => undefined);
  }, [refreshSettings, serviceState]);
  useEffect(() => {
    const timer = window.setInterval(
      () => refresh().catch(() => undefined),
      transitionActive ? 2000 : 15000,
    );
    return () => window.clearInterval(timer);
  }, [refresh, transitionActive]);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!activeJobId) return;
    let cancelled = false;
    const poll = async () => {
      const response = await apiFetch(`/api/v1/admin/ha/replication/${activeJobId}`);
      if (cancelled) return;
      if (!response.ok) {
        await refresh();
        return;
      }
      const job: HAReplicationStatus = await response.json();
      setDashboard((current) => current ? { ...current, replication: job } : current);
      if (job.job_state === "succeeded" || job.job_state === "failed") {
        setActiveJobId(null);
        setNotice({
          type: job.job_state === "succeeded" ? "success" : "error",
          message: job.message || (job.job_state === "succeeded"
            ? "The peer accepted and verified the complete application state."
            : "Replication failed; the previous verified peer copy remains unchanged."),
        });
        await refresh();
      }
    };
    poll().catch(() => undefined);
    const timer = window.setInterval(() => poll().catch(() => undefined), 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [activeJobId, refresh]);

  const handleReplicateNow = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const response = await withReauth(() =>
        apiFetch("/api/v1/admin/ha/replication", { method: "POST", body: "{}" }),
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Replication could not be queued.");
      setActiveJobId(data.job_id);
      setDashboard((current) => current ? {
        ...current,
        replication: { ...current.replication, job_id: data.job_id, job_state: "queued", state: "replicating" },
      } : current);
      setNotice({ type: "success", message: "Replication queued. This page will follow it until verification completes." });
    } catch (error: unknown) {
      setNotice({ type: "error", message: error instanceof Error ? error.message : "Re-authentication cancelled." });
    } finally {
      setBusy(false);
    }
  };

  const saveInterval = async () => {
    if (!intervalMeta) return;
    setBusy(true);
    setNotice(null);
    try {
      const response = await withReauth(() => apiFetch("/api/v1/admin/settings", {
        method: "PUT",
        body: JSON.stringify({ settings: { ha_replication_interval_minutes: interval } }),
      }));
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.errors?.length) throw new Error(data.detail || data.errors?.[0]?.error || "Frequency could not be saved.");
      setNotice({ type: "success", message: `Replication frequency saved: every ${interval} minutes.` });
      await Promise.all([refresh(), refreshSettings()]);
    } catch (error: unknown) {
      setNotice({ type: "error", message: error instanceof Error ? error.message : "Re-authentication cancelled." });
    } finally {
      setBusy(false);
    }
  };

  if (!dashboard) return <div className="py-12 text-center text-gray-500 dark:text-gray-400">Loading high-availability status...</div>;

  const replication = dashboard.replication;
  const cluster = dashboard.cluster;
  const elapsedSinceRefresh = Math.max(0, Math.floor((now - new Date(dashboard.observed_at).getTime()) / 1000));
  const recoveryAge = replication.potential_data_loss_seconds == null
    ? null : replication.potential_data_loss_seconds + elapsedSinceRefresh;
  const isPrimary = cluster.holder_node_id === cluster.local_node_id;
  const recovery = dashboard.recovery;
  const latestDatabase = recovery.latest_database as Record<string, unknown> | null | undefined;
  const latestFull = recovery.latest_full as Record<string, unknown> | null | undefined;
  const portable = recovery.portable_export as Record<string, unknown> | null | undefined;
  const protectedState = replication.state === "healthy" && recovery.state === "protected";
  const stableNodes = [...dashboard.nodes].sort((left, right) =>
    left.node_id.localeCompare(right.node_id),
  );
  const smtpFingerprints = stableNodes
    .map((node) => node.smtp_config_fingerprint)
    .filter((value): value is string => Boolean(value));
  const smtpConfigurationMatches = smtpFingerprints.length === 2 &&
    smtpFingerprints[0] === smtpFingerprints[1];
  const incidentGroups = dashboard.incident_groups?.length
    ? dashboard.incident_groups
    : legacyIncidentGroups(dashboard.incidents);
  const computedIncidentSummary = summariseIncidentGroups(incidentGroups);
  const incidentSummary: HAIncidentSummary = {
    retention_days: dashboard.incident_summary?.retention_days || 90,
    overall: dashboard.incident_summary?.overall || computedIncidentSummary.overall,
    planned_handoff: dashboard.incident_summary?.planned_handoff || computedIncidentSummary.planned_handoff,
    automatic_failover: dashboard.incident_summary?.automatic_failover || computedIncidentSummary.automatic_failover,
    primary_outage: dashboard.incident_summary?.primary_outage || computedIncidentSummary.primary_outage,
  };
  const latestActiveIncident = dashboard.incidents.find((incident) =>
    incident.state === "open" || incident.state === "routing",
  );
  const protectedTransition = dashboard.transition?.phase !== "stable"
    ? dashboard.transition : null;
  const fallbackFromNode = cluster.holder_node_id || null;
  const fallbackTargetNode = stableNodes.find((node) => node.node_id !== fallbackFromNode)?.node_id || null;
  const fallbackLastContact = stableNodes.find((node) => node.node_id === fallbackFromNode)?.last_heartbeat_at || null;
  const activeTransition: HATransitionStatus | null = protectedTransition || (
    publicTransitionActive
      ? {
          phase: serviceState,
          reason: publicStatus?.reason || null,
          from_node_id: fallbackFromNode,
          to_node_id: fallbackTargetNode,
          started_at: publicStatus?.transition_started_at || null,
          last_contact_at: fallbackLastContact,
          detected_at: typeof latestActiveIncident?.detected_at === "string"
            ? latestActiveIncident.detected_at : null,
          decision_at: typeof latestActiveIncident?.decision_at === "string"
            ? latestActiveIncident.decision_at : null,
          earliest_failover_at: publicStatus?.earliest_failover_at || null,
          recovery_point_at: publicStatus?.recovery_point_at || null,
        }
      : null
  );
  const transitionCheckpoints = activeTransition
    ? buildHATimelineCheckpoints(activeTransition, now)
    : [];
  const downtimeSummaryCards: Array<{
    key: keyof Omit<HAIncidentSummary, "retention_days">;
    label: string;
    aggregate: HADowntimeAggregate;
  }> = [
    { key: "overall", label: "All live outages", aggregate: incidentSummary.overall },
    { key: "planned_handoff", label: "Planned handovers", aggregate: incidentSummary.planned_handoff },
    { key: "automatic_failover", label: "Automatic failovers", aggregate: incidentSummary.automatic_failover },
    { key: "primary_outage", label: "Primary outages without transition", aggregate: incidentSummary.primary_outage },
  ];

  const toggleIncident = (id: string) => {
    setExpandedIncidentIds((current) => current.includes(id)
      ? current.filter((candidate) => candidate !== id)
      : [...current, id]);
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">High Availability</h2>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Writer lease, peer replication, recovery points and recorded service transitions.</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => {
            refresh().catch(() => undefined);
            if (serviceState === "ready") refreshSettings().catch(() => undefined);
          }}><RefreshCw size={14} /> Refresh</Button>
          {dashboard.mode === "ha" && (
            <Button size="sm" onClick={handleReplicateNow} disabled={busy || !isPrimary || ACTIVE_REPLICATION_STATES.includes(replication.job_state || "")}>
              <RefreshCw size={14} className={ACTIVE_REPLICATION_STATES.includes(replication.job_state || "") ? "animate-spin" : ""} /> Replicate now
            </Button>
          )}
        </div>
      </div>

      {notice && <div className={`rounded-lg border px-4 py-3 text-sm ${notice.type === "success" ? "border-green-200 bg-green-50 text-green-800 dark:border-green-800 dark:bg-green-900/20 dark:text-green-200" : "border-red-200 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-900/20 dark:text-red-200"}`}>{notice.message}</div>}

      {activeTransition && (
        <Card className="overflow-hidden border-amber-300 dark:border-amber-700" role="status" aria-live="polite">
          <div className="border-b border-amber-200 bg-amber-50 px-5 py-4 dark:border-amber-800 dark:bg-amber-900/20">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-300">Transition ongoing</p>
                <h3 className="mt-1 font-semibold text-gray-900 dark:text-gray-100">{transitionReasonLabel(activeTransition.reason)}</h3>
                <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
                  {activeTransition.from_node_id || "Previous primary"} → {activeTransition.to_node_id || "Standby"}
                  {cluster.generation ? ` · generation ${cluster.generation}` : ""}
                </p>
              </div>
              <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">
                Updating every 2 seconds
              </span>
            </div>
          </div>
          <HATimeline checkpoints={transitionCheckpoints} />
        </Card>
      )}

      {dashboard.mode === "standalone" ? (
        <Card className="p-5">
          <div className="flex items-start gap-3"><Server size={20} className="mt-0.5 text-gray-400" /><div><h3 className="font-semibold text-gray-900 dark:text-gray-100">Standalone server</h3><p className="mt-1 text-sm text-gray-500 dark:text-gray-400">This installation has no peer writer lease. Local encrypted snapshots remain visible below. HA setup and destructive failover controls intentionally stay in the protected SSH management interface.</p></div></div>
        </Card>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {[
              ["Protection", protectedState ? "Protected" : replication.state === "replicating" ? "Replicating" : "Needs attention"],
              ["Current primary", String(cluster.holder_node_id || "Unknown")],
              ["Recovery-point age", formatDuration(recoveryAge)],
              ["Automatic failover", cluster.automatic_failover ? "Enabled" : "Disabled"],
              ["Failover delay", `${formatDuration(cluster.failover_delay_seconds ?? 120)} (fixed)`],
            ].map(([label, value]) => <Card key={label} className="p-4"><p className="text-xs font-medium uppercase tracking-wide text-gray-400">{label}</p><p className="mt-1 text-base font-semibold text-gray-900 dark:text-gray-100">{value}</p></Card>)}
          </div>

          <Card className="overflow-hidden">
            <div className="border-b border-gray-200 px-5 py-4 dark:border-gray-700">
              <h3 className="font-semibold text-gray-900 dark:text-gray-100">Nodes and writer lease</h3>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-gray-500 dark:text-gray-400">
                <span>Generation <strong className="font-medium text-gray-700 dark:text-gray-200">{cluster.generation ?? "Unknown"}</strong></span>
                <span>Routing <strong className="font-medium text-gray-700 dark:text-gray-200">{cluster.routing_ready ? "ready" : "not ready"}</strong></span>
                <span>Witness <strong className="font-medium text-gray-700 dark:text-gray-200">{formatDuration(cluster.witness_age_seconds == null ? null : cluster.witness_age_seconds + elapsedSinceRefresh)} ago</strong></span>
                <span>Lease remaining <strong className="font-medium text-gray-700 dark:text-gray-200">{formatDuration(cluster.lease_remaining_seconds == null ? null : Math.max(0, cluster.lease_remaining_seconds - elapsedSinceRefresh))}</strong></span>
              </div>
            </div>
            {stableNodes.length ? (
              <div className="grid gap-4 p-4 md:grid-cols-2">
                {stableNodes.map((node, index) => {
                  const heartbeatAge = node.heartbeat_age_seconds == null
                    ? null : node.heartbeat_age_seconds + elapsedSinceRefresh;
                  const isLocal = node.node_id === cluster.local_node_id;
                  return (
                    <section
                      key={node.node_id}
                      aria-label={`Node ${index === 0 ? "A" : "B"}: ${node.node_id}`}
                      className={`rounded-xl border p-4 ${
                        node.healthy === false
                          ? "border-red-300 bg-red-50/60 dark:border-red-800 dark:bg-red-900/10"
                          : node.is_holder
                            ? "border-blue-300 bg-blue-50/60 dark:border-blue-700 dark:bg-blue-900/10"
                            : "border-gray-200 bg-gray-50/70 dark:border-gray-700 dark:bg-gray-800/50"
                      }`}
                    >
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="text-xs font-semibold uppercase tracking-wider text-gray-400">Node {index === 0 ? "A" : "B"}</p>
                          <div className="mt-1 flex min-w-0 items-center gap-2">
                            <Server size={18} className="shrink-0 text-gray-500" />
                            <h4 className="break-all font-semibold text-gray-900 dark:text-gray-100">{node.node_id}</h4>
                          </div>
                        </div>
                        <div className="flex flex-wrap justify-end gap-1.5">
                          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${node.is_holder ? "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-200" : "bg-gray-200 text-gray-700 dark:bg-gray-700 dark:text-gray-200"}`}>{node.is_holder ? "Primary" : "Standby"}</span>
                          <span className="rounded-full bg-white px-2 py-0.5 text-xs text-gray-600 shadow-sm dark:bg-gray-900 dark:text-gray-300">{isLocal ? "This VPS" : "Peer VPS"}</span>
                        </div>
                      </div>

                      <div className="mt-4 flex items-center gap-2 text-sm">
                        <span aria-hidden="true" className={`h-2.5 w-2.5 rounded-full ${node.healthy ? "bg-green-500" : "bg-red-500"}`} />
                        <span className={node.healthy ? "font-medium text-green-700 dark:text-green-300" : "font-medium text-red-700 dark:text-red-300"}>{node.healthy ? "Healthy" : "Unhealthy"}</span>
                        <span className="text-gray-500 dark:text-gray-400">· Heartbeat {formatDuration(heartbeatAge)} ago</span>
                      </div>

                      <div className={`mt-3 rounded-lg px-3 py-2 text-sm ${
                        node.smtp_ready && smtpConfigurationMatches
                          ? "bg-green-50 text-green-800 dark:bg-green-900/20 dark:text-green-200"
                          : "bg-amber-50 text-amber-800 dark:bg-amber-900/20 dark:text-amber-200"
                      }`}>
                        <p className="font-medium">
                          SMTP {node.smtp_ready ? "authenticated" : node.smtp_configured ? "degraded" : "not configured"}
                        </p>
                        <p className="mt-0.5 text-xs">
                          {node.smtp_error_code
                            ? `Check: ${node.smtp_error_code.replaceAll("_", " ")}`
                            : smtpConfigurationMatches
                              ? "Configuration matches the peer."
                              : "Configuration has not been verified against the peer."}
                          {node.smtp_checked_at ? ` · ${fmtDateTime(node.smtp_checked_at)}` : ""}
                        </p>
                      </div>

                      <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
                        <div><dt className="text-xs uppercase tracking-wide text-gray-400">Last heartbeat</dt><dd className="mt-0.5 text-gray-800 dark:text-gray-200">{node.last_heartbeat_at ? fmtDateTime(node.last_heartbeat_at) : "Unknown"}</dd></div>
                        <div><dt className="text-xs uppercase tracking-wide text-gray-400">Bundle generation</dt><dd className="mt-0.5 text-gray-800 dark:text-gray-200">{node.bundle_generation ?? "None"}</dd></div>
                        <div><dt className="text-xs uppercase tracking-wide text-gray-400">Bundle created</dt><dd className="mt-0.5 text-gray-800 dark:text-gray-200">{node.bundle_created_at ? fmtDateTime(node.bundle_created_at) : "None"}</dd></div>
                        <div><dt className="text-xs uppercase tracking-wide text-gray-400">Writer lease</dt><dd className="mt-0.5 text-gray-800 dark:text-gray-200">{node.is_holder ? "Held by this node" : "Not held"}</dd></div>
                        <div><dt className="text-xs uppercase tracking-wide text-gray-400">Protected changes</dt><dd className="mt-0.5 text-gray-800 dark:text-gray-200">{node.critical_pending ? "Replication pending" : "None pending"}</dd></div>
                      </dl>
                      <div className="mt-4 space-y-2 border-t border-gray-200 pt-3 text-xs dark:border-gray-700">
                        <p><span className="text-gray-400">Bundle ID</span><br /><code className="break-all text-gray-700 dark:text-gray-300">{node.bundle_id || "None"}</code></p>
                        <p><span className="text-gray-400">Release hash</span><br /><code className="break-all text-gray-700 dark:text-gray-300">{node.release_hash || "Unknown"}</code></p>
                      </div>
                    </section>
                  );
                })}
              </div>
            ) : <p className="px-5 py-4 text-sm text-gray-500">Node telemetry will appear after the updated witness receives both heartbeats.</p>}
          </Card>

          <Card className="p-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between"><div><h3 className="font-semibold text-gray-900 dark:text-gray-100">Peer replication</h3><p className="mt-1 text-sm text-gray-500 dark:text-gray-400">{replication.message || "No replication result has been recorded yet."}</p></div><span className={`text-sm font-medium ${replication.peer_reachable === false ? "text-red-600" : "text-green-700 dark:text-green-300"}`}>{replication.peer_reachable === true ? "Peer reachable" : replication.peer_reachable === false ? "Peer unavailable" : "Peer not checked"}</span></div>
            <div className="mt-4 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4"><p><span className="text-gray-400">Last verified copy</span><br />{replication.last_success_at ? fmtDateTime(replication.last_success_at) : "None"}</p><p><span className="text-gray-400">Job</span><br />{replication.job_state || "Idle"}</p><p><span className="text-gray-400">Peer compatibility</span><br />{replication.peer_compatible === true ? "Compatible" : replication.peer_compatible === false ? "Mismatch" : "Not checked"}</p><p><span className="text-gray-400">Last received here</span><br />{replication.last_received_at ? fmtDateTime(replication.last_received_at) : "None"}</p></div>
          </Card>
        </>
      )}

      <Card className="p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between"><div><h3 className="font-semibold text-gray-900 dark:text-gray-100">Replication frequency</h3><p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Maximum scheduled recovery-point interval; allowed range {intervalMeta?.min ?? 5}–{intervalMeta?.max ?? 1440} minutes.</p></div><div className="flex items-center gap-2"><input type="number" min={intervalMeta?.min ?? 5} max={intervalMeta?.max ?? 1440} value={interval} onChange={(event) => setIntervalValue(Number(event.target.value))} className="w-28 rounded-lg border border-gray-300 bg-white px-3 py-2 text-right text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100" /><span className="text-sm text-gray-500">minutes</span><Button size="sm" onClick={saveInterval} disabled={busy || !intervalMeta || interval === intervalMeta.value || interval < intervalMeta.min || interval > intervalMeta.max}>Save</Button></div></div>
      </Card>

      <Card className="p-5">
        <h3 className="font-semibold text-gray-900 dark:text-gray-100">Recovery snapshots</h3>
        <div className="mt-4 grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-4"><p><span className="text-gray-400">Storage policy</span><br />{recovery.storage_mode === "manual_portable" ? "Manual workstation export" : "SSH archive"}</p><p><span className="text-gray-400">Local snapshots</span><br />{String(recovery.local_snapshot_count ?? "Unknown")} ({String(recovery.deep_verified_count ?? "Unknown")} deep verified)</p><p><span className="text-gray-400">Latest database</span><br />{latestDatabase?.created_at ? fmtDateTime(String(latestDatabase.created_at)) : "None"}</p><p><span className="text-gray-400">Latest full</span><br />{latestFull?.created_at ? fmtDateTime(String(latestFull.created_at)) : "None"}</p></div>
        <div className={`mt-4 rounded-lg px-3 py-2 text-sm ${recovery.state === "protected" ? "bg-green-50 text-green-800 dark:bg-green-900/20 dark:text-green-200" : "bg-amber-50 text-amber-800 dark:bg-amber-900/20 dark:text-amber-200"}`}>{recovery.state === "protected" ? recovery.storage_mode === "manual_portable" ? `Portable copy confirmed${portable?.confirmed_at ? ` ${fmtDateTime(String(portable.confirmed_at))}` : ""}.` : "The latest recovery point has a hash-verified SSH archive copy." : recovery.storage_mode === "manual_portable" ? "No current operator-confirmed workstation export is recorded. Use mp-opt over SSH to create, deep-verify and export one." : "No latest recovery point has a hash-verified SSH archive copy. Review snapshots in mp-opt over SSH."}</div>
      </Card>

      <section className="space-y-3" aria-labelledby="ha-incident-history-title">
        <div>
          <h3 id="ha-incident-history-title" className="font-semibold text-gray-900 dark:text-gray-100">Incident history and live-service downtime</h3>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Related witness events are grouped into operational episodes. Statistics cover the retained {incidentSummary.retention_days}-day history.
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {downtimeSummaryCards
            .filter(({ key, aggregate }) => key !== "primary_outage" || aggregate.incident_count > 0)
            .map(({ key, label, aggregate }) => (
              <Card key={key} className="p-4">
                <p className="text-xs font-medium uppercase tracking-wide text-gray-400">{label}</p>
                <p className="mt-2 text-xl font-semibold text-gray-900 dark:text-gray-100">
                  {formatDuration(aggregate.total_downtime_seconds)}
                </p>
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Total live downtime</p>
                <dl className="mt-3 grid grid-cols-2 gap-2 border-t border-gray-200 pt-3 text-sm dark:border-gray-700">
                  <div><dt className="text-xs text-gray-400">Average</dt><dd className="mt-0.5 font-medium text-gray-800 dark:text-gray-200">{aggregate.incident_count ? formatDuration(aggregate.average_downtime_seconds) : "No incidents"}</dd></div>
                  <div><dt className="text-xs text-gray-400">Incidents</dt><dd className="mt-0.5 font-medium text-gray-800 dark:text-gray-200">{aggregate.incident_count}{aggregate.active_count ? ` · ${aggregate.active_count} ongoing` : ""}</dd></div>
                </dl>
              </Card>
            ))}
        </div>

        <Card className="overflow-hidden">
          <div className="border-b border-gray-200 px-5 py-4 dark:border-gray-700">
            <h3 className="font-semibold text-gray-900 dark:text-gray-100">Grouped incidents and transitions</h3>
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Newest first; expand an episode to see the same checkpoints shown during the live incident.</p>
          </div>
          {incidentGroups.length === 0 ? (
            <p className="px-5 py-5 text-sm text-gray-500">No incidents have been recorded by the witness.</p>
          ) : (
            <div className="divide-y divide-gray-200 dark:divide-gray-700">
              {incidentGroups.map((group) => {
                const isExpanded = group.state !== "resolved" || expandedIncidentIds.includes(group.id);
                const panelId = `ha-incident-${group.id}`;
                const location = group.from_node_id
                  ? `${group.from_node_id} → ${group.to_node_id || "standby"}`
                  : group.node_id || "Cluster";
                const downtime = !group.service_impact
                  ? "No live downtime"
                  : group.service_restored_at
                    ? `Restored in ${formatDuration(group.downtime_seconds)}`
                    : `Ongoing · ${formatDuration(group.downtime_seconds)}`;
                const checkpoints = buildHATimelineCheckpoints(group, now, true);
                return (
                  <div key={group.id}>
                    <button
                      type="button"
                      className="grid w-full gap-3 px-5 py-4 text-left transition-colors hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 dark:hover:bg-gray-800/60 sm:grid-cols-[minmax(0,1fr)_auto_auto_auto] sm:items-center"
                      aria-expanded={isExpanded}
                      aria-controls={panelId}
                      onClick={() => toggleIncident(group.id)}
                    >
                      <span className="min-w-0">
                        <span className="block font-medium text-gray-900 dark:text-gray-100">{incidentCategoryLabel(group.category)}</span>
                        <span className="mt-0.5 block text-xs text-gray-500 dark:text-gray-400">{location} · {fmtDateTime(group.started_at)}{group.generation ? ` · generation ${group.generation}` : ""}</span>
                      </span>
                      <span className={`w-fit rounded-full px-2.5 py-1 text-xs font-medium ${group.state === "resolved" ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-200" : group.service_restored_at ? "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-200" : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-200"}`}>{incidentStateLabel(group)}</span>
                      <span className="text-sm font-medium text-gray-700 dark:text-gray-200">{downtime}</span>
                      <ChevronDown size={18} className={`text-gray-400 transition-transform ${isExpanded ? "rotate-180" : ""}`} />
                    </button>
                    {isExpanded && (
                      <div id={panelId} className="border-t border-gray-100 bg-gray-50/60 dark:border-gray-800 dark:bg-gray-900/30">
                        <div className="grid gap-3 border-b border-gray-200 px-5 py-3 text-xs text-gray-500 dark:border-gray-700 dark:text-gray-400 sm:grid-cols-3">
                          <p><span className="block uppercase tracking-wide text-gray-400">Live-service impact</span><strong className="mt-0.5 block font-medium text-gray-700 dark:text-gray-200">{group.service_impact ? downtime : "None; primary remained available"}</strong></p>
                          <p><span className="block uppercase tracking-wide text-gray-400">Episode state</span><strong className="mt-0.5 block font-medium text-gray-700 dark:text-gray-200">{incidentStateLabel(group)}</strong></p>
                          <p><span className="block uppercase tracking-wide text-gray-400">Witness records</span><strong className="mt-0.5 block font-medium text-gray-700 dark:text-gray-200">{group.event_count || 1} related event{(group.event_count || 1) === 1 ? "" : "s"}</strong></p>
                        </div>
                        <HATimeline checkpoints={checkpoints} />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      </section>

      <p className="text-xs text-gray-500 dark:text-gray-400">For safety, enabling automatic failover, planned handoff, restoration and recovery-key operations remain available only in <code className="rounded bg-gray-100 px-1 py-0.5 dark:bg-gray-800">mp-opt</code> over SSH.</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Security Settings Tab
// ---------------------------------------------------------------------------
interface SettingMeta {
  value: number;
  default: number;
  label: string;
  unit: string;
  min: number;
  max: number;
}

const SETTING_DESCRIPTIONS: Record<string, string> = {
  session_ttl_hours:
    "Maximum duration a regular user session stays valid. After this period the user must log in again, regardless of activity.",
  session_ttl_hours_admin:
    "Maximum duration an admin session stays valid. Kept shorter than regular sessions for additional security.",
  session_inactivity_minutes:
    "Time of inactivity after which a session is automatically invalidated. The user will need to log in again.",
  offline_access_ttl_hours:
    "How long a successfully signed-in user may view a cached masterplan while offline. Access also expires at the end of the local day. Cached data is read-only and requires the calendar to have loaded online first.",
  reauth_window_minutes:
    "After confirming identity with a passkey, sensitive actions (e.g. changing security settings) are permitted for this duration without another prompt.",
  activation_link_expiry_hours:
    "How long a newly generated activation link remains usable. Changes apply only to links generated afterwards; existing links keep their original expiry.",
  retention_revoked_sessions_days:
    "How many days records of manually revoked sessions are kept before being permanently deleted during cleanup.",
  retention_expired_sessions_days:
    "How many days records of naturally expired sessions are kept before being permanently deleted during cleanup.",
  retention_used_activation_links_days:
    "How many days records of already-used activation links are kept before being permanently deleted during cleanup.",
  audit_log_retention_days:
    "How many days audit log entries are retained before being permanently deleted during cleanup.",
  event_purge_grace_days:
    "Full days retained after an event ends before a signed whole-event deletion case is queued. The saved deadline is shown on each event and is not silently moved by later setting changes.",
  secret_max_age_days:
    "Maximum age of the publish secret before a rotation warning is shown. Set to 0 to disable age checks.",
  max_snapshots_per_event:
    "Maximum number of publish history snapshots stored per event. Oldest unfrozen snapshots are pruned when the limit is exceeded.",
  challenge_ttl_minutes:
    "How long a passkey authentication challenge remains valid. Shorter values are more secure but give users less time to complete authentication.",
  exchange_code_ttl_seconds:
    "How long a one-time exchange code is valid after a successful passkey authentication. This is the window between passkey verification and session creation.",
  reauth_challenge_ttl_minutes:
    "How long a re-authentication passkey challenge remains valid for sensitive admin operations.",
  passkey_requests_per_minute:
    "Maximum passkey requests accepted per minute. Public sign-in is limited per client address, while registration and re-authentication are limited per activation or account session.",
  announcements_per_event_limit:
    "Maximum number of announcements returned per event. Older announcements beyond this limit are not shown.",
  masterplan_pushes_per_minute:
    "Maximum number of masterplan publish requests accepted from one desktop client each minute.",
  public_schedule_pushes_per_minute:
    "Maximum number of Public Schedule publish requests accepted from one desktop client each minute.",
  ha_replication_interval_minutes:
    "How often the current primary sends a complete encrypted point-in-time copy to its peer. The previous verified copy remains usable if a transfer fails.",
};

const SECTION_DESCRIPTIONS: Record<string, string> = {
  "Session Lifetimes":
    "Control how long user sessions remain active and when they expire due to inactivity.",
  "Offline Access":
    "Control the read-only cached masterplan window for users who lose connection after signing in.",
  Authentication:
    "Configure account verification windows and activation link behaviour.",
  Passkeys:
    "Configure passkey ceremony lifetimes and throughput for sign-in, registration, and re-authentication.",
  "Data Retention":
    "Set how long expired or revoked records are kept before automatic cleanup removes them.",
  "Desktop Publishing":
    "Control how frequently one desktop client may push masterplan and Public Schedule data to this server.",
  "High Availability":
    "Control the maximum planned data-loss window for the provider-neutral peer copy.",
  Limits: "Configure storage and query limits for snapshots and announcements.",
};

function SecurityTab() {
  const [settings, setSettings] = useState<Record<string, SettingMeta> | null>(
    null,
  );
  const [draft, setDraft] = useState<Record<string, number>>({});
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);
  const [expandedInfo, setExpandedInfo] = useState<Record<string, boolean>>({});
  const [mailSettings, setMailSettings] =
    useState<ActivationDeliverySettings | null>(null);
  const [testRecipient, setTestRecipient] = useState("");
  const [mailActionBusy, setMailActionBusy] = useState(false);
  const [confirmInvalidateAll, setConfirmInvalidateAll] = useState(false);

  const toggleInfo = (key: string) =>
    setExpandedInfo((prev) => ({ ...prev, [key]: !prev[key] }));

  const fetchSettings = useCallback(async () => {
    try {
      const res = await apiFetch("/api/v1/admin/settings");
      if (res.ok) {
        const data: Record<string, SettingMeta> = await res.json();
        setSettings(data);
        const initial: Record<string, number> = {};
        for (const [key, meta] of Object.entries(data)) {
          initial[key] = meta.value;
        }
        setDraft(initial);
      }
      const mailResponse = await apiFetch(
        "/api/v1/admin/activation-delivery/settings",
      );
      if (mailResponse.ok) setMailSettings(await mailResponse.json());
    } catch {
      // silent
    }
  }, []);

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  const hasChanges =
    settings && Object.keys(draft).some((k) => draft[k] !== settings[k]?.value);

  const handleSave = async () => {
    if (!settings) return;
    const changed: Record<string, number> = {};
    for (const [key, val] of Object.entries(draft)) {
      if (val !== settings[key]?.value) {
        changed[key] = val;
      }
    }
    if (Object.keys(changed).length === 0) return;

    setSaving(true);
    setStatus(null);
    try {
      const res = await withReauth(() =>
        apiFetch("/api/v1/admin/settings", {
          method: "PUT",
          body: JSON.stringify({ settings: changed }),
        }),
      );
      if (res.ok) {
        setStatus({ type: "success", message: "Settings saved." });
        await fetchSettings();
      } else {
        const err = await res.json().catch(() => ({}));
        setStatus({
          type: "error",
          message: err.detail || "Failed to save settings.",
        });
      }
    } catch (e: unknown) {
      setStatus({
        type: "error",
        message:
          e instanceof Error ? e.message : "Re-authentication cancelled.",
      });
    } finally {
      setSaving(false);
    }
  };

  const handleReset = (key: string) => {
    if (!settings) return;
    setDraft((prev) => ({ ...prev, [key]: settings[key].default }));
  };

  const handleTestEmail = async () => {
    if (!testRecipient.trim()) return;
    setMailActionBusy(true);
    setStatus(null);
    try {
      const response = await withReauth(() =>
        apiFetch("/api/v1/admin/settings/email/test", {
          method: "POST",
          body: JSON.stringify({ recipient: testRecipient.trim() }),
        }),
      );
      const data = await response.json().catch(() => ({}));
      setStatus({
        type: response.ok ? "success" : "error",
        message: data.message || data.detail || "Test email could not be sent.",
      });
    } catch {
      setStatus({ type: "error", message: "Re-authentication cancelled." });
    } finally {
      setMailActionBusy(false);
    }
  };

  const handleInvalidateAllLinks = async () => {
    setMailActionBusy(true);
    setStatus(null);
    try {
      const response = await withReauth(() =>
        apiFetch("/api/v1/admin/activation-links/invalidate-all", {
          method: "POST",
          body: JSON.stringify({ confirm: true }),
        }),
      );
      const data = await response.json().catch(() => ({}));
      setStatus({
        type: response.ok ? "success" : "error",
        message: response.ok
          ? `${data.invalidated_count} active link${data.invalidated_count === 1 ? "" : "s"} invalidated.`
          : data.detail || "Activation links could not be invalidated.",
      });
      if (response.ok) setConfirmInvalidateAll(false);
    } catch {
      setStatus({ type: "error", message: "Re-authentication cancelled." });
    } finally {
      setMailActionBusy(false);
    }
  };

  if (!settings)
    return (
      <div className="text-center py-12 text-gray-500 dark:text-gray-400">
        Loading settings...
      </div>
    );

  const groups: { title: string; keys: string[] }[] = [
    {
      title: "Session Lifetimes",
      keys: [
        "session_ttl_hours",
        "session_ttl_hours_admin",
        "session_inactivity_minutes",
      ],
    },
    {
      title: "Offline Access",
      keys: ["offline_access_ttl_hours"],
    },
    {
      title: "Authentication",
      keys: [
        "reauth_window_minutes",
        "activation_link_expiry_hours",
      ],
    },
    {
      title: "Passkeys",
      keys: [
        "challenge_ttl_minutes",
        "exchange_code_ttl_seconds",
        "reauth_challenge_ttl_minutes",
        "passkey_requests_per_minute",
      ],
    },
    {
      title: "Data Retention",
      keys: [
        "retention_revoked_sessions_days",
        "retention_expired_sessions_days",
        "retention_used_activation_links_days",
        "audit_log_retention_days",
        "event_purge_grace_days",
        "secret_max_age_days",
      ],
    },
    {
      title: "Desktop Publishing",
      keys: [
        "masterplan_pushes_per_minute",
        "public_schedule_pushes_per_minute",
      ],
    },
    {
      title: "Limits",
      keys: ["max_snapshots_per_event", "announcements_per_event_limit"],
    },
  ];

  return (
    <div className="space-y-5">
      {/* Header */}
      <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
        Security Settings
      </h2>

      <p className="text-sm text-gray-500 dark:text-gray-400">
        Configure runtime security parameters. Passkey re-authentication is
        required to save changes.
      </p>

      <Card className="p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
              Activation email
            </h3>
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
              SMTP credentials are deployment-only and are never shown here.
            </p>
          </div>
          <span className={`inline-flex items-center gap-1.5 text-sm font-medium ${mailSettings?.configured ? "text-green-700 dark:text-green-300" : "text-gray-500 dark:text-gray-400"}`}>
            {mailSettings?.configured ? <Check size={15} /> : <AlertTriangle size={15} />}
            {mailSettings?.configured ? "Ready" : "Not configured"}
          </span>
        </div>
        {mailSettings?.configured && (
          <div className="mt-4 grid gap-1 text-sm text-gray-600 dark:text-gray-300 sm:grid-cols-2">
            <p>Sender: {mailSettings.from_name} &lt;{mailSettings.from_email}&gt;</p>
            <p>Connection: {mailSettings.security === "tls" ? "TLS" : "STARTTLS"}</p>
          </div>
        )}
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <input
            type="email"
            value={testRecipient}
            onChange={(event) => setTestRecipient(event.target.value)}
            placeholder="Test recipient email"
            className="min-h-11 flex-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 sm:min-h-0"
          />
          <Button
            variant="outline"
            onClick={handleTestEmail}
            disabled={!mailSettings?.configured || !testRecipient.trim() || mailActionBusy}
          >
            <Send size={14} /> Send test
          </Button>
        </div>
        <div className="mt-5 border-t border-gray-200 pt-4 dark:border-gray-700">
          {!confirmInvalidateAll ? (
            <button
              type="button"
              onClick={() => setConfirmInvalidateAll(true)}
              className="text-sm font-medium text-red-600 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300"
            >
              Invalidate all active activation links
            </button>
          ) : (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 dark:border-red-800 dark:bg-red-900/20">
              <p className="text-sm text-red-800 dark:text-red-200">
                Every current activation and passkey-reset link will stop working. Existing users remain active.
              </p>
              <div className="mt-3 flex gap-2">
                <Button variant="danger" size="sm" onClick={handleInvalidateAllLinks} disabled={mailActionBusy}>
                  Invalidate all links
                </Button>
                <Button variant="outline" size="sm" onClick={() => setConfirmInvalidateAll(false)}>
                  Cancel
                </Button>
              </div>
            </div>
          )}
        </div>
      </Card>

      {groups.map((group) => (
        <Card key={group.title} className="p-5">
          <div className="mb-5">
            <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
              {group.title}
            </h3>
            {SECTION_DESCRIPTIONS[group.title] && (
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                {SECTION_DESCRIPTIONS[group.title]}
              </p>
            )}
          </div>
          <div className="space-y-5">
            {group.keys.map((key) => {
              const meta = settings[key];
              if (!meta) return null;
              const isModified = draft[key] !== meta.default;
              const isChanged = draft[key] !== meta.value;
              const infoOpen = expandedInfo[key] ?? false;
              return (
                <div key={key}>
                  <div className="flex items-start gap-6 flex-wrap sm:flex-nowrap">
                    <div className="flex-1 min-w-[240px]">
                      <div className="flex items-center gap-1.5">
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300">
                          {meta.label}
                        </label>
                        {SETTING_DESCRIPTIONS[key] && (
                          <button
                            type="button"
                            onClick={() => toggleInfo(key)}
                            className={`p-0.5 rounded-full transition-colors ${
                              infoOpen
                                ? "text-blue-500 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/30"
                                : "text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
                            }`}
                            aria-label={`Info about ${meta.label}`}
                          >
                            <Info size={14} />
                          </button>
                        )}
                      </div>
                      <span className="text-xs text-gray-400 dark:text-gray-500 mt-0.5 block">
                        Default: {meta.default} {meta.unit} &middot; Range:{" "}
                        {meta.min} - {meta.max}
                      </span>
                    </div>
                    <div className="flex items-center gap-2.5 shrink-0">
                      <input
                        type="number"
                        min={meta.min}
                        max={meta.max}
                        value={draft[key] ?? meta.value}
                        onChange={(e) => {
                          const v = parseInt(e.target.value, 10);
                          if (!isNaN(v)) {
                            setDraft((prev) => ({ ...prev, [key]: v }));
                          }
                        }}
                        className={`w-28 px-3 py-2 border rounded-lg text-sm text-right bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent ${
                          isChanged
                            ? "border-blue-400 dark:border-blue-500 ring-1 ring-blue-200 dark:ring-blue-800"
                            : "border-gray-300 dark:border-gray-600"
                        }`}
                      />
                      <span className="text-xs text-gray-500 dark:text-gray-400 w-16">
                        {meta.unit}
                      </span>
                      <button
                        onClick={() => handleReset(key)}
                        className={`p-1.5 rounded-lg transition-colors ${
                          isModified
                            ? "text-gray-500 hover:text-blue-500 hover:bg-blue-50 dark:hover:bg-blue-900/20"
                            : "text-gray-200 dark:text-gray-700 cursor-default"
                        }`}
                        title="Reset to default"
                        disabled={!isModified}
                      >
                        <RotateCcw size={14} />
                      </button>
                    </div>
                  </div>
                  {infoOpen && SETTING_DESCRIPTIONS[key] && (
                    <p className="mt-2 text-sm text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2">
                      {SETTING_DESCRIPTIONS[key]}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      ))}

      {/* Save bar */}
      <div className="flex items-center gap-3 pt-1">
        <Button onClick={handleSave} disabled={saving || !hasChanges}>
          <Shield size={15} />
          {saving ? "Saving..." : "Save Security Settings"}
        </Button>
        {status && (
          <span
            className={`text-sm ${
              status.type === "success"
                ? "text-green-600 dark:text-green-400"
                : "text-red-600 dark:text-red-400"
            }`}
          >
            {status.message}
          </span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Audit Log Tab
// ---------------------------------------------------------------------------
interface AuditEntry {
  id: number;
  timestamp: string;
  user_id: number | null;
  actor_ref: string | null;
  action: string;
  resource_type: string | null;
  resource_id: number | null;
  detail: string | null;
  outcome: string;
}

function AuditTab() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [actionFilter, setActionFilter] = useState("");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const perPage = 50;

  const fetchAuditLog = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: String(page),
        per_page: String(perPage),
      });
      if (actionFilter) params.set("action", actionFilter);
      const res = await apiFetch(`/api/v1/admin/audit-log?${params}`);
      if (res.ok) {
        const data = await res.json();
        setEntries(data.entries);
        setTotal(data.total);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [page, actionFilter]);

  useEffect(() => {
    fetchAuditLog();
  }, [fetchAuditLog]);

  const totalPages = Math.max(1, Math.ceil(total / perPage));

  const outcomeBadge = (outcome: string) => {
    switch (outcome) {
      case "success":
        return "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400";
      case "denied":
        return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400";
      case "error":
        return "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400";
      default:
        return "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-400";
    }
  };

  const formatDetail = (detail: string | null): string | null => {
    if (!detail) return null;
    try {
      return JSON.stringify(JSON.parse(detail), null, 2);
    } catch {
      return detail;
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
          Audit Log
        </h2>
        <Button size="sm" variant="outline" onClick={fetchAuditLog}>
          <RefreshCw size={14} className="mr-1" />
          Refresh
        </Button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2">
        <Input
          placeholder="Filter by action (e.g. auth.login)..."
          value={actionFilter}
          onChange={(e) => {
            setActionFilter(e.target.value);
            setPage(1);
          }}
          className="max-w-xs text-sm"
        />
        {actionFilter && (
          <button
            onClick={() => {
              setActionFilter("");
              setPage(1);
            }}
            className="p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors"
          >
            <X size={14} />
          </button>
        )}
        <span className="text-xs text-gray-500 dark:text-gray-400 ml-auto">
          {total} {total === 1 ? "entry" : "entries"}
        </span>
      </div>

      {/* Entries */}
      {loading ? (
        <p className="text-gray-500 dark:text-gray-400 text-sm py-8 text-center">
          Loading...
        </p>
      ) : entries.length === 0 ? (
        <p className="text-gray-500 dark:text-gray-400 text-sm py-8 text-center">
          No audit log entries found.
        </p>
      ) : (
        <div className="space-y-2">
          {entries.map((entry) => {
            const detail = formatDetail(entry.detail);
            const isExpanded = expandedId === entry.id;
            return (
              <Card key={entry.id} className="p-0 overflow-hidden">
                <button
                  type="button"
                  onClick={() => setExpandedId(isExpanded ? null : entry.id)}
                  className="w-full text-left px-4 py-3 flex items-start gap-3 hover:bg-gray-50 dark:hover:bg-gray-800/30 transition-colors"
                >
                  {/* Left: action + outcome badge */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono text-sm font-medium text-gray-900 dark:text-gray-100">
                        {entry.action}
                      </span>
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${outcomeBadge(entry.outcome)}`}
                      >
                        {entry.outcome}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 mt-1 text-xs text-gray-500 dark:text-gray-400">
                      <span>{fmtDateTime(entry.timestamp)}</span>
                      <span>
                        {entry.actor_ref ? `actor ${entry.actor_ref.slice(0, 8)}` : (
                          <span className="italic">system</span>
                        )}
                      </span>
                      {entry.resource_type ? (
                        <span>
                          {entry.resource_type}
                          {entry.resource_id != null && (
                            <span className="text-gray-400 dark:text-gray-500">
                              #{entry.resource_id}
                            </span>
                          )}
                        </span>
                      ) : (
                        <span className="text-gray-300 dark:text-gray-600">
                          -
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Expand chevron */}
                  {detail && (
                    <ChevronDown
                      size={16}
                      className={`shrink-0 text-gray-400 transition-transform mt-1 ${isExpanded ? "rotate-180" : ""}`}
                    />
                  )}
                </button>

                {/* Expanded detail */}
                {isExpanded && detail && (
                  <div className="px-4 py-3 bg-gray-50 dark:bg-gray-800/50 border-t border-gray-200 dark:border-gray-700">
                    <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
                      Detail
                    </p>
                    <pre className="text-xs text-gray-600 dark:text-gray-300 whitespace-pre-wrap font-mono overflow-x-auto">
                      {detail}
                    </pre>
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between pt-2">
          <Button
            size="sm"
            variant="outline"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            <ChevronLeft size={14} className="mr-1" />
            Previous
          </Button>
          <span className="text-xs text-gray-500 dark:text-gray-400">
            Page {page} of {totalPages}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
            <ChevronRight size={14} className="ml-1" />
          </Button>
        </div>
      )}
    </div>
  );
}

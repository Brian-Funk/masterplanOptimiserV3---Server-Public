"use client";

import Link from "next/link";
import { ArrowRightLeft, CalendarDays, RefreshCw, ServerOff, WifiOff } from "lucide-react";
import { useServiceAvailability, type ServiceState } from "@/contexts/ServiceAvailabilityContext";
import type { OfflineAccessMarker } from "@/lib/offlineAccess";
import { formatOfflineCachedAt, isOfflineAccessValid } from "@/lib/offlineAccess";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";

const TRANSITION_COPY = {
  title: "Service transition in progress",
  body: "Live access will return automatically. You can continue viewing the saved schedule.",
};

const COPY: Record<ServiceState, { title: string; body: string }> = {
  checking: { title: "Checking service status", body: "This normally takes only a moment." },
  ready: { title: "Service available", body: "Live schedules and account access are available." },
  device_offline: { title: "You are offline", body: "Reconnect for live updates. A saved schedule can still be viewed on this device." },
  service_unreachable: { title: "Service temporarily unreachable", body: "The service cannot be reached. It will retry automatically." },
  planned_handoff: TRANSITION_COPY,
  failover_wait: TRANSITION_COPY,
  automatic_failover_disabled: { title: "Service temporarily unavailable", body: "Live access is paused safely. You can continue viewing the saved schedule." },
  promoting: TRANSITION_COPY,
  routing: TRANSITION_COPY,
  control_unavailable: { title: "Ownership cannot be verified", body: "Live access is paused safely until the HA control service is available." },
  standby_shell: TRANSITION_COPY,
};

/** Full-page-safe availability message shared by login, calendar and public links. */
export function ServiceStatusPanel({
  offlineAccess = null,
  offlineAccessExpired = false,
  savedScheduleAvailable,
}: {
  offlineAccess?: OfflineAccessMarker | null;
  offlineAccessExpired?: boolean;
  savedScheduleAvailable?: boolean;
}) {
  const { state, refresh } = useServiceAvailability();
  const copy = COPY[state];
  const canUseSaved = Boolean(
    savedScheduleAvailable !== false &&
    offlineAccess?.event_id &&
    isOfflineAccessValid(offlineAccess),
  );
  const body = state === "device_offline" && savedScheduleAvailable === false
    ? "Reconnect for live schedule access. No saved schedule is available on this device."
    : copy.body;
  const Icon = state === "device_offline" ? WifiOff
    : ["planned_handoff", "failover_wait", "promoting", "routing"].includes(state)
      ? ArrowRightLeft : ServerOff;

  return (
    <Card className="w-full max-w-lg p-6 text-center sm:p-8" role="status" aria-live="polite">
      <Icon className="mx-auto h-9 w-9 text-amber-600 dark:text-amber-300" aria-hidden="true" />
      <h1 className="mt-4 text-xl font-semibold text-gray-900 dark:text-gray-100">{copy.title}</h1>
      <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">{body}</p>
      {offlineAccessExpired && (
        <p className="mt-3 text-sm text-amber-700 dark:text-amber-300">
          Saved-schedule access has expired. Reconnect and sign in again.
        </p>
      )}
      <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:justify-center">
        {canUseSaved && offlineAccess?.event_id && (
          <Link
            href={`/calendar?event=${offlineAccess.event_id}&mode=cached`}
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            <CalendarDays size={17} /> View saved schedule
          </Link>
        )}
        <Button variant="outline" onClick={() => void refresh()}>
          <RefreshCw size={16} className="mr-2" /> Check again
        </Button>
      </div>
      {canUseSaved && offlineAccess && (
        <p className="mt-3 text-xs text-gray-500 dark:text-gray-400">
          Saved {formatOfflineCachedAt(offlineAccess) ?? "on this device"}; read-only.
        </p>
      )}
    </Card>
  );
}

/** Compact transition/offline banner for an already-open cached schedule. */
export function ServiceStatusBanner({ cachedAt }: { cachedAt: string | null }) {
  const { state } = useServiceAvailability();
  const copy = COPY[state];
  if (state === "ready") return null;
  return (
    <div className="mx-auto mb-4 w-[calc(100%_-_2rem)] rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200 sm:w-full sm:px-4 sm:py-3 sm:text-sm" role="status">
      <p className="font-medium">{copy.title}</p>
      <p className="mt-1">{copy.body}</p>
      <p className="mt-1 text-xs">{cachedAt ? `Showing the read-only schedule saved at ${cachedAt}.` : "Showing a read-only saved schedule."}</p>
    </div>
  );
}

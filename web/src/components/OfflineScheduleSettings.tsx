"use client";

import { useEffect, useState } from "react";

import { useAuth } from "@/contexts/AuthContext";
import { apiFetch } from "@/lib/api";
import {
  clearOfflineCalendarCacheForUser,
  offlineCalendarStorageEnabled,
  setOfflineCalendarStorageEnabled,
  storeOfflineCalendarPayload,
} from "@/lib/offlineCalendarCache";
import {
  buildOfflineAccessForCalendar,
  clearOfflineAccessMarker,
  commitOfflineAccessMarker,
} from "@/lib/offlineAccess";
import { Button } from "@/components/ui/Button";

/** Phone-only control for the optional, bounded IndexedDB schedule copy. */
export function OfflineScheduleSettings({ eventId }: { eventId: number }) {
  const { user } = useAuth();
  const [enabled, setEnabled] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setEnabled(user ? offlineCalendarStorageEnabled(user.id) : false);
  }, [user]);

  if (!user) return null;

  const toggle = async () => {
    setWorking(true);
    setError("");
    try {
      if (enabled) {
        await clearOfflineCalendarCacheForUser(user.id);
        setOfflineCalendarStorageEnabled(user.id, false);
        clearOfflineAccessMarker();
        setEnabled(false);
        return;
      }

      setOfflineCalendarStorageEnabled(user.id, true);
      try {
        const response = await apiFetch(`/api/v1/calendar/${eventId}/offline`, {
          cache: "no-store",
        });
        if (!response.ok) throw new Error("The offline calendar copy could not be downloaded.");
        const payload: unknown = await response.json();
        const cachedAt = new Date().toISOString();
        const marker = buildOfflineAccessForCalendar(user, eventId, cachedAt);
        const stored = await storeOfflineCalendarPayload(
          user.id,
          eventId,
          payload,
          cachedAt,
          marker.valid_until,
        );
        if (!stored) throw new Error("The offline schedule could not be saved on this device.");
        commitOfflineAccessMarker(marker);
        setEnabled(true);
      } catch (storageError) {
        setOfflineCalendarStorageEnabled(user.id, false);
        throw storageError;
      }
    } catch (storageError) {
      setError(
        storageError instanceof Error
          ? storageError.message
          : "The offline schedule could not be changed.",
      );
    } finally {
      setWorking(false);
    }
  };

  return (
    <section id="offline" className="space-y-4 rounded-2xl border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-800 md:hidden">
      <div>
        <h2 className="font-semibold text-gray-900 dark:text-gray-100">Offline schedule on this device</h2>
        <p className="mt-1 text-sm leading-6 text-gray-600 dark:text-gray-300">
          Optional. Keeps the schedule, at most your linked participant identity and your own published unavailability until the server-bounded expiry, logout or successful removal.
        </p>
        <a className="mt-2 inline-block text-sm font-medium text-blue-700 underline dark:text-blue-300" href="/privacy">Privacy details</a>
      </div>
      {error && (
        <p role="alert" className="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-100">
          {error} No offline copy is being claimed unless storage completed successfully.
        </p>
      )}
      <Button fullWidth variant={enabled ? "danger" : "primary"} disabled={working} onClick={() => void toggle()}>
        {working ? "Please wait…" : enabled ? "Remove offline copy" : "Enable offline copy"}
      </Button>
    </section>
  );
}

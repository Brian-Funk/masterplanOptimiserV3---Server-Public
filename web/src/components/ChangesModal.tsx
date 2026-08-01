"use client";

import React from "react";
import { X, Plus, Minus, RefreshCw } from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Compact task summary used in change notifications. */
export interface TaskSummary {
  name: string;
  start: string;
  end: string;
  location?: string;
}

/** Before and after value for one changed task field. */
export interface FieldChange {
  old: string | null;
  new: string | null;
}

/** Task with one or more changed fields. */
export interface ModifiedTask {
  name: string;
  changes: Record<string, FieldChange>;
}

/** Structured change set produced by a publish or republish operation. */
export interface ChangeRecord {
  type: "initial" | "republish";
  summary: string;
  added: TaskSummary[];
  removed: TaskSummary[];
  modified: ModifiedTask[];
}

/** Pending schedule change summary shown to the affected user. */
export interface PendingChange {
  id: number;
  changes: ChangeRecord;
  created_at: string;
}

/** Props for `ChangesModal`. */
export interface ChangesModalProps {
  changes: PendingChange[];
  onDismiss: () => void;
  dayAliases?: Record<string, string> | null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const FIELD_LABELS: Record<string, string> = {
  name: "Name",
  start: "Start",
  end: "End",
  location_name: "Location",
  location_address: "Address",
  attendees: "Attendees",
};

function formatTime(iso: string | null): string {
  if (!iso) return "-";
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

function isoToDateKey(iso: string): string {
  const d = new Date(iso);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function getScheduleDate(changes: PendingChange[]): string | null {
  for (const c of changes) {
    for (const t of c.changes.added || []) {
      if (t.start) return t.start;
    }
    for (const t of c.changes.removed || []) {
      if (t.start) return t.start;
    }
    for (const t of c.changes.modified || []) {
      const s = t.changes.start;
      if (s?.new) return s.new;
      if (s?.old) return s.old;
    }
  }
  return null;
}

function isTimeField(field: string): boolean {
  return field === "start" || field === "end";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
/**
 * Modal that explains newly published or republished schedule changes.
 */
export function ChangesModal({
  changes,
  onDismiss,
  dayAliases,
}: ChangesModalProps) {
  if (changes.length === 0) return null;

  // Merge all change records for display
  const allAdded: TaskSummary[] = [];
  const allRemoved: TaskSummary[] = [];
  const allModified: ModifiedTask[] = [];
  let isInitial = false;

  for (const c of changes) {
    if (c.changes.type === "initial") isInitial = true;
    allAdded.push(...(c.changes.added || []));
    allRemoved.push(...(c.changes.removed || []));
    allModified.push(...(c.changes.modified || []));
  }

  const scheduleDate = getScheduleDate(changes);
  const dateKey = scheduleDate ? isoToDateKey(scheduleDate) : null;
  const alias = dateKey && dayAliases ? dayAliases[dateKey] : null;

  let title: string;
  if (isInitial) {
    title = "Your Schedule is Ready";
  } else if (scheduleDate) {
    title = `Schedule updated for ${formatDate(scheduleDate)}${alias ? ` (${alias})` : ""}`;
  } else {
    title = "Schedule Updated";
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onDismiss}
    >
      <div
        className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl max-w-md w-full mx-4 overflow-hidden max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
            {title}
          </h2>
          <button
            onClick={onDismiss}
            className="p-1 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto px-5 py-4 space-y-4 flex-1">
          {/* Summary line */}
          {changes.length === 1 && (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              {changes[0].changes.summary}
            </p>
          )}
          {changes.length > 1 && (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              {changes.length} updates since your last visit
            </p>
          )}

          {/* New tasks */}
          {allAdded.length > 0 && (
            <div>
              <h3 className="flex items-center gap-1.5 text-sm font-medium text-green-700 dark:text-green-400 mb-2">
                <Plus size={14} />
                {isInitial ? "Your Tasks" : "New Tasks"}
              </h3>
              <ul className="space-y-1.5">
                {allAdded.map((t, i) => (
                  <li
                    key={i}
                    className="text-sm bg-green-50 dark:bg-green-900/20 rounded-lg px-3 py-2 text-gray-800 dark:text-gray-200"
                  >
                    <span className="font-medium">{t.name}</span>
                    <span className="block text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                      {formatTime(t.start)} - {formatTime(t.end)}
                      {t.location ? ` · ${t.location}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Removed tasks */}
          {allRemoved.length > 0 && (
            <div>
              <h3 className="flex items-center gap-1.5 text-sm font-medium text-red-700 dark:text-red-400 mb-2">
                <Minus size={14} />
                Removed Tasks
              </h3>
              <ul className="space-y-1.5">
                {allRemoved.map((t, i) => (
                  <li
                    key={i}
                    className="text-sm bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2 text-gray-800 dark:text-gray-200"
                  >
                    <span className="font-medium line-through">{t.name}</span>
                    <span className="block text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                      {formatTime(t.start)} - {formatTime(t.end)}
                      {t.location ? ` · ${t.location}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Modified tasks */}
          {allModified.length > 0 && (
            <div>
              <h3 className="flex items-center gap-1.5 text-sm font-medium text-amber-700 dark:text-amber-400 mb-2">
                <RefreshCw size={14} />
                Changed Tasks
              </h3>
              <ul className="space-y-1.5">
                {allModified.map((t, i) => (
                  <li
                    key={i}
                    className="text-sm bg-amber-50 dark:bg-amber-900/20 rounded-lg px-3 py-2 text-gray-800 dark:text-gray-200"
                  >
                    <span className="font-medium">{t.name}</span>
                    <ul className="mt-1 space-y-0.5">
                      {Object.entries(t.changes).map(([field, change]) => (
                        <li
                          key={field}
                          className="text-xs text-gray-500 dark:text-gray-400"
                        >
                          <span className="font-medium text-gray-600 dark:text-gray-300">
                            {FIELD_LABELS[field] || field}:
                          </span>{" "}
                          <span className="line-through text-red-500/70">
                            {isTimeField(field)
                              ? formatTime(change.old)
                              : change.old || "-"}
                          </span>{" "}
                          {"->"}{" "}
                          <span className="text-green-600 dark:text-green-400">
                            {isTimeField(field)
                              ? formatTime(change.new)
                              : change.new || "-"}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t border-gray-200 dark:border-gray-700">
          <button
            onClick={onDismiss}
            className="w-full py-2 px-4 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium transition-colors"
          >
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}

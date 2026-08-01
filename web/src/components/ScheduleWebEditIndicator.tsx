"use client";

import { useEffect, useMemo, useState } from "react";
import { PencilLine, X } from "lucide-react";
import {
  formatWebEditTimestamp,
  type WebEditSummary,
} from "@/lib/webEditConfidence";

function editStateFingerprint(eventId: number, summary: WebEditSummary): string {
  return [eventId, summary.edited_task_count, summary.last_edited_at ?? "none"].join(":");
}

function dismissKey(eventId: number, summary: WebEditSummary): string {
  return `mpopt:web-edits:dismissed:${editStateFingerprint(eventId, summary)}`;
}

function countLabel(count: number): string {
  return `${count} web edit${count === 1 ? "" : "s"}`;
}

/** Render the schedule-local web edit entry point without showing the full review panel by default. */
export function ScheduleWebEditIndicator({
  eventId,
  summary,
  loading = false,
  onReview,
}: {
  eventId: number;
  summary: WebEditSummary | null;
  loading?: boolean;
  onReview: () => void;
}) {
  const [dismissed, setDismissed] = useState(false);
  const items = summary?.items ?? [];
  const fingerprint = useMemo(
    () => (summary ? editStateFingerprint(eventId, summary) : "none"),
    [eventId, summary],
  );

  useEffect(() => {
    if (!summary || summary.edited_task_count === 0) {
      setDismissed(false);
      return;
    }
    try {
      setDismissed(localStorage.getItem(dismissKey(eventId, summary)) === "1");
    } catch {
      setDismissed(false);
    }
  }, [eventId, fingerprint, summary]);

  const editCount = summary?.edited_task_count ?? items.length;
  if (loading || !summary || editCount === 0) return null;

  const label = countLabel(editCount);
  const lastEdit = formatWebEditTimestamp(summary.last_edited_at);
  const description = lastEdit
    ? `${label} since desktop publish. Last edit ${lastEdit}.`
    : `${label} since desktop publish.`;

  const icon = (
    <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-amber-500/10 text-amber-700 ring-1 ring-amber-500/20 dark:text-amber-300">
      <PencilLine className="h-3.5 w-3.5" />
    </span>
  );

  if (dismissed) {
    return (
      <button
        type="button"
        onClick={onReview}
        className="inline-flex items-center gap-2 rounded-lg border border-amber-200/70 bg-white px-2.5 py-1.5 text-sm text-amber-800 shadow-sm transition-colors hover:bg-amber-50 dark:border-amber-900/50 dark:bg-gray-800 dark:text-amber-200 dark:hover:bg-amber-950/20"
        aria-label={label}
        title={label}
      >
        {icon}
        <span className="sr-only">{label}</span>
      </button>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-200/70 bg-white px-3 py-2 text-sm text-gray-700 shadow-sm dark:border-amber-900/50 dark:bg-gray-800 dark:text-gray-200">
      {icon}
      <span className="text-amber-900 dark:text-amber-100">{description}</span>
      <button
        type="button"
        onClick={onReview}
        className="rounded-md border border-amber-300/70 px-2 py-1 text-xs font-medium text-amber-800 transition-colors hover:bg-amber-50 dark:border-amber-800 dark:text-amber-200 dark:hover:bg-amber-950/30"
      >
        Review
      </button>
      <button
        type="button"
        onClick={() => {
          try {
            localStorage.setItem(dismissKey(eventId, summary), "1");
          } catch {
            // Ignore storage failures. The indicator still remains usable.
          }
          setDismissed(true);
        }}
        className="rounded-md p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-700 dark:hover:text-gray-200"
        aria-label="Dismiss web edit notice"
        title="Dismiss web edit notice"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
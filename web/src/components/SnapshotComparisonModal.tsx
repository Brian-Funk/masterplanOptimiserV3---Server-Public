"use client";

import { useState } from "react";
import { AlertCircle, CheckCircle, CircleHelp, GitCompare, X } from "lucide-react";
import type {
  SnapshotComparisonSection,
  SnapshotComparisonSummary,
  SnapshotTaskSummary,
} from "@/lib/snapshotComparison";

const LEVEL_STYLES = {
  healthy: {
    icon: CheckCircle,
    iconColour: "text-green-600 dark:text-green-300",
    ring: "ring-green-500/20",
  },
  review: {
    icon: GitCompare,
    iconColour: "text-amber-600 dark:text-amber-300",
    ring: "ring-amber-500/20",
  },
  blocked: {
    icon: AlertCircle,
    iconColour: "text-red-600 dark:text-red-300",
    ring: "ring-red-500/20",
  },
  unknown: {
    icon: CircleHelp,
    iconColour: "text-gray-500 dark:text-gray-300",
    ring: "ring-gray-400/20",
  },
};

function taskLine(task?: SnapshotTaskSummary | null): string {
  if (!task) return "Not available";
  const time = task.startTime && task.endTime
    ? `${task.startTime} - ${task.endTime}`
    : task.startTime || task.endTime || null;
  return [time, task.location, task.assignedPeople?.join(", ")]
    .filter(Boolean)
    .join(" · ") || task.title || "Not available";
}

function CountItem({ label, value }: { label: string; value: number }) {
  return (
    <span className="rounded-full bg-gray-50 px-2.5 py-1 text-xs text-gray-600 ring-1 ring-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:ring-gray-700">
      {label} <strong className="font-semibold text-gray-900 dark:text-gray-100">{value}</strong>
    </span>
  );
}

function SectionBlock({ section }: { section: SnapshotComparisonSection }) {
  const [showAll, setShowAll] = useState(false);
  const visibleItems = showAll ? section.items : section.items.slice(0, 3);

  return (
    <section className="rounded-xl border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-900">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          {section.title}
        </h4>
        <span className="text-xs text-gray-500 dark:text-gray-400">
          {section.count} {section.count === 1 ? "change" : "changes"}
        </span>
      </div>
      <div className="space-y-2">
        {visibleItems.map((item, index) => (
          <article
            key={`${section.id}-${item.taskId ?? item.taskName}-${index}`}
            className="rounded-lg bg-gray-50 px-3 py-2 text-sm dark:bg-gray-800/70"
          >
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <p className="font-medium text-gray-900 dark:text-gray-100">
                {item.taskName}
              </p>
              {item.dayLabel && (
                <span className="text-xs text-gray-500 dark:text-gray-400">
                  {item.dayLabel}
                </span>
              )}
            </div>
            <p className="mt-1 text-xs text-gray-600 dark:text-gray-300">
              {item.changeSummary}
            </p>
            <div className="mt-2 grid gap-1 text-xs text-gray-500 dark:text-gray-400 sm:grid-cols-2">
              {item.before && <p><span className="font-medium">Before:</span> {taskLine(item.before)}</p>}
              {item.after && <p><span className="font-medium">After:</span> {taskLine(item.after)}</p>}
            </div>
            {item.affectedPeople && item.affectedPeople.length > 0 && (
              <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                Affected: {item.affectedPeople.join(", ")}
              </p>
            )}
          </article>
        ))}
      </div>
      {section.items.length > 3 && (
        <button
          type="button"
          onClick={() => setShowAll((value) => !value)}
          className="mt-2 text-xs font-medium text-gray-500 underline-offset-2 hover:text-gray-700 hover:underline dark:text-gray-400 dark:hover:text-gray-200"
        >
          {showAll ? "Show less" : `Show all ${section.count}`}
        </button>
      )}
    </section>
  );
}

export function SnapshotComparisonModal({
  open,
  loading,
  summary,
  onClose,
}: {
  open: boolean;
  loading?: boolean;
  summary: SnapshotComparisonSummary | null;
  onClose: () => void;
}) {
  if (!open) return null;

  const displaySummary = loading || !summary
    ? {
        level: "unknown" as const,
        headline: "Comparing schedules",
        description: "Checking the selected snapshot against the current schedule.",
        snapshotLabel: "selected snapshot",
        totalChanges: 0,
        addedCount: 0,
        removedCount: 0,
        timeChangeCount: 0,
        locationChangeCount: 0,
        assignmentChangeCount: 0,
        detailsChangeCount: 0,
        dayChangeCount: 0,
        sections: [],
      }
    : summary;
  const style = LEVEL_STYLES[displaySummary.level];
  const Icon = style.icon;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4 py-6">
      <div className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-2xl border border-gray-200 bg-white shadow-xl dark:border-gray-700 dark:bg-gray-900">
        <header className="flex items-start justify-between gap-4 border-b border-gray-100 px-5 py-4 dark:border-gray-800">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className={`inline-flex h-7 w-7 items-center justify-center rounded-full bg-white ring-1 dark:bg-gray-900 ${style.ring}`}>
                <Icon className={`h-4 w-4 ${style.iconColour}`} />
              </span>
              <div>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  Compared with {displaySummary.snapshotLabel}
                </p>
                <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
                  {displaySummary.headline}
                </h3>
              </div>
            </div>
            <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
              {displaySummary.description}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200"
            aria-label="Close comparison"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="overflow-y-auto px-5 py-4">
          {!loading && summary && (
            <div className="mb-4 flex flex-wrap gap-2">
              <CountItem label="Added" value={summary.addedCount} />
              <CountItem label="Removed" value={summary.removedCount} />
              <CountItem label="Time" value={summary.timeChangeCount} />
              <CountItem label="Location" value={summary.locationChangeCount} />
              <CountItem label="Assignments" value={summary.assignmentChangeCount} />
              <CountItem label="Details" value={summary.detailsChangeCount} />
            </div>
          )}

          {loading ? (
            <div className="rounded-xl bg-gray-50 p-4 text-sm text-gray-500 dark:bg-gray-800 dark:text-gray-400">
              Loading comparison...
            </div>
          ) : displaySummary.sections.length === 0 ? (
            <div className="rounded-xl bg-gray-50 p-4 text-sm text-gray-500 dark:bg-gray-800 dark:text-gray-400">
              {displaySummary.level === "healthy"
                ? "There are no schedule differences to review."
                : "No comparable change details are available."}
            </div>
          ) : (
            <div className="space-y-3">
              {displaySummary.sections.map((section) => (
                <SectionBlock key={section.id} section={section} />
              ))}
            </div>
          )}
        </div>

        <footer className="flex justify-end border-t border-gray-100 px-5 py-3 dark:border-gray-800">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-800"
          >
            Close
          </button>
        </footer>
      </div>
    </div>
  );
}
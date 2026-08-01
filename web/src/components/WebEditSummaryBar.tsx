"use client";

import { useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  Eye,
  Info,
} from "lucide-react";
import {
  formatWebEditTimestamp,
  groupWebEditItemsByDay,
  summariseWebEditState,
  type WebEditItem,
  type WebEditSummary,
} from "@/lib/webEditConfidence";

const tone = {
  healthy: {
    container:
      "border-green-200/70 bg-green-50/70 text-green-900 dark:border-green-900/50 dark:bg-green-950/20 dark:text-green-100",
    icon: "text-green-600 dark:text-green-300",
    pill: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-200",
    iconNode: Check,
  },
  review: {
    container:
      "border-amber-200/70 bg-amber-50/70 text-amber-950 dark:border-amber-900/50 dark:bg-amber-950/20 dark:text-amber-100",
    icon: "text-amber-600 dark:text-amber-300",
    pill: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-200",
    iconNode: AlertTriangle,
  },
  blocked: {
    container:
      "border-red-200/80 bg-red-50/80 text-red-950 dark:border-red-900/60 dark:bg-red-950/20 dark:text-red-100",
    icon: "text-red-600 dark:text-red-300",
    pill: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-200",
    iconNode: AlertTriangle,
  },
  unknown: {
    container:
      "border-gray-200 bg-white text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100",
    icon: "text-gray-500 dark:text-gray-400",
    pill: "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300",
    iconNode: Info,
  },
};

function formatDayLabel(day: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return day;
  const [year, month, date] = day.split("-");
  return `${date}.${month}.${year}`;
}

function itemMeta(item: WebEditItem): string {
  const details = [
    item.edited_by ? `edited by ${item.edited_by}` : null,
    formatWebEditTimestamp(item.edited_at),
  ].filter(Boolean);
  return details.join(" ");
}

export function WebEditSummaryBar({
  summary,
  loading = false,
  currentUserId = null,
  onReview,
}: {
  summary: WebEditSummary | null;
  loading?: boolean;
  currentUserId?: number | null;
  onReview?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [filter, setFilter] = useState<"all" | "mine">("all");
  const display = summariseWebEditState(summary);
  const config = tone[display.level];
  const Icon = config.iconNode;
  const items = useMemo(() => summary?.items ?? [], [summary?.items]);
  const filteredItems = useMemo(() => {
    if (filter === "mine" && currentUserId) {
      return items.filter((item) => item.edited_by_user_id === currentUserId);
    }
    return items;
  }, [currentUserId, filter, items]);
  const grouped = groupWebEditItemsByDay(filteredItems);
  const canFilterMine =
    Boolean(currentUserId) &&
    items.some((item) => item.edited_by_user_id === currentUserId);

  return (
    <section className={`rounded-lg border px-3 py-2 ${config.container}`}>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-start gap-2">
          <Icon size={16} className={`mt-0.5 shrink-0 ${config.icon}`} />
          <div className="min-w-0">
            <p className="text-sm font-semibold leading-5">
              {loading ? "Checking web edits" : display.headline}
            </p>
            <p className="text-xs leading-5 opacity-80">
              {loading ? "Loading web edit state..." : display.description}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span
            className={`rounded-full px-2 py-0.5 text-xs font-medium ${config.pill}`}
          >
            {display.countLabel}
          </span>
          {onReview && items.length > 0 && (
            <button
              type="button"
              onClick={onReview}
              aria-label="Review web edits"
              className="inline-flex items-center gap-1 rounded-md border border-current/20 px-2 py-1 text-xs font-medium hover:bg-white/50 dark:hover:bg-white/10"
            >
              <Eye size={13} /> {items.length === 1 ? "Review edit" : `Review ${items.length} edits`}
            </button>
          )}
          {items.length > 0 && (
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              aria-label={expanded ? "Hide details" : "Show details"}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium opacity-80 hover:bg-white/50 hover:opacity-100 dark:hover:bg-white/10"
            >
              Details{" "}
              {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            </button>
          )}
        </div>
      </div>

      {expanded && items.length > 0 && (
        <div className="mt-3 border-t border-current/10 pt-3">
          {canFilterMine && (
            <div className="mb-3 flex gap-1">
              {(["all", "mine"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setFilter(value)}
                  className={`rounded-md px-2 py-1 text-xs font-medium ${
                    filter === value
                      ? "bg-white/70 dark:bg-white/15"
                      : "opacity-70 hover:bg-white/50 dark:hover:bg-white/10"
                  }`}
                >
                  {value === "all" ? "All edits" : "Edited by me"}
                </button>
              ))}
            </div>
          )}
          {grouped.length === 0 ? (
            <p className="text-xs opacity-75">No edits match this filter.</p>
          ) : (
            <div className="space-y-3">
              {grouped.map((group) => (
                <div key={group.day}>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide opacity-65">
                    {formatDayLabel(group.day)}
                  </p>
                  <div className="space-y-1.5">
                    {group.items.map((item) => (
                      <div
                        key={item.task_id}
                        className="rounded-md bg-white/55 px-2 py-1.5 text-xs dark:bg-white/10"
                      >
                        <div className="flex flex-col gap-0.5 sm:flex-row sm:items-center sm:justify-between">
                          <span className="font-medium">{item.task_name}</span>
                          {itemMeta(item) && (
                            <span className="opacity-70">
                              {itemMeta(item)}
                            </span>
                          )}
                        </div>
                        <p className="mt-0.5 opacity-75">
                          {item.change_summary.join(", ") || "Edited on the web"}
                          {item.location ? ` - ${item.location}` : ""}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

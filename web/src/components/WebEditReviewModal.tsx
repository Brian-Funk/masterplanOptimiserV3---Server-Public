"use client";

import { useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle,
  Eye,
  PencilLine,
  RotateCcw,
  X,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import {
  formatWebEditTimestamp,
  groupWebEditItemsByDay,
  type WebEditItem,
  type WebEditSummary,
} from "@/lib/webEditConfidence";

function formatDayLabel(day: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return day;
  const [year, month, date] = day.split("-");
  return `${date}.${month}.${year}`;
}

function itemMeta(item: WebEditItem): string {
  return [
    item.edited_by ? `edited by ${item.edited_by}` : null,
    formatWebEditTimestamp(item.edited_at),
  ]
    .filter(Boolean)
    .join(" ");
}

function currentContext(item: WebEditItem): string {
  return item.current_summary || [item.location, itemMeta(item)].filter(Boolean).join(" · ");
}

export function WebEditReviewModal({
  open,
  eventId,
  summary,
  loading = false,
  canRevert = false,
  onClose,
  onRefresh,
}: {
  open: boolean;
  eventId: number | "";
  summary: WebEditSummary | null;
  loading?: boolean;
  canRevert?: boolean;
  onClose: () => void;
  onRefresh: () => Promise<void> | void;
}) {
  const [expandedTaskId, setExpandedTaskId] = useState<number | null>(null);
  const [confirmItem, setConfirmItem] = useState<WebEditItem | null>(null);
  const [confirmBulk, setConfirmBulk] = useState<"selected" | "all" | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const items = useMemo(() => summary?.items ?? [], [summary?.items]);
  const grouped = useMemo(() => groupWebEditItemsByDay(items), [items]);
  const selectedItems = items.filter((item) => selectedIds.has(item.task_id));

  if (!open) return null;

  const refreshAfterRevert = async (text: string) => {
    setMessage({ type: "success", text });
    setSelectedIds(new Set());
    setConfirmItem(null);
    setConfirmBulk(null);
    await onRefresh();
  };

  const handleSingleRevert = async (item: WebEditItem) => {
    if (!eventId) return;
    setBusy(true);
    setMessage(null);
    try {
      const res = await apiFetch(
        `/api/v1/admin/events/${eventId}/web-edits/${item.task_id}/revert`,
        { method: "POST", body: JSON.stringify({}) },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Could not revert web edit");
      await refreshAfterRevert(
        data.message || `${item.task_name} reverted to the published version.`,
      );
    } catch {
      setMessage({
        type: "error",
        text: `Could not revert ${item.task_name}. Please try again.`,
      });
    } finally {
      setBusy(false);
    }
  };

  const handleBulkRevert = async () => {
    if (!eventId || !confirmBulk) return;
    const taskIds =
      confirmBulk === "selected" ? Array.from(selectedIds) : undefined;
    setBusy(true);
    setMessage(null);
    try {
      const res = await apiFetch(`/api/v1/admin/events/${eventId}/web-edits/revert`, {
        method: "POST",
        body: JSON.stringify({
          task_ids: taskIds,
          revert_all: confirmBulk === "all",
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Could not revert web edits");
      await refreshAfterRevert(data.message || "Web edits reverted.");
    } catch {
      setMessage({
        type: "error",
        text: "Could not revert the selected web edits. Please try again.",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 px-4 py-6"
      role="dialog"
      aria-modal="true"
      aria-label="Review web edits"
    >
      <div className="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-900">
        <div className="flex items-start justify-between gap-4 border-b border-gray-200 px-5 py-4 dark:border-gray-700">
          <div>
            <div className="flex items-center gap-2">
              <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-amber-500/10 text-amber-700 ring-1 ring-amber-500/20 dark:text-amber-300">
                <PencilLine className="h-3.5 w-3.5" />
              </span>
              <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                Review web edits
              </h2>
            </div>
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
              {items.length === 0
                ? "No committed web edits need review."
                : `${items.length} task${items.length === 1 ? "" : "s"} differ from the desktop-published schedule.`}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800"
            aria-label="Close review web edits"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="overflow-y-auto px-5 py-4">
          {message && (
            <div
              className={`mb-4 flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${
                message.type === "success"
                  ? "border-green-200 bg-green-50 text-green-800 dark:border-green-900/50 dark:bg-green-950/20 dark:text-green-100"
                  : "border-red-200 bg-red-50 text-red-800 dark:border-red-900/50 dark:bg-red-950/20 dark:text-red-100"
              }`}
            >
              {message.type === "success" ? (
                <CheckCircle className="mt-0.5 h-4 w-4 shrink-0" />
              ) : (
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              )}
              {message.text}
            </div>
          )}

          {loading ? (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Loading web edits...
            </p>
          ) : items.length === 0 ? (
            <div className="rounded-lg border border-green-200/70 bg-green-50/70 px-4 py-3 text-sm text-green-900 dark:border-green-900/50 dark:bg-green-950/20 dark:text-green-100">
              Live schedule matches the published desktop source.
            </div>
          ) : (
            <div className="space-y-4">
              {grouped.map((group) => (
                <section key={group.day}>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                    {formatDayLabel(group.day)}
                  </h3>
                  <div className="space-y-2">
                    {group.items.map((item) => {
                      const expanded = expandedTaskId === item.task_id;
                      const selected = selectedIds.has(item.task_id);
                      return (
                        <div
                          key={item.task_id}
                          className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800"
                        >
                          <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                {canRevert && (
                                  <input
                                    type="checkbox"
                                    checked={selected}
                                    onChange={(event) => {
                                      const next = new Set(selectedIds);
                                      if (event.target.checked) next.add(item.task_id);
                                      else next.delete(item.task_id);
                                      setSelectedIds(next);
                                    }}
                                    aria-label={`Select ${item.task_name}`}
                                    className="h-4 w-4 rounded border-gray-300 text-amber-600"
                                  />
                                )}
                                <span className="font-medium text-gray-900 dark:text-gray-100">
                                  {item.task_name}
                                </span>
                              </div>
                              <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                                {item.change_summary.join(", ") || "Edited on the web"}
                                {itemMeta(item) ? ` · ${itemMeta(item)}` : ""}
                              </p>
                              <p className="mt-1 text-xs text-gray-600 dark:text-gray-300">
                                {currentContext(item)}
                              </p>
                            </div>
                            <div className="flex shrink-0 gap-1">
                              <button
                                type="button"
                                onClick={() =>
                                  setExpandedTaskId(expanded ? null : item.task_id)
                                }
                                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
                              >
                                <Eye className="h-3.5 w-3.5" />
                                {expanded ? "Hide" : "Review"}
                              </button>
                              {canRevert && (
                                <button
                                  type="button"
                                  onClick={() => setConfirmItem(item)}
                                  className="inline-flex items-center gap-1 rounded-md border border-amber-300/70 px-2 py-1 text-xs font-medium text-amber-800 hover:bg-amber-50 dark:border-amber-800 dark:text-amber-200 dark:hover:bg-amber-950/30"
                                >
                                  <RotateCcw className="h-3.5 w-3.5" />
                                  Revert
                                </button>
                              )}
                            </div>
                          </div>

                          {expanded && (
                            <div className="mt-3 grid gap-2 border-t border-gray-100 pt-3 text-xs dark:border-gray-700 sm:grid-cols-2">
                              <div className="rounded-md bg-gray-50 p-2 dark:bg-gray-900/60">
                                <p className="font-semibold text-gray-500 dark:text-gray-400">
                                  Original published
                                </p>
                                <p className="mt-1 text-gray-900 dark:text-gray-100">
                                  {item.original_summary}
                                </p>
                              </div>
                              <div className="rounded-md bg-amber-50/70 p-2 dark:bg-amber-950/20">
                                <p className="font-semibold text-amber-700 dark:text-amber-300">
                                  Current live
                                </p>
                                <p className="mt-1 text-gray-900 dark:text-gray-100">
                                  {item.current_summary}
                                </p>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </section>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-col gap-2 border-t border-gray-200 px-5 py-4 dark:border-gray-700 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Reverting restores tasks to the version originally published from the desktop app.
          </p>
          {canRevert && items.length > 0 && (
            <div className="flex gap-2">
              <button
                type="button"
                disabled={selectedIds.size === 0 || busy}
                onClick={() => setConfirmBulk("selected")}
                className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-800"
              >
                Revert selected
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => setConfirmBulk("all")}
                className="rounded-lg border border-amber-300 px-3 py-1.5 text-sm font-medium text-amber-800 hover:bg-amber-50 disabled:opacity-50 dark:border-amber-800 dark:text-amber-200 dark:hover:bg-amber-950/30"
              >
                Revert all
              </button>
            </div>
          )}
        </div>
      </div>

      {(confirmItem || confirmBulk) && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 px-4">
          <div className="w-full max-w-lg rounded-xl border border-gray-200 bg-white p-5 shadow-2xl dark:border-gray-700 dark:bg-gray-900">
            {confirmItem ? (
              <>
                <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
                  Revert {confirmItem.task_name}?
                </h3>
                <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                  This will restore the task to the version originally published from the desktop app.
                </p>
                <div className="mt-4 grid gap-2 text-sm">
                  <div className="rounded-lg bg-gray-50 p-3 dark:bg-gray-800">
                    <p className="text-xs font-semibold text-gray-500 dark:text-gray-400">
                      Original published
                    </p>
                    <p>{confirmItem.original_summary}</p>
                  </div>
                  <div className="rounded-lg bg-amber-50 p-3 dark:bg-amber-950/20">
                    <p className="text-xs font-semibold text-amber-700 dark:text-amber-300">
                      Current live
                    </p>
                    <p>{confirmItem.current_summary}</p>
                  </div>
                </div>
                <div className="mt-5 flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => setConfirmItem(null)}
                    className="rounded-lg px-3 py-1.5 text-sm font-medium text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => handleSingleRevert(confirmItem)}
                    className="rounded-lg bg-amber-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
                  >
                    Revert to published version
                  </button>
                </div>
              </>
            ) : (
              <>
                <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
                  Revert {confirmBulk === "all" ? items.length : selectedItems.length} web edits?
                </h3>
                <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                  This will restore the selected tasks to their published versions. This cannot be undone from this panel.
                </p>
                <div className="mt-5 flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => setConfirmBulk(null)}
                    className="rounded-lg px-3 py-1.5 text-sm font-medium text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    disabled={busy || (confirmBulk === "selected" && selectedIds.size === 0)}
                    onClick={handleBulkRevert}
                    className="rounded-lg bg-amber-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
                  >
                    Revert web edits
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

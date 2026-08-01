"use client";

import React, { useState } from "react";
import { ChevronUp, ChevronDown, Send, Trash2 } from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
/** Person assignment stored on a draft change. */
export interface Attendee {
  name: string;
  person_id: number;
}

/** Draft changes for an existing calendar task. */
export interface DraftEdit {
  name?: string;
  summary?: string;
  description?: string;
  start?: string;
  end?: string;
  location_name?: string;
  location_address?: string;
  color?: string;
  attendees?: Attendee[];
  field_assignments?: Record<string, Attendee[]>;
}

/** Draft task created locally before publish. */
export interface DraftNewTask {
  tempId: number;
  name: string;
  start: string;
  end: string;
  summary?: string;
  description?: string;
  location_name?: string;
  location_address?: string;
  color?: string;
  attendees?: Attendee[];
}

/** Props for `DraftChangesPanel`. */
export interface DraftChangesPanelProps {
  edits: Map<number, DraftEdit>;
  deletions: Set<number>;
  creations: DraftNewTask[];
  /** Maps task ID to its display name (for edit/delete labels) */
  taskNames: Map<number, string>;
  onCommit: () => void;
  onDiscard: () => void;
  onRemoveEdit: (taskId: number) => void;
  onRemoveDeletion: (taskId: number) => void;
  onRemoveCreation: (tempId: number) => void;
  committing: boolean;
  commitDisabled?: boolean;
  commitDisabledMessage?: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function describeEdit(edit: DraftEdit): string {
  const parts: string[] = [];
  if (edit.name !== undefined) parts.push("name");
  if (edit.start !== undefined || edit.end !== undefined) parts.push("time");
  if (edit.location_name !== undefined || edit.location_address !== undefined)
    parts.push("location");
  if (edit.summary !== undefined) parts.push("summary");
  if (edit.description !== undefined) parts.push("description");
  if (edit.color !== undefined) parts.push("colour");
  if (edit.field_assignments !== undefined) parts.push("assignments");
  else if (edit.attendees !== undefined) parts.push("attendees");
  return parts.join(", ");
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
/**
 * Sticky review panel for pending edits, deletions, and new tasks.
 */
export function DraftChangesPanel({
  edits,
  deletions,
  creations,
  taskNames,
  onCommit,
  onDiscard,
  onRemoveEdit,
  onRemoveDeletion,
  onRemoveCreation,
  committing,
  commitDisabled = false,
  commitDisabledMessage,
}: DraftChangesPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const total = edits.size + deletions.size + creations.length;

  if (total === 0) return null;

  return (
    <div className="fixed bottom-[calc(4rem+env(safe-area-inset-bottom,0px))] left-0 right-0 z-40 md:bottom-0">
      {/* Expanded detail view */}
      {expanded && (
        <div className="max-w-3xl mx-auto bg-white dark:bg-gray-800 border border-b-0 border-gray-200 dark:border-gray-700 rounded-t-xl shadow-xl max-h-64 overflow-y-auto px-5 py-4">
          {/* Edits */}
          {edits.size > 0 && (
            <div className="mb-3">
              <h4 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-1.5">
                Edited ({edits.size})
              </h4>
              <div className="space-y-1">
                {Array.from(edits.entries()).map(([taskId, edit]) => (
                  <div
                    key={taskId}
                    className="flex items-center justify-between text-sm"
                  >
                    <div className="text-gray-900 dark:text-gray-100">
                      <span className="font-medium">
                        {taskNames.get(taskId) || `Task #${taskId}`}
                      </span>
                      <span className="text-gray-400 dark:text-gray-500 ml-2">
                        {describeEdit(edit)}
                      </span>
                    </div>
                    <button
                      onClick={() => onRemoveEdit(taskId)}
                      className="text-gray-400 hover:text-red-500 transition-colors p-1"
                      title="Remove from draft"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Deletions */}
          {deletions.size > 0 && (
            <div className="mb-3">
              <h4 className="text-xs font-semibold text-red-400 dark:text-red-500 uppercase tracking-wider mb-1.5">
                Deleted ({deletions.size})
              </h4>
              <div className="space-y-1">
                {Array.from(deletions).map((taskId) => (
                  <div
                    key={taskId}
                    className="flex items-center justify-between text-sm"
                  >
                    <span className="text-red-600 dark:text-red-400 line-through">
                      {taskNames.get(taskId) || `Task #${taskId}`}
                    </span>
                    <button
                      onClick={() => onRemoveDeletion(taskId)}
                      className="text-gray-400 hover:text-green-500 transition-colors p-1"
                      title="Undo deletion"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Creations */}
          {creations.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-green-400 dark:text-green-500 uppercase tracking-wider mb-1.5">
                New ({creations.length})
              </h4>
              <div className="space-y-1">
                {creations.map((task) => (
                  <div
                    key={task.tempId}
                    className="flex items-center justify-between text-sm"
                  >
                    <span className="text-green-600 dark:text-green-400 font-medium">
                      {task.name}
                    </span>
                    <button
                      onClick={() => onRemoveCreation(task.tempId)}
                      className="text-gray-400 hover:text-red-500 transition-colors p-1"
                      title="Remove from draft"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Bottom bar */}
      <div className="bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 shadow-lg">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-2 px-3 py-2.5 sm:px-5 sm:py-3">
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:text-gray-900 dark:hover:text-gray-100 transition-colors"
          >
            {expanded ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
            <span>
              {total} pending change{total !== 1 ? "s" : ""}
            </span>
          </button>
          <div className="flex items-center gap-2">
            <button
              onClick={onDiscard}
              disabled={committing}
              className="hidden min-h-10 rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700 sm:inline-flex"
            >
              Discard All
            </button>
            <button
              onClick={onCommit}
              disabled={committing || commitDisabled}
              className="inline-flex min-h-10 items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
              title={commitDisabled ? commitDisabledMessage : undefined}
            >
              <Send size={14} />
              {committing ? "Committing..." : "Commit All"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

"use client";

import React, { useEffect, useState } from "react";
import {
  X,
  Pencil,
  PencilLine,
  RotateCcw,
  Trash2,
  ArrowLeft,
  ArrowRight,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { TASK_COLORS, ALERT_COLOR } from "@/lib/colors";
import { describeWebEditTask } from "@/lib/webEditConfidence";
import { PermittedDataInputNotice } from "@/components/PermittedDataInputNotice";

// ---------------------------------------------------------------------------
// Types (same as CalendarGrid)
// ---------------------------------------------------------------------------
/** Person assigned to a task in the task detail view. */
export interface Attendee {
  name: string;
  person_id: number;
}

/** Detailed calendar task shown in the editor and viewer modal. */
export interface Task {
  id: number;
  external_task_id: number;
  name: string;
  summary: string | null;
  description: string | null;
  start: string;
  end: string;
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

/** Person available for assignment from the web task editor. */
export interface Person {
  id: number;
  external_person_id: number;
  first_name: string;
  last_name: string;
}

/** Draft changes for a task before they are committed to the server. */
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
  field_values?: Record<string, unknown>;
}

/** Props for `TaskDetailModal`. */
export interface TaskDetailModalProps {
  task: Task;
  canEdit: boolean;
  eventId: number;
  persons: Person[];
  onClose: () => void;
  onDataChanged: () => void;
  onDraftEdit: (taskId: number, changes: DraftEdit) => void;
  onDraftDelete: (taskId: number) => void;
  isDraftNew?: boolean;
  onNavigatePrev?: (() => void) | null;
  onNavigateNext?: (() => void) | null;
  dataPolicyAcknowledged: boolean;
  dataPolicyVersion?: number | null;
  dataPolicySha256?: string | null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function formatTime(iso: string): string {
  const timePart = iso.split("T")[1] || "00:00";
  const [hours, minutes] = timePart.split(":").map(Number);
  return `${hours.toString().padStart(2, "0")}:${minutes.toString().padStart(2, "0")}`;
}

function formatDate(iso: string): string {
  const datePart = iso.split("T")[0];
  const d = new Date(datePart + "T00:00:00");
  return d.toLocaleDateString(undefined, {
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function isoToDateTimeLocal(iso: string): string {
  const datePart = iso.split("T")[0];
  const timePart = iso.split("T")[1] || "00:00";
  const [hours, minutes] = timePart.split(":").map(Number);
  return `${datePart}T${hours.toString().padStart(2, "0")}:${minutes.toString().padStart(2, "0")}`;
}

function normaliseAttendeeList(value: unknown): Attendee[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((entry) => {
      if (typeof entry !== "object" || entry === null) return null;
      const raw = entry as { person_id?: unknown; id?: unknown; name?: unknown };
      const personId = Number(raw.person_id ?? raw.id);
      const name = String(raw.name ?? "").trim();
      if (!Number.isFinite(personId) || personId <= 0 || !name) return null;
      return { name, person_id: personId };
    })
    .filter((entry): entry is Attendee => entry !== null);
}

function getStructuredAssignmentFields(
  task: Task,
): { fieldId: string; fieldName: string; attendees: Attendee[] }[] {
  const definitions =
    task.field_definitions?.filter((def) => def.type === "persons_list") ?? [];
  if (definitions.length > 0) {
    return definitions.map((def) => ({
      fieldId: def.id,
      fieldName: def.name,
      attendees: normaliseAttendeeList(
        task.field_assignments?.[def.id] ?? task.field_values?.[def.id] ?? [],
      ),
    }));
  }
  if (!task.field_assignments) return [];
  return Object.entries(task.field_assignments).map(([fieldId, attendees]) => ({
    fieldId,
    fieldName: fieldId,
    attendees: normaliseAttendeeList(attendees),
  }));
}

function buildFieldAssignmentRecord(
  task: Task,
): Record<string, Attendee[]> | null {
  const fields = getStructuredAssignmentFields(task);
  if (fields.length === 0) return null;
  return Object.fromEntries(fields.map((field) => [field.fieldId, field.attendees]));
}

function flattenFieldAssignments(
  assignments: Record<string, Attendee[]>,
): Attendee[] {
  const flattened: Attendee[] = [];
  const seen = new Set<number>();
  Object.values(assignments).forEach((attendees) => {
    attendees.forEach((attendee) => {
      if (seen.has(attendee.person_id)) return;
      seen.add(attendee.person_id);
      flattened.push(attendee);
    });
  });
  return flattened;
}

/** Extract persons_list fields from field definitions, values, and assignments. */
function getPersonFields(task: Task): { fieldName: string; names: string }[] {
  // First: use field_definitions for clean field names
  if (task.field_definitions) {
    const fromDefs = task.field_definitions
      .filter((def) => def.type === "persons_list")
      .map((def) => {
        const val = task.field_assignments?.[def.id];
        if (!val) return { fieldName: def.name, names: "" };
        const names = Array.isArray(val)
          ? val.map((p: { name?: string }) => p.name ?? String(p)).join(", ")
          : String(val);
        return { fieldName: def.name, names };
      });
    if (fromDefs.length > 0) return fromDefs;
  }
  return [];
}

/** Extract text (notes) fields */
function getTextFields(task: Task): { fieldName: string; text: string }[] {
  if (!task.field_definitions || !task.field_values) return [];
  return task.field_definitions
    .filter((def) => def.type === "text" && task.field_values?.[def.id])
    .map((def) => ({
      fieldName: def.name,
      text: String(task.field_values![def.id]),
    }));
}

/** Extract link fields */
function getLinkFields(
  task: Task,
): { fieldName: string; url: string; text: string }[] {
  if (!task.field_definitions || !task.field_values) return [];
  return task.field_definitions
    .filter((def) => def.type === "link" && task.field_values?.[def.id])
    .map((def) => {
      const raw = task.field_values![def.id];
      if (typeof raw === "object" && raw !== null && "url" in raw) {
        const obj = raw as { url: string; text?: string };
        return { fieldName: def.name, url: obj.url, text: obj.text || obj.url };
      }
      const url = String(raw);
      return { fieldName: def.name, url, text: url };
    })
    .filter((field) => {
      try {
        const parsed = new URL(field.url);
        return parsed.protocol === "http:" || parsed.protocol === "https:";
      } catch {
        return false;
      }
    });
}

/** Extract location fields (for transfer tasks with start/end locations) */
function getLocationFields(
  task: Task,
): { fieldName: string; name: string; address: string | null }[] {
  if (!task.field_definitions || !task.field_values) return [];
  return task.field_definitions
    .filter((def) => def.type === "location" && task.field_values?.[def.id])
    .map((def) => {
      const raw = task.field_values![def.id];
      if (typeof raw === "object" && raw !== null && "name" in raw) {
        const obj = raw as { name: string; address?: string | null };
        return {
          fieldName: def.name,
          name: obj.name,
          address: obj.address ?? null,
        };
      }
      return { fieldName: def.name, name: String(raw), address: null };
    });
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
/**
 * Modal for viewing a task and drafting authorised schedule edits.
 */
export function TaskDetailModal({
  task,
  canEdit,
  eventId,
  persons,
  onClose,
  onDataChanged,
  onDraftEdit,
  onDraftDelete,
  isDraftNew,
  onNavigatePrev,
  onNavigateNext,
  dataPolicyAcknowledged,
  dataPolicyVersion,
  dataPolicySha256,
}: TaskDetailModalProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editName, setEditName] = useState(task.name);
  const [editStart, setEditStart] = useState(isoToDateTimeLocal(task.start));
  const [editEnd, setEditEnd] = useState(isoToDateTimeLocal(task.end));
  const [editSummary, setEditSummary] = useState(task.summary || "");
  const [editDescription, setEditDescription] = useState(
    task.description || "",
  );
  const [editLocationName, setEditLocationName] = useState(
    task.location_name || "",
  );
  const [editLocationAddress, setEditLocationAddress] = useState(
    task.location_address || "",
  );
  const [editColor, setEditColor] = useState(task.color || "#6B7280");
  const [editAttendees, setEditAttendees] = useState<Attendee[]>([
    ...task.attendees,
  ]);
  const [editFieldAssignments, setEditFieldAssignments] = useState<
    Record<string, Attendee[]> | null
  >(() => buildFieldAssignmentRecord(task));
  const [editFieldValues, setEditFieldValues] = useState<
    Record<string, unknown>
  >(() => ({ ...(task.field_values || {}) }));
  const [reverting, setReverting] = useState(false);
  const [error, setError] = useState("");

  const color = task.color || "#6B7280";
  const webEditDescription = task.has_web_edit ? describeWebEditTask(task) : "";

  useEffect(() => {
    setIsEditing(false);
    setEditName(task.name);
    setEditStart(isoToDateTimeLocal(task.start));
    setEditEnd(isoToDateTimeLocal(task.end));
    setEditSummary(task.summary || "");
    setEditDescription(task.description || "");
    setEditLocationName(task.location_name || "");
    setEditLocationAddress(task.location_address || "");
    setEditColor(task.color || "#6B7280");
    setEditAttendees([...task.attendees]);
    setEditFieldAssignments(buildFieldAssignmentRecord(task));
    setEditFieldValues({ ...(task.field_values || {}) });
    setError("");
  }, [task]);

  /** Convert datetime-local value to naive ISO string (no timezone shift). */
  const dtLocalToISO = (dtLocal: string) =>
    dtLocal.length === 16 ? dtLocal + ":00" : dtLocal;

  const handleSaveDraft = () => {
    const changes: DraftEdit = {};
    if (editName !== task.name) changes.name = editName;
    if ((editSummary || null) !== (task.summary || null))
      changes.summary = editSummary || undefined;
    if ((editDescription || null) !== (task.description || null))
      changes.description = editDescription || undefined;
    const origStart = isoToDateTimeLocal(task.start);
    const origEnd = isoToDateTimeLocal(task.end);
    if (editStart !== origStart) changes.start = dtLocalToISO(editStart);
    if (editEnd !== origEnd) changes.end = dtLocalToISO(editEnd);
    if ((editLocationName || null) !== (task.location_name || null))
      changes.location_name = editLocationName || undefined;
    if ((editLocationAddress || null) !== (task.location_address || null))
      changes.location_address = editLocationAddress || undefined;
    if (editColor !== (task.color || "#6B7280")) changes.color = editColor;
    const originalFieldAssignments = buildFieldAssignmentRecord(task);
    if (
      editFieldAssignments &&
      originalFieldAssignments &&
      JSON.stringify(editFieldAssignments) !==
        JSON.stringify(originalFieldAssignments)
    ) {
      changes.field_assignments = editFieldAssignments;
      changes.attendees = flattenFieldAssignments(editFieldAssignments);
    } else if (JSON.stringify(editAttendees) !== JSON.stringify(task.attendees)) {
      changes.attendees = editAttendees;
    }
    if (
      JSON.stringify(editFieldValues) !==
      JSON.stringify(task.field_values || {})
    )
      changes.field_values = editFieldValues;
    if (Object.keys(changes).length > 0) {
      onDraftEdit(task.id, changes);
    }
    onClose();
  };

  const handleDelete = () => {
    onDraftDelete(task.id);
    onClose();
  };

  const addAttendee = (personId: number) => {
    const person = persons.find((p) => p.external_person_id === personId);
    if (person) {
      setEditAttendees((prev) => [
        ...prev,
        {
          name: `${person.first_name} ${person.last_name}`,
          person_id: person.external_person_id,
        },
      ]);
    }
  };

  const removeAttendee = (index: number) => {
    setEditAttendees((prev) => prev.filter((_, i) => i !== index));
  };

  const addFieldAttendee = (fieldId: string, personId: number) => {
    const person = persons.find((p) => p.external_person_id === personId);
    if (!person) return;
    setEditFieldAssignments((prev) => {
      const current = prev ?? {};
      const fieldAttendees = current[fieldId] ?? [];
      if (fieldAttendees.some((attendee) => attendee.person_id === personId)) {
        return current;
      }
      return {
        ...current,
        [fieldId]: [
          ...fieldAttendees,
          {
            name: `${person.first_name} ${person.last_name}`,
            person_id: person.external_person_id,
          },
        ],
      };
    });
  };

  const removeFieldAttendee = (fieldId: string, index: number) => {
    setEditFieldAssignments((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        [fieldId]: (prev[fieldId] ?? []).filter((_, i) => i !== index),
      };
    });
  };

  const handleRevert = async () => {
    setReverting(true);
    setError("");
    try {
      const res = await apiFetch(
        `/api/v1/calendar/${eventId}/tasks/${task.id}/edits`,
        { method: "DELETE" },
      );
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail || "Failed to revert");
      }
      onDataChanged();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revert");
    } finally {
      setReverting(false);
    }
  };

  const personFields = getPersonFields(task);
  const hasMultiplePersonFields = personFields.length > 1;
  const editableAssignmentFields = getStructuredAssignmentFields(task);
  const hasStructuredAssignments = editableAssignmentFields.length > 0;
  const textFields = getTextFields(task);
  const linkFields = getLinkFields(task);
  const locationFields = getLocationFields(task);

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 sm:items-center sm:p-4"
      onClick={onClose}
    >
      {/* Prev arrow - floating left of modal */}
      {onNavigatePrev && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onNavigatePrev();
          }}
          className="absolute left-2 sm:left-4 top-1/2 -translate-y-1/2 z-50 p-1.5 rounded-full bg-black/10 dark:bg-white/10 hover:bg-black/20 dark:hover:bg-white/20 text-gray-500 dark:text-gray-400 transition-colors"
          title="Previous task"
        >
          <ArrowLeft size={16} />
        </button>
      )}

      {/* Next arrow - floating right of modal */}
      {onNavigateNext && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onNavigateNext();
          }}
          className="absolute right-2 sm:right-4 top-1/2 -translate-y-1/2 z-50 p-1.5 rounded-full bg-black/10 dark:bg-white/10 hover:bg-black/20 dark:hover:bg-white/20 text-gray-500 dark:text-gray-400 transition-colors"
          title="Next task"
        >
          <ArrowRight size={16} />
        </button>
      )}

      <div
        className="flex h-[100dvh] w-full flex-col overflow-hidden bg-white shadow-2xl dark:bg-gray-800 sm:h-auto sm:max-h-[90vh] sm:max-w-lg sm:rounded-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header with colour bar */}
        <div
          className="flex shrink-0 items-center justify-between px-4 py-3 sm:px-6 sm:py-4"
          style={{ backgroundColor: color }}
        >
          <div className="flex-1 min-w-0">
            <h2 className="text-lg font-bold text-white truncate">
              {task.name}
            </h2>
            {task.task_type_name && (
              <p className="text-sm text-white/80">{task.task_type_name}</p>
            )}
          </div>
          <div className="flex items-center gap-2 flex-shrink-0 ml-3">
            {task.has_web_edit && (
              <span
                className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-amber-500/10 text-amber-100 ring-1 ring-amber-200/30"
                title={webEditDescription}
                aria-label={webEditDescription}
              >
                <PencilLine className="h-2.5 w-2.5" />
              </span>
            )}
            {canEdit && !isEditing && (
              <button
                onClick={() => setIsEditing(true)}
                className="p-1.5 rounded-lg bg-white/20 hover:bg-white/30 text-white transition-colors"
                title="Edit task"
              >
                <Pencil size={16} />
              </button>
            )}
            {canEdit && task.has_web_edit && !isEditing && !isDraftNew && (
              <button
                onClick={handleRevert}
                disabled={reverting}
                className="p-1.5 rounded-lg bg-white/20 hover:bg-white/30 text-white transition-colors disabled:opacity-50"
                title="Revert to published version"
              >
                <RotateCcw size={16} />
              </button>
            )}
            {canEdit && !isEditing && (
              <button
                onClick={handleDelete}
                className="p-1.5 rounded-lg bg-white/20 hover:bg-red-500/80 text-white transition-colors"
                title="Delete task"
              >
                <Trash2 size={16} />
              </button>
            )}
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg bg-white/20 hover:bg-white/30 text-white transition-colors"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4 sm:max-h-[60vh] sm:flex-none sm:px-6">
          {error && (
            <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
          )}

          {isEditing ? (
            <div className="space-y-3">
              <PermittedDataInputNotice acknowledged={dataPolicyAcknowledged} version={dataPolicyVersion} sha256={dataPolicySha256} />
              {/* Name */}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Participant-visible task name
                </label>
                <input
                  type="text"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-sm"
                />
              </div>

              {/* Start / End */}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Start
                  </label>
                  <input
                    type="datetime-local"
                    value={editStart}
                    onChange={(e) => setEditStart(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    End
                  </label>
                  <input
                    type="datetime-local"
                    value={editEnd}
                    onChange={(e) => setEditEnd(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-sm"
                  />
                </div>
              </div>

              {/* Location */}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Participant-visible operational location
                </label>
                <input
                  type="text"
                  value={editLocationName}
                  onChange={(e) => setEditLocationName(e.target.value)}
                  placeholder="Operational location name"
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-sm"
                />
                <input
                  type="text"
                  value={editLocationAddress}
                  onChange={(e) => setEditLocationAddress(e.target.value)}
                  placeholder="Participant-visible location details"
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-sm mt-2"
                />
              </div>

              {/* Summary */}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Participant-visible schedule summary
                </label>
                <input
                  type="text"
                  value={editSummary}
                  onChange={(e) => setEditSummary(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-sm"
                />
              </div>

              {/* Description */}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Participant-visible operational instruction
                </label>
                <textarea
                  value={editDescription}
                  onChange={(e) => setEditDescription(e.target.value)}
                  rows={3}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-sm resize-y"
                />
              </div>

              {/* Colour */}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Colour
                </label>
                <div className="flex flex-wrap gap-2">
                  {TASK_COLORS.map(({ hex, label }) => (
                    <button
                      key={hex}
                      type="button"
                      onClick={() => setEditColor(hex)}
                      title={label}
                      className={`w-8 h-8 rounded-full border-2 transition-all ${
                        editColor.toLowerCase() === hex.toLowerCase()
                          ? "border-gray-900 dark:border-white scale-110 ring-2 ring-offset-1 ring-gray-400 dark:ring-gray-500"
                          : "border-transparent hover:scale-110"
                      }`}
                      style={{ backgroundColor: hex }}
                    />
                  ))}
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setEditColor(ALERT_COLOR.hex)}
                    title={ALERT_COLOR.label}
                    className={`w-8 h-8 rounded-full border-2 transition-all ${
                      editColor.toLowerCase() === ALERT_COLOR.hex.toLowerCase()
                        ? "border-gray-900 dark:border-white scale-110 ring-2 ring-offset-1 ring-gray-400 dark:ring-gray-500"
                        : "border-transparent hover:scale-110"
                    }`}
                    style={{ backgroundColor: ALERT_COLOR.hex }}
                  />
                  <span className="text-xs text-gray-500 dark:text-gray-400">
                    {ALERT_COLOR.label}
                  </span>
                </div>
              </div>

              {/* Attendees */}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Attendees
                </label>
                {hasStructuredAssignments ? (
                  <div className="space-y-3">
                    {editableAssignmentFields.map((field) => {
                      const fieldAttendees =
                        editFieldAssignments?.[field.fieldId] ?? [];
                      return (
                        <div
                          key={field.fieldId}
                          className="rounded-lg border border-gray-200 p-2 dark:border-gray-700"
                        >
                          <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                            {field.fieldName}
                          </div>
                          {fieldAttendees.length > 0 ? (
                            <div className="mb-2 flex flex-wrap gap-1.5">
                              {fieldAttendees.map((attendee, index) => (
                                <span
                                  key={`${field.fieldId}-${attendee.person_id}-${index}`}
                                  className="inline-flex items-center gap-1 rounded bg-gray-100 px-2 py-0.5 text-sm text-gray-900 dark:bg-gray-700 dark:text-gray-100"
                                >
                                  {attendee.name}
                                  <button
                                    onClick={() =>
                                      removeFieldAttendee(field.fieldId, index)
                                    }
                                    className="text-gray-400 transition-colors hover:text-red-500"
                                  >
                                    <X size={12} />
                                  </button>
                                </span>
                              ))}
                            </div>
                          ) : (
                            <p className="mb-2 text-xs text-gray-500 dark:text-gray-400">
                              No one assigned.
                            </p>
                          )}
                          <select
                            onChange={(e) => {
                              if (e.target.value) {
                                addFieldAttendee(
                                  field.fieldId,
                                  Number(e.target.value),
                                );
                              }
                              e.target.value = "";
                            }}
                            defaultValue=""
                            className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
                          >
                            <option value="">Add person to {field.fieldName}...</option>
                            {persons
                              .filter(
                                (p) =>
                                  !fieldAttendees.some(
                                    (a) =>
                                      a.person_id === p.external_person_id,
                                  ),
                              )
                              .map((p) => (
                                <option
                                  key={p.external_person_id}
                                  value={p.external_person_id}
                                >
                                  {p.first_name} {p.last_name}
                                </option>
                              ))}
                          </select>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <>
                    {editAttendees.length > 0 && (
                      <div className="mb-2 flex flex-wrap gap-1.5">
                        {editAttendees.map((a, i) => (
                          <span
                            key={`${a.person_id}-${i}`}
                            className="inline-flex items-center gap-1 rounded bg-gray-100 px-2 py-0.5 text-sm text-gray-900 dark:bg-gray-700 dark:text-gray-100"
                          >
                            {a.name}
                            <button
                              onClick={() => removeAttendee(i)}
                              className="text-gray-400 transition-colors hover:text-red-500"
                            >
                              <X size={12} />
                            </button>
                          </span>
                        ))}
                      </div>
                    )}
                    <select
                      onChange={(e) => {
                        if (e.target.value) addAttendee(Number(e.target.value));
                        e.target.value = "";
                      }}
                      defaultValue=""
                      className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
                    >
                      <option value="">Add person...</option>
                      {persons
                        .filter(
                          (p) =>
                            !editAttendees.some(
                              (a) => a.person_id === p.external_person_id,
                            ),
                        )
                        .map((p) => (
                          <option
                            key={p.external_person_id}
                            value={p.external_person_id}
                          >
                            {p.first_name} {p.last_name}
                          </option>
                        ))}
                    </select>
                  </>
                )}
              </div>

              {/* Links (from field_definitions) */}
              {task.field_definitions &&
                task.field_definitions.filter((d) => d.type === "link").length >
                  0 && (
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                      Links
                    </label>
                    <div className="space-y-2">
                      {task.field_definitions
                        .filter((d) => d.type === "link")
                        .map((def) => {
                          const raw = editFieldValues[def.id];
                          const val =
                            typeof raw === "object" &&
                            raw !== null &&
                            "url" in raw
                              ? (raw as { url: string; text?: string })
                              : { url: String(raw ?? ""), text: "" };
                          return (
                            <div key={def.id}>
                              <span className="text-xs text-gray-500 dark:text-gray-400">
                                {def.name}
                              </span>
                              <input
                                type="url"
                                value={val.url}
                                onChange={(e) =>
                                  setEditFieldValues((prev) => ({
                                    ...prev,
                                    [def.id]: {
                                      url: e.target.value,
                                      text: val.text || "",
                                    },
                                  }))
                                }
                                placeholder="URL"
                                className="w-full px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-sm"
                              />
                              <input
                                type="text"
                                value={val.text || ""}
                                onChange={(e) =>
                                  setEditFieldValues((prev) => ({
                                    ...prev,
                                    [def.id]: {
                                      url: val.url,
                                      text: e.target.value,
                                    },
                                  }))
                                }
                                placeholder="Display text (optional)"
                                className="w-full px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-sm mt-1"
                              />
                            </div>
                          );
                        })}
                    </div>
                  </div>
                )}
            </div>
          ) : (
            <>
              {task.has_web_edit && (
                <div className="rounded-lg border border-amber-200/70 bg-amber-50/70 px-3 py-2 text-sm text-amber-950 dark:border-amber-900/50 dark:bg-amber-950/20 dark:text-amber-100">
                  <div className="flex items-center gap-2 font-medium">
                    <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-amber-500/10 text-amber-700 ring-1 ring-amber-500/20 dark:text-amber-300">
                      <PencilLine className="h-2.5 w-2.5" />
                    </span>
                    Edited on the web
                  </div>
                  <p className="mt-1 text-xs opacity-80">
                    {webEditDescription}
                  </p>
                </div>
              )}

              {/* Time */}
              <div>
                <h4 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-1">
                  Time
                </h4>
                <p className="text-sm text-gray-900 dark:text-gray-100">
                  {formatDate(task.start)}
                </p>
                <p className="text-sm text-gray-900 dark:text-gray-100">
                  {formatTime(task.start)} - {formatTime(task.end)}
                </p>
              </div>

              {/* Location */}
              {locationFields.length > 0 ? (
                <div>
                  <h4 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-1">
                    {locationFields.length > 1 ? "Locations" : "Location"}
                  </h4>
                  <div className="space-y-2">
                    {locationFields.map((lf) => (
                      <div key={lf.fieldName}>
                        {locationFields.length > 1 && (
                          <span className="text-xs font-medium text-gray-500 dark:text-gray-400">
                            {lf.fieldName}:
                          </span>
                        )}
                        <p className="text-sm text-gray-900 dark:text-gray-100">
                          {lf.name}
                        </p>
                        {lf.address && (
                          <a
                            href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(lf.address)}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-blue-600 dark:text-blue-400 hover:underline block"
                          >
                            {lf.address}
                          </a>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ) : task.location_name ? (
                <div>
                  <h4 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-1">
                    Location
                  </h4>
                  <p className="text-sm text-gray-900 dark:text-gray-100">
                    {task.location_name}
                  </p>
                  {task.location_address && (
                    <a
                      href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(task.location_address)}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs text-blue-600 dark:text-blue-400 hover:underline block"
                    >
                      {task.location_address}
                    </a>
                  )}
                </div>
              ) : null}

              {/* Assigned persons */}
              {personFields.length > 0 ? (
                <div>
                  <h4 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-1">
                    Assigned
                  </h4>
                  {hasMultiplePersonFields ? (
                    <div className="space-y-1.5">
                      {personFields.map((pf) => (
                        <div key={pf.fieldName}>
                          <span className="text-xs font-medium text-gray-500 dark:text-gray-400">
                            {pf.fieldName}:
                          </span>
                          <span className="text-sm text-gray-900 dark:text-gray-100 ml-1">
                            {pf.names}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-gray-900 dark:text-gray-100">
                      {personFields[0].names}
                    </p>
                  )}
                </div>
              ) : task.attendees.length > 0 ? (
                <div>
                  <h4 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-1">
                    Assigned
                  </h4>
                  <p className="text-sm text-gray-900 dark:text-gray-100">
                    {task.attendees.map((a) => a.name).join(", ")}
                  </p>
                </div>
              ) : null}

              {/* Notes (text fields) */}
              {textFields.length > 0 && (
                <div>
                  <h4 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-1">
                    Notes
                  </h4>
                  <div className="space-y-2">
                    {textFields.map((tf) => (
                      <p
                        key={tf.fieldName}
                        className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap"
                      >
                        {tf.text}
                      </p>
                    ))}
                  </div>
                </div>
              )}

              {/* Links */}
              {linkFields.length > 0 && (
                <div>
                  <h4 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-1">
                    Links
                  </h4>
                  <div className="space-y-1">
                    {linkFields.map((lf) => {
                      return (
                        <div key={lf.fieldName} className="text-sm">
                          <a
                            href={lf.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-600 dark:text-blue-400 hover:underline"
                            title={lf.url}
                          >
                            {lf.fieldName}
                          </a>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Summary */}
              {task.summary && (
                <div>
                  <h4 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-1">
                    Summary
                  </h4>
                  <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">
                    {task.summary}
                  </p>
                </div>
              )}

              {/* Description */}
              {task.description && (
                <div>
                  <h4 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-1">
                    Description
                  </h4>
                  <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">
                    {task.description}
                  </p>
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer  -  edit actions */}
        {isEditing && (
          <div className="px-6 py-3 border-t border-gray-200 dark:border-gray-700 flex items-center justify-end gap-2">
            <button
              onClick={() => {
                setIsEditing(false);
                setEditName(task.name);
                setEditStart(isoToDateTimeLocal(task.start));
                setEditEnd(isoToDateTimeLocal(task.end));
                setEditSummary(task.summary || "");
                setEditDescription(task.description || "");
                setEditLocationName(task.location_name || "");
                setEditLocationAddress(task.location_address || "");
                setEditColor(task.color || "#6B7280");
                setEditAttendees([...task.attendees]);
                setEditFieldAssignments(buildFieldAssignmentRecord(task));
                setEditFieldValues({ ...(task.field_values || {}) });
                setError("");
              }}
              className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
            >
              Cancel
            </button>
            <button
              onClick={handleSaveDraft}
              className="px-3 py-1.5 text-sm rounded-lg text-white"
              style={{ backgroundColor: color }}
            >
              Save Draft
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

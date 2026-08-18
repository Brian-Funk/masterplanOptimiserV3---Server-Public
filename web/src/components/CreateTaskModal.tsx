"use client";

import React, { useState } from "react";
import { X } from "lucide-react";
import { TASK_COLORS, ALERT_COLOR } from "@/lib/colors";
import { PermittedDataInputNotice } from "@/components/PermittedDataInputNotice";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
/** Person assignment stored on a draft task. */
export interface Attendee {
  name: string;
  person_id: number;
}

/** Person option available when creating a draft task. */
export interface Person {
  id: number;
  external_person_id: number;
  first_name: string;
  last_name: string;
}

/** Draft task created in the web calendar before being committed. */
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

/** Props for `CreateTaskModal`. */
export interface CreateTaskModalProps {
  persons: Person[];
  defaultDate: string; // YYYY-MM-DD
  onAdd: (task: DraftNewTask) => void;
  onClose: () => void;
  nextTempId: number;
  dataPolicyAcknowledged: boolean;
  dataPolicyVersion?: number | null;
  dataPolicySha256?: string | null;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
/**
 * Modal for creating a new draft calendar task.
 */
export function CreateTaskModal({
  persons,
  defaultDate,
  onAdd,
  onClose,
  nextTempId,
  dataPolicyAcknowledged,
  dataPolicyVersion,
  dataPolicySha256,
}: CreateTaskModalProps) {
  const [name, setName] = useState("");
  const [start, setStart] = useState(`${defaultDate}T09:00`);
  const [end, setEnd] = useState(`${defaultDate}T10:00`);
  const [summary, setSummary] = useState("");
  const [description, setDescription] = useState("");
  const [locationName, setLocationName] = useState("");
  const [locationAddress, setLocationAddress] = useState("");
  const [color, setColor] = useState(TASK_COLORS[0].hex);
  const [attendees, setAttendees] = useState<Attendee[]>([]);

  const inputClass =
    "w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-sm";
  const labelClass =
    "block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1";

  const addAttendee = (personId: number) => {
    const person = persons.find((p) => p.external_person_id === personId);
    if (person) {
      setAttendees((prev) => [
        ...prev,
        {
          name: `${person.first_name} ${person.last_name}`,
          person_id: person.external_person_id,
        },
      ]);
    }
  };

  const removeAttendee = (index: number) => {
    setAttendees((prev) => prev.filter((_, i) => i !== index));
  };

  const handleAdd = () => {
    if (!name.trim()) return;
    const dtLocalToISO = (v: string) => (v.length === 16 ? v + ":00" : v);
    onAdd({
      tempId: nextTempId,
      name: name.trim(),
      start: dtLocalToISO(start),
      end: dtLocalToISO(end),
      summary: summary || undefined,
      description: description || undefined,
      location_name: locationName || undefined,
      location_address: locationAddress || undefined,
      color: color || undefined,
      attendees: attendees.length > 0 ? attendees : undefined,
    });
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 sm:items-center sm:p-4"
      onClick={onClose}
    >
      <div
        className="flex h-[100dvh] w-full flex-col overflow-hidden bg-white shadow-2xl dark:bg-gray-800 sm:h-auto sm:max-h-[90vh] sm:max-w-lg sm:rounded-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-6 py-4 flex items-center justify-between bg-blue-600">
          <h2 className="text-lg font-bold text-white">New Task</h2>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg bg-white/20 hover:bg-white/30 text-white transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {/* Form */}
        <div className="px-6 py-4 space-y-3 max-h-[60vh] overflow-y-auto">
          <PermittedDataInputNotice acknowledged={dataPolicyAcknowledged} version={dataPolicyVersion} sha256={dataPolicySha256} />
          {/* Name */}
          <div>
            <label className={labelClass}>
              Task name <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Operational task name"
              className={inputClass}
              autoFocus
            />
          </div>

          {/* Start / End */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label className={labelClass}>Start</label>
              <input
                type="datetime-local"
                value={start}
                onChange={(e) => setStart(e.target.value)}
                className={inputClass}
              />
            </div>
            <div>
              <label className={labelClass}>End</label>
              <input
                type="datetime-local"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
                className={inputClass}
              />
            </div>
          </div>

          {/* Location */}
          <div>
            <label className={labelClass}>Operational location</label>
            <input
              type="text"
              value={locationName}
              onChange={(e) => setLocationName(e.target.value)}
              placeholder="Operational location name"
              className={inputClass}
            />
            <input
              type="text"
              value={locationAddress}
              onChange={(e) => setLocationAddress(e.target.value)}
              placeholder="Location details"
              className={`${inputClass} mt-2`}
            />
          </div>

          {/* Summary */}
          <div>
            <label className={labelClass}>Schedule summary</label>
            <input
              type="text"
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              className={inputClass}
            />
          </div>

          {/* Description */}
          <div>
            <label className={labelClass}>Operational instruction</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className={`${inputClass} resize-y`}
            />
          </div>

          {/* Colour */}
          <div>
            <label className={labelClass}>Colour</label>
            <div className="flex flex-wrap gap-2">
              {TASK_COLORS.map(({ hex, label }) => (
                <button
                  key={hex}
                  type="button"
                  onClick={() => setColor(hex)}
                  title={label}
                  className={`w-8 h-8 rounded-full border-2 transition-all ${
                    color.toLowerCase() === hex.toLowerCase()
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
                onClick={() => setColor(ALERT_COLOR.hex)}
                title={ALERT_COLOR.label}
                className={`w-8 h-8 rounded-full border-2 transition-all ${
                  color.toLowerCase() === ALERT_COLOR.hex.toLowerCase()
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
            <label className={labelClass}>Attendees</label>
            {attendees.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mb-2">
                {attendees.map((a, i) => (
                  <span
                    key={`${a.person_id}-${i}`}
                    className="inline-flex items-center gap-1 bg-gray-100 dark:bg-gray-700 px-2 py-0.5 rounded text-sm text-gray-900 dark:text-gray-100"
                  >
                    {a.name}
                    <button
                      onClick={() => removeAttendee(i)}
                      className="text-gray-400 hover:text-red-500 transition-colors"
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
              className={inputClass}
            >
              <option value="">Add person...</option>
              {persons
                .filter(
                  (p) =>
                    !attendees.some(
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
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-gray-200 dark:border-gray-700 flex items-center justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
          >
            Cancel
          </button>
          <button
            onClick={handleAdd}
            disabled={!name.trim()}
            className="px-3 py-1.5 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
          >
            Add to Draft
          </button>
        </div>
      </div>
    </div>
  );
}

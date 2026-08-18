"use client";

import React, { useState, useEffect, useMemo } from "react";
import { PencilLine } from "lucide-react";
import { layoutCalendarIntervals } from "@/lib/calendarIntervalLayout";
import { describeWebEditTask } from "@/lib/webEditConfidence";
import {
  taskAllocations,
  taskOperationalFields,
} from "@/lib/taskPresentation";
import {
  currentWorkingDate,
  currentWorkingMinute,
  DEFAULT_SCHEDULE_DAY_RANGE,
  formatWorkingHour,
  normaliseScheduleDayRange,
  ScheduleDayRange,
  toWorkingDayMinutes,
  workingDateForDateTime,
} from "@/lib/scheduleDays";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
/** Person assigned to a task in the public calendar view. */
export interface Attendee {
  name: string;
  person_id: number;
}

/** Calendar task with schedule, location, assignment, and custom field data. */
export interface Task {
  id: number;
  external_task_id: number;
  name: string;
  summary: string | null;
  description: string | null;
  start: string;
  end: string;
  working_date?: string;
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

/** Props for `CalendarGrid`. */
export interface CalendarGridProps {
  tasks: Task[];
  selectedDate: string;
  highlightedPersonId: number | null;
  highlightMode: "off" | "opacity" | "greyed-out" | "hatched";
  onTaskDoubleClick: (task: Task) => void;
  scheduleDayRange?: ScheduleDayRange;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const PX_PER_HOUR = 120;
const MIN_CARD_HEIGHT_PX = 24;
const MIN_CARD_DURATION_MINUTES = (MIN_CARD_HEIGHT_PX / PX_PER_HOUR) * 60;

function hexToRgba(hex: string, alpha: number): string {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!result) return `rgba(156, 163, 175, ${alpha})`;
  return `rgba(${parseInt(result[1], 16)}, ${parseInt(result[2], 16)}, ${parseInt(result[3], 16)}, ${alpha})`;
}

function isoToMinutes(iso: string, workingDate: string): number {
  const datePart = iso.split("T")[0];
  const timePart = iso.split("T")[1] || "00:00";
  return toWorkingDayMinutes(datePart, timePart, workingDate);
}

function taskMinutes(task: Task, workingDate: string): { start: number; end: number } {
  const start = isoToMinutes(task.start, workingDate);
  let end = isoToMinutes(task.end, workingDate);
  if (end <= start) end += 24 * 60;
  return { start, end };
}

function formatIsoTime(iso: string): string {
  const timePart = iso.split("T")[1] || "00:00";
  const [hours, minutes] = timePart.split(":").map(Number);
  return `${hours.toString().padStart(2, "0")}:${minutes.toString().padStart(2, "0")}`;
}

function taskHasPersonId(task: Task, personId: number): boolean {
  return task.attendees.some((a) => a.person_id === personId);
}

// ---------------------------------------------------------------------------
// CalendarGridCard (individual task block)
// ---------------------------------------------------------------------------
function CalendarGridCard({
  task,
  isHighlighted,
  highlightMode,
  onDoubleClick,
}: {
  task: Task;
  isHighlighted: boolean | null;
  highlightMode: "off" | "opacity" | "greyed-out" | "hatched";
  onDoubleClick: () => void;
}) {
  const color = task.color || "#6B7280";
  const backgroundColor = hexToRgba(color, 0.25);
  const borderColor = hexToRgba(color, 0.5);

  const isDimmed = isHighlighted === false;
  const isGreyed = isDimmed && highlightMode === "greyed-out";
  const isHatched = isDimmed && highlightMode === "hatched";

  const workingDate = task.working_date ?? task.start.split("T")[0];
  const startMin = isoToMinutes(task.start, workingDate);
  let endMin = isoToMinutes(task.end, workingDate);
  if (endMin <= startMin) endMin += 24 * 60;
  const durationMin = endMin - startMin;
  const isCompact = durationMin < 25;

  const startTime = formatIsoTime(task.start);
  const endTime = formatIsoTime(task.end);

  const allocations = taskAllocations(task);
  const operationalFields = taskOperationalFields(task);
  const locationValues = operationalFields
    .filter((field) => field.type === "location")
    .map((field) => field.value);
  const locationDisplay = locationValues.length > 0
    ? locationValues.join(" · ")
    : task.location_name || null;
  const webEditDescription = task.has_web_edit ? describeWebEditTask(task) : "";

  return (
    <div
      className={`absolute h-full w-full rounded border-2 border-solid cursor-pointer transition-opacity hover:opacity-90 overflow-hidden group relative ${
        isCompact ? "p-1" : "p-2"
      }`}
      style={{
        backgroundColor: isGreyed
          ? "rgba(156, 163, 175, 0.15)"
          : backgroundColor,
        borderColor: isGreyed
          ? "rgba(156, 163, 175, 0.35)"
          : isHatched
            ? "rgba(107, 114, 128, 0.5)"
            : borderColor,
        opacity: isDimmed && highlightMode === "opacity" ? 0.4 : undefined,
        filter: isGreyed ? "grayscale(1)" : undefined,
      }}
      role="button"
      tabIndex={0}
      aria-label={`Open ${task.name}`}
      onClick={onDoubleClick}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onDoubleClick();
        }
      }}
    >
      {task.has_web_edit && (
        <span
          className="absolute right-1.5 top-1.5 z-20 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-amber-500/10 text-amber-700 ring-1 ring-amber-500/20 dark:text-amber-300"
          title={webEditDescription}
          aria-label={webEditDescription}
        >
          <PencilLine className="h-2.5 w-2.5" />
        </span>
      )}
      {isHatched && (
        <div
          className="absolute inset-0 pointer-events-none z-10"
          style={{
            backgroundImage:
              "repeating-linear-gradient(135deg, transparent, transparent 4px, rgba(107,114,128,0.18) 4px, rgba(107,114,128,0.18) 6px)",
          }}
        />
      )}
      {isCompact ? (
        <div className="flex items-center gap-1 overflow-hidden">
          <span className="text-xs font-medium truncate" style={{ color }}>
            {task.name}
          </span>
          {locationDisplay && (
            <span className="text-xs text-gray-500 dark:text-gray-400 italic truncate">
              {locationDisplay}
            </span>
          )}
        </div>
      ) : (
        <div className="overflow-hidden h-full">
          {/* Task name + location */}
          <div className="flex items-center gap-1 overflow-hidden">
            <span
              className="font-semibold text-sm flex-shrink-0"
              style={{ color }}
            >
              {task.name}
            </span>
            {locationDisplay && (
              <span className="text-xs text-gray-500 dark:text-gray-400 italic truncate">
                {locationDisplay}
              </span>
            )}
          </div>

          {/* Time range */}
          <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            {startTime} - {endTime}
          </div>

          {/* Assigned persons */}
          {allocations.length > 0 ? (
            <div className="text-xs text-gray-600 dark:text-gray-300 mt-0.5 space-y-0.5 overflow-hidden">
              {allocations.map((allocation) => (
                <div key={allocation.fieldId ?? "legacy"} className="truncate">
                  {allocation.label && (
                    <span className="font-semibold text-gray-400 dark:text-gray-500">
                      {allocation.label}:{" "}
                    </span>
                  )}
                  {allocation.attendees.map((attendee) => attendee.name).join(", ")}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      )}

      {/* Hover tooltip */}
      <div
        className="invisible group-hover:visible absolute z-[200] left-0 top-full mt-1 text-white text-xs rounded-lg shadow-xl p-3 min-w-[200px] max-w-[300px] border-2"
        style={{ backgroundColor: color, borderColor: color }}
      >
        <div className="space-y-1">
          <div className="font-semibold">{task.name}</div>
          {task.has_web_edit && (
            <div className="text-[11px] opacity-85">{webEditDescription}</div>
          )}
          <div>
            {startTime} - {endTime}
          </div>
          {locationDisplay && <div>📍 {locationDisplay}</div>}
          {/* Assigned */}
          {allocations.map((allocation) => (
            <div key={allocation.fieldId ?? "legacy"}>
              {allocation.label && (
                <span className="opacity-70">{allocation.label}: </span>
              )}
              {allocation.attendees.map((attendee) => attendee.name).join(", ")}
            </div>
          ))}
          {operationalFields.map((field) => (
            <div key={field.fieldId} className="whitespace-pre-wrap opacity-80">
              <span className="opacity-70">{field.label}: </span>
              {field.value}
            </div>
          ))}
          {task.summary && (
            <div className="opacity-80 mt-1">{task.summary}</div>
          )}
          <div className="opacity-60 mt-1 text-[10px]">
            Tap for details
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CalendarGrid (main component)
// ---------------------------------------------------------------------------
/**
 * Render the day calendar grid with task blocks and person highlighting.
 */
export function CalendarGrid({
  tasks,
  selectedDate,
  highlightedPersonId,
  highlightMode,
  onTaskDoubleClick,
  scheduleDayRange: scheduleDayRangeValue = DEFAULT_SCHEDULE_DAY_RANGE,
}: CalendarGridProps) {
  const scheduleDayRange = useMemo(
    () => normaliseScheduleDayRange(scheduleDayRangeValue),
    [scheduleDayRangeValue],
  );
  const [startHour, setStartHour] = useState(scheduleDayRange.start_hour);
  const [endHour, setEndHour] = useState(scheduleDayRange.end_hour);

  useEffect(() => {
    setStartHour(scheduleDayRange.start_hour);
    setEndHour(scheduleDayRange.end_hour);
  }, [scheduleDayRange.start_hour, scheduleDayRange.end_hour]);

  const hours = useMemo(
    () => Array.from({ length: endHour - startHour }, (_, i) => i + startHour),
    [startHour, endHour],
  );

  // Filter tasks for the selected date
  const tasksForDate = useMemo(() => {
    return tasks.filter((task) => (
      task.working_date ?? workingDateForDateTime(task.start, scheduleDayRange)
    ) === selectedDate);
  }, [scheduleDayRange, tasks, selectedDate]);

  // --- Position & overlap layout ---
  const getTaskPosition = (task: Task) => {
    const { start: startMin, end: endMin } = taskMinutes(task, selectedDate);
    const startOfDay = startHour * 60;
    const top = ((startMin - startOfDay) / 60) * PX_PER_HOUR;
    const height = Math.max(
      ((endMin - startMin) / 60) * PX_PER_HOUR,
      MIN_CARD_HEIGHT_PX,
    );
    return { top, height, start: startMin, end: endMin };
  };

  const taskLayout = useMemo(
    () =>
      layoutCalendarIntervals(
        tasksForDate.map((task) => ({
          id: task.id,
          ...taskMinutes(task, selectedDate),
        })),
        MIN_CARD_DURATION_MINUTES,
      ),
    [selectedDate, tasksForDate],
  );

  // --- Auto-fit / Reset ---
  const handleAutoFit = () => {
    let minMin = Infinity;
    let maxMin = -Infinity;
    tasksForDate.forEach((t) => {
      const { start, end } = taskMinutes(t, selectedDate);
      minMin = Math.min(minMin, start);
      maxMin = Math.max(maxMin, end);
    });
    if (minMin === Infinity) return;
    setStartHour(Math.max(0, Math.floor(minMin / 60) - 1));
    setEndHour(Math.min(36, Math.ceil(maxMin / 60) + 1));
  };

  const handleResetZoom = () => {
    setStartHour(scheduleDayRange.start_hour);
    setEndHour(scheduleDayRange.end_hour);
  };

  const isZoomed =
    startHour !== scheduleDayRange.start_hour || endHour !== scheduleDayRange.end_hour;

  // --- Current time indicator ---
  const isToday = selectedDate === currentWorkingDate(scheduleDayRange);
  const [nowMinutes, setNowMinutes] = useState(() => currentWorkingMinute(scheduleDayRange));

  useEffect(() => {
    if (!isToday) return;
    const interval = setInterval(() => {
      setNowMinutes(currentWorkingMinute(scheduleDayRange));
    }, 30_000); // update every 30 seconds
    return () => clearInterval(interval);
  }, [isToday, scheduleDayRange]);

  const nowTop = isToday
    ? ((nowMinutes - startHour * 60) / 60) * PX_PER_HOUR
    : null;

  return (
    <div className="overflow-x-auto overscroll-x-contain">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-2 py-1.5 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
        <button
          onClick={handleAutoFit}
          disabled={tasksForDate.length === 0}
          className="px-2.5 py-1 text-xs font-medium rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed"
          title="Fit timeline to task range"
        >
          Auto-Fit
        </button>
        {isZoomed && (
          <button
            onClick={handleResetZoom}
            className="px-2.5 py-1 text-xs font-medium rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700"
            title="Reset to full day view"
          >
            Reset
          </button>
        )}
        {isZoomed && (
          <span className="text-xs text-gray-500 dark:text-gray-400">
            {formatWorkingHour(startHour)} - {formatWorkingHour(endHour)}
          </span>
        )}
      </div>

      <div className="flex min-w-max">
        {/* Time column */}
        <div className="sticky left-0 z-20 w-8 flex-shrink-0 border-r border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-800/95">
          {hours.map((hour) => (
            <div
              key={hour}
              className="h-[120px] border-b border-gray-200 dark:border-gray-700 text-xs text-gray-400 dark:text-gray-500 relative"
            >
              <span
                className="absolute top-1 left-1/2 -translate-x-1/2 whitespace-nowrap"
                style={{
                  writingMode: "vertical-rl",
                  transform: "translateX(-50%) rotate(180deg)",
                  fontSize: "10px",
                }}
              >
                {formatWorkingHour(hour)}
              </span>
            </div>
          ))}
        </div>

        {/* Task column */}
        <div className="min-w-[280px] flex-1 sm:min-w-[300px]">
          {/* Time grid */}
          <div
            className="relative"
            style={{ height: `${hours.length * PX_PER_HOUR}px` }}
          >
            {/* Hour lines */}
            {hours.map((_, index) => (
              <React.Fragment key={index}>
                <div
                  className="absolute w-full h-[60px] border-b border-gray-100 dark:border-gray-800"
                  style={{ top: `${index * PX_PER_HOUR}px` }}
                />
                <div
                  className="absolute w-full h-[60px] border-b border-gray-200 dark:border-gray-700"
                  style={{ top: `${index * PX_PER_HOUR + 60}px` }}
                />
              </React.Fragment>
            ))}

            {/* Task blocks */}
            {tasksForDate.map((task) => {
              const pos = getTaskPosition(task);
              const li = taskLayout.get(task.id);
              const leftPct = li?.leftPercentage ?? 0;
              const widthPct = li?.widthPercentage ?? 100;
              const isFirstLane = (li?.laneIndex ?? 0) === 0;
              const reachesLastLane = li
                ? li.laneIndex + li.laneSpan === li.laneCount
                : true;

              // null = show all normally, true = matches, false = dimmed
              const highlighted =
                highlightMode === "off" || highlightedPersonId === null
                  ? null
                  : taskHasPersonId(task, highlightedPersonId);

              return (
                <div
                  key={task.id}
                  className="absolute"
                  style={{
                    top: `${pos.top}px`,
                    height: `${pos.height}px`,
                    left: `${leftPct}%`,
                    width: `${widthPct}%`,
                    paddingLeft: isFirstLane ? "4px" : "2px",
                    paddingRight: reachesLastLane ? "4px" : "2px",
                  }}
                >
                  <CalendarGridCard
                    task={task}
                    isHighlighted={highlighted}
                    highlightMode={highlightMode}
                    onDoubleClick={() => onTaskDoubleClick(task)}
                  />
                </div>
              );
            })}

            {/* Current time indicator */}
            {nowTop !== null &&
              nowTop >= 0 &&
              nowTop <= hours.length * PX_PER_HOUR && (
                <div
                  className="absolute left-0 right-0 z-30 pointer-events-none"
                  style={{ top: `${nowTop}px` }}
                >
                  <div className="relative h-[10px] -translate-y-1/2">
                    <div className="absolute -left-1 top-1/2 w-[10px] h-[10px] -translate-y-1/2 rounded-full bg-red-500" />
                    <div className="absolute left-0 top-1/2 h-[2px] w-full -translate-y-1/2 bg-red-500" />
                  </div>
                </div>
              )}
          </div>
        </div>
      </div>
    </div>
  );
}

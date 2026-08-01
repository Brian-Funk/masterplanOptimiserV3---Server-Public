"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { UserMinus, X } from "lucide-react";

/** Person name used by the daily unavailability indicator. */
export interface AvailabilityPerson {
  external_person_id: number;
  first_name: string;
  last_name: string;
}

/** Exact unavailability interval published for one working day. */
export interface PublishedUnavailability {
  person_id: number;
  working_date: string;
  start: string;
  end: string;
}

/** Props for the compact daily unavailability indicator. */
export interface DailyUnavailabilityIndicatorProps {
  people: AvailabilityPerson[];
  intervals: PublishedUnavailability[];
  selectedDate: string;
  variant?: "compact" | "touch";
}

interface MergedInterval {
  start: string;
  end: string;
  startTimestamp: number;
  endTimestamp: number;
}

function mergeIntervals(intervals: PublishedUnavailability[]): MergedInterval[] {
  const sorted = intervals
    .map((interval) => ({
      start: interval.start,
      end: interval.end,
      startTimestamp: new Date(interval.start).getTime(),
      endTimestamp: new Date(interval.end).getTime(),
    }))
    .filter((interval) => (
      !Number.isNaN(interval.startTimestamp)
      && !Number.isNaN(interval.endTimestamp)
      && interval.endTimestamp > interval.startTimestamp
    ))
    .sort((a, b) => a.startTimestamp - b.startTimestamp);
  const merged: MergedInterval[] = [];
  for (const interval of sorted) {
    const previous = merged[merged.length - 1];
    if (previous && interval.startTimestamp <= previous.endTimestamp) {
      if (interval.endTimestamp > previous.endTimestamp) {
        previous.end = interval.end;
        previous.endTimestamp = interval.endTimestamp;
      }
    } else {
      merged.push({ ...interval });
    }
  }
  return merged;
}

function formatClock(value: string): string {
  const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})/.exec(value);
  if (!match) return value;
  return `${match[2]}:${match[3]}`;
}

function coversWholeDay(interval: MergedInterval, selectedDate: string): boolean {
  const dayStart = new Date(`${selectedDate}T00:00:00`);
  if (Number.isNaN(dayStart.getTime())) return false;
  const dayEnd = new Date(dayStart);
  dayEnd.setDate(dayEnd.getDate() + 1);
  return interval.startTimestamp <= dayStart.getTime() && interval.endTimestamp >= dayEnd.getTime();
}

function formatInterval(interval: MergedInterval, selectedDate: string): string {
  if (coversWholeDay(interval, selectedDate)) return "Whole day";
  return `${formatClock(interval.start)} - ${formatClock(interval.end)}`;
}

/** Render a compact entry point for exact person unavailability on one working day. */
export function DailyUnavailabilityIndicator({
  people,
  intervals,
  selectedDate,
  variant = "compact",
}: DailyUnavailabilityIndicatorProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const peopleById = useMemo(
    () => new Map(people.map((person) => [person.external_person_id, person])),
    [people],
  );
  const details = useMemo(() => {
    const grouped = new Map<number, PublishedUnavailability[]>();
    for (const interval of intervals) {
      if (interval.working_date !== selectedDate || !peopleById.has(interval.person_id)) continue;
      grouped.set(interval.person_id, [...(grouped.get(interval.person_id) ?? []), interval]);
    }
    return Array.from(grouped.entries())
      .map(([personId, personIntervals]) => {
        const person = peopleById.get(personId)!;
        return {
          personId,
          name: `${person.first_name} ${person.last_name}`.trim(),
          intervals: mergeIntervals(personIntervals),
        };
      })
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [intervals, peopleById, selectedDate]);

  const close = useCallback(() => {
    setOpen(false);
    window.setTimeout(() => triggerRef.current?.focus(), 0);
  }, []);

  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [close, open]);

  if (details.length === 0) return null;
  const label = `${details.length} ${details.length === 1 ? "person" : "people"} unavailable`;

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(true)}
        className={`inline-flex items-center justify-center gap-1 rounded-lg border border-amber-200/70 bg-white text-xs font-medium text-amber-800 shadow-sm hover:bg-amber-50 dark:border-amber-900/50 dark:bg-gray-800 dark:text-amber-200 dark:hover:bg-amber-950/20 ${
          variant === "touch" ? "h-11 min-w-11 px-2.5" : "h-8 px-2"
        }`}
        aria-label={`${label} on the selected day`}
        title={label}
      >
        <UserMinus className="h-3.5 w-3.5" />
        <span>{details.length}</span>
      </button>
      {open && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
          onMouseDown={(event) => event.target === event.currentTarget && close()}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="daily-unavailability-title"
            className="flex max-h-[80vh] w-full max-w-sm flex-col overflow-hidden rounded-xl bg-white shadow-2xl dark:bg-gray-800"
          >
            <header className="flex items-center justify-between border-b border-gray-200 px-4 py-3 dark:border-gray-700">
              <div>
                <h2 id="daily-unavailability-title" className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                  Unavailable on this working day
                </h2>
                <p className="text-xs text-gray-500 dark:text-gray-400">{label}</p>
              </div>
              <button
                ref={closeRef}
                type="button"
                onClick={close}
                className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-700 dark:hover:text-gray-200"
                aria-label="Close unavailability details"
              >
                <X className="h-4 w-4" />
              </button>
            </header>
            <div className="overflow-y-auto px-4 py-3">
              <ul className="space-y-3">
                {details.map((detail) => (
                  <li key={detail.personId}>
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{detail.name}</p>
                    <ul className="mt-1 space-y-0.5 text-xs text-gray-600 dark:text-gray-300">
                      {detail.intervals.map((interval, index) => (
                        <li key={`${interval.start}-${index}`}>{formatInterval(interval, selectedDate)}</li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            </div>
          </section>
        </div>
      )}
    </>
  );
}

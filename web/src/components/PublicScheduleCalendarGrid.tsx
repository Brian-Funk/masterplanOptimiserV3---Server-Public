"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  CalendarDays,
  Clock3,
  MapPin,
  Tag,
  UserRound,
  Users,
  X,
} from "lucide-react";
import { layoutCalendarIntervals } from "@/lib/calendarIntervalLayout";
import {
  currentWorkingDate,
  currentWorkingMinute,
  DEFAULT_SCHEDULE_DAY_RANGE,
  formatWorkingHour,
  normaliseScheduleDayRange,
  ScheduleDayRange,
  toWorkingDayEndMinutes,
  toWorkingDayMinutes,
} from "@/lib/scheduleDays";

/** Audience label attached to a public schedule item. */
export interface PublicScheduleAudience {
  name?: string | null;
  short_name?: string | null;
}

/** Public schedule item rendered in the participant calendar. */
export interface PublicScheduleCalendarItem {
  id: number;
  title: string;
  date: string;
  working_date?: string;
  start_time: string;
  end_time: string;
  location_name: string | null;
  location_address?: string | null;
  responsible?: string | null;
  audience_teams: PublicScheduleAudience[];
  description: string | null;
  type_name: string | null;
  colour: string | null;
  sort_order: number;
}

/** Props for the public schedule timeline grid. */
export interface PublicScheduleCalendarGridProps {
  items: PublicScheduleCalendarItem[];
  selectedDate: string;
  scheduleDayRange?: ScheduleDayRange;
}

const PX_PER_HOUR = 120;
const MIN_CARD_HEIGHT_PX = 24;
const MIN_CARD_DURATION_MINUTES = (MIN_CARD_HEIGHT_PX / PX_PER_HOUR) * 60;

function hexToRgba(hex: string, alpha: number): string {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!result) return `rgba(125, 211, 252, ${alpha})`;
  return `rgba(${parseInt(result[1], 16)}, ${parseInt(result[2], 16)}, ${parseInt(result[3], 16)}, ${alpha})`;
}

function timeToMinutes(time: string): number {
  const [hours, minutes] = time.split(":").map(Number);
  return (hours || 0) * 60 + (minutes || 0);
}

function itemAudience(item: PublicScheduleCalendarItem): string {
  return item.audience_teams
    .map((team) => team.short_name || team.name)
    .filter(Boolean)
    .join(", ");
}

function optionalText(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed || null;
}

function formatItemDate(value: string): string {
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString(undefined, {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

function PublicScheduleDetailModal({
  item,
  onClose,
}: {
  item: PublicScheduleCalendarItem;
  onClose: () => void;
}) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const audience = itemAudience(item);
  const typeName = optionalText(item.type_name);
  const locationName = optionalText(item.location_name);
  const locationAddress = optionalText(item.location_address);
  const location = [locationName, locationAddress].filter(Boolean).join(" - ");
  const responsible = optionalText(item.responsible);
  const description = optionalText(item.description);

  useEffect(() => {
    closeButtonRef.current?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab") {
        event.preventDefault();
        closeButtonRef.current?.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[300] flex items-center justify-center bg-black/50 px-4 py-6"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-lg border border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-900"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`public-schedule-detail-${item.id}`}
      >
        <div className="flex items-start justify-between gap-4 border-b border-gray-200 px-5 py-4 dark:border-gray-700">
          <div className="min-w-0">
            <h2
              id={`public-schedule-detail-${item.id}`}
              className="text-lg font-semibold text-gray-900 dark:text-gray-100"
            >
              {item.title}
            </h2>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            className="rounded p-1.5 text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800"
            aria-label="Close schedule details"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="overflow-y-auto px-5 py-4">
          <dl className="space-y-3 text-sm">
            <div className="flex items-start gap-3">
              <CalendarDays className="mt-0.5 h-4 w-4 shrink-0 text-gray-400" />
              <div>
                <dt className="font-medium text-gray-700 dark:text-gray-300">Date</dt>
                <dd className="text-gray-600 dark:text-gray-400">
                  {formatItemDate(item.date)}
                </dd>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <Clock3 className="mt-0.5 h-4 w-4 shrink-0 text-gray-400" />
              <div>
                <dt className="font-medium text-gray-700 dark:text-gray-300">Time</dt>
                <dd className="text-gray-600 dark:text-gray-400">
                  {item.start_time} - {item.end_time}
                </dd>
              </div>
            </div>
            {typeName && (
              <div className="flex items-start gap-3">
                <Tag className="mt-0.5 h-4 w-4 shrink-0 text-gray-400" />
                <div>
                  <dt className="font-medium text-gray-700 dark:text-gray-300">Type</dt>
                  <dd className="text-gray-600 dark:text-gray-400">{typeName}</dd>
                </div>
              </div>
            )}
            {location && (
              <div className="flex items-start gap-3">
                <MapPin className="mt-0.5 h-4 w-4 shrink-0 text-gray-400" />
                <div>
                  <dt className="font-medium text-gray-700 dark:text-gray-300">Location</dt>
                  <dd className="text-gray-600 dark:text-gray-400">{location}</dd>
                </div>
              </div>
            )}
            {responsible && (
              <div className="flex items-start gap-3">
                <UserRound className="mt-0.5 h-4 w-4 shrink-0 text-gray-400" />
                <div>
                  <dt className="font-medium text-gray-700 dark:text-gray-300">
                    Responsible
                  </dt>
                  <dd className="text-gray-600 dark:text-gray-400">{responsible}</dd>
                </div>
              </div>
            )}
            {audience && (
              <div className="flex items-start gap-3">
                <Users className="mt-0.5 h-4 w-4 shrink-0 text-gray-400" />
                <div>
                  <dt className="font-medium text-gray-700 dark:text-gray-300">Audience</dt>
                  <dd className="text-gray-600 dark:text-gray-400">{audience}</dd>
                </div>
              </div>
            )}
            {description && (
              <div className="border-t border-gray-200 pt-3 dark:border-gray-700">
                <dt className="font-medium text-gray-700 dark:text-gray-300">
                  Description
                </dt>
                <dd className="mt-1 whitespace-pre-wrap text-gray-600 dark:text-gray-400">
                  {description}
                </dd>
              </div>
            )}
          </dl>
        </div>
      </div>
    </div>
  );
}

function PublicScheduleGridCard({
  item,
  onOpen,
}: {
  item: PublicScheduleCalendarItem;
  onOpen: (trigger: HTMLButtonElement) => void;
}) {
  const colour = item.colour || "#7dd3fc";
  const audience = itemAudience(item);
  const durationMin = timeToMinutes(item.end_time) - timeToMinutes(item.start_time);
  const compact = durationMin < 25;
  const touchStartRef = useRef<{ x: number; y: number } | null>(null);

  return (
    <button
      type="button"
      className={`relative h-full w-full cursor-pointer overflow-hidden rounded border-2 border-solid text-left focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1 ${compact ? "p-1" : "p-2"}`}
      style={{
        backgroundColor: hexToRgba(colour, 0.22),
        borderColor: hexToRgba(colour, 0.55),
      }}
      aria-label={`View details for ${item.title}`}
      onDoubleClick={(event) => onOpen(event.currentTarget)}
      onPointerDown={(event) => {
        if (event.pointerType !== "mouse") {
          touchStartRef.current = { x: event.clientX, y: event.clientY };
        }
      }}
      onPointerUp={(event) => {
        const start = touchStartRef.current;
        touchStartRef.current = null;
        if (
          event.pointerType !== "mouse" &&
          start &&
          Math.hypot(event.clientX - start.x, event.clientY - start.y) <= 8
        ) {
          onOpen(event.currentTarget);
        }
      }}
      onPointerCancel={() => {
        touchStartRef.current = null;
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen(event.currentTarget);
        }
      }}
    >
      <div className="h-full overflow-hidden">
        <div className="flex items-center gap-1 overflow-hidden">
          <span className="truncate text-sm font-semibold" style={{ color: colour }}>
            {item.title}
          </span>
          {item.location_name && (
            <span className="truncate text-xs italic text-gray-500 dark:text-gray-400">
              {item.location_name}
            </span>
          )}
        </div>
        {!compact && (
          <>
            <div className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
              {item.start_time} - {item.end_time}
            </div>
            {audience && (
              <div className="mt-0.5 truncate text-xs text-gray-600 dark:text-gray-300">
                {audience}
              </div>
            )}
          </>
        )}
      </div>

    </button>
  );
}

/** Render a day timeline for public schedule items. */
export function PublicScheduleCalendarGrid({
  items,
  selectedDate,
  scheduleDayRange: scheduleDayRangeValue = DEFAULT_SCHEDULE_DAY_RANGE,
}: PublicScheduleCalendarGridProps) {
  const scheduleDayRange = useMemo(
    () => normaliseScheduleDayRange(scheduleDayRangeValue),
    [scheduleDayRangeValue],
  );
  const [startHour, setStartHour] = useState(scheduleDayRange.start_hour);
  const [endHour, setEndHour] = useState(scheduleDayRange.end_hour);
  const [selectedItem, setSelectedItem] =
    useState<PublicScheduleCalendarItem | null>(null);
  const detailTriggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    setStartHour(scheduleDayRange.start_hour);
    setEndHour(scheduleDayRange.end_hour);
  }, [scheduleDayRange.start_hour, scheduleDayRange.end_hour]);

  const openDetails = useCallback(
    (item: PublicScheduleCalendarItem, trigger: HTMLButtonElement) => {
      detailTriggerRef.current = trigger;
      setSelectedItem(item);
    },
    [],
  );

  const closeDetails = useCallback(() => {
    setSelectedItem(null);
    window.setTimeout(() => detailTriggerRef.current?.focus(), 0);
  }, []);

  const itemsForDate = useMemo(
    () => items.filter((item) => (item.working_date ?? item.date) === selectedDate),
    [items, selectedDate],
  );
  const hours = useMemo(
    () => Array.from({ length: endHour - startHour }, (_, i) => i + startHour),
    [startHour, endHour],
  );

  const getItemPosition = (item: PublicScheduleCalendarItem) => {
    const startMin = toWorkingDayMinutes(item.date, item.start_time, selectedDate);
    const endMin = toWorkingDayEndMinutes(item.date, item.start_time, item.end_time, selectedDate);
    const top = ((startMin - startHour * 60) / 60) * PX_PER_HOUR;
    const height = Math.max(
      ((endMin - startMin) / 60) * PX_PER_HOUR,
      MIN_CARD_HEIGHT_PX,
    );
    return { top, height, start: startMin, end: endMin };
  };

  const itemLayout = useMemo(
    () =>
      layoutCalendarIntervals(
        itemsForDate.map((item) => ({
          id: item.id,
          start: toWorkingDayMinutes(item.date, item.start_time, selectedDate),
          end: toWorkingDayEndMinutes(item.date, item.start_time, item.end_time, selectedDate),
        })),
        MIN_CARD_DURATION_MINUTES,
      ),
    [itemsForDate, selectedDate],
  );

  const handleAutoFit = () => {
    let minMin = Infinity;
    let maxMin = -Infinity;
    itemsForDate.forEach((item) => {
      minMin = Math.min(minMin, toWorkingDayMinutes(item.date, item.start_time, selectedDate));
      maxMin = Math.max(maxMin, toWorkingDayEndMinutes(item.date, item.start_time, item.end_time, selectedDate));
    });
    if (minMin === Infinity) return;
    setStartHour(Math.max(0, Math.floor(minMin / 60) - 1));
    setEndHour(Math.min(36, Math.ceil(maxMin / 60) + 1));
  };

  const handleResetZoom = () => {
    setStartHour(scheduleDayRange.start_hour);
    setEndHour(scheduleDayRange.end_hour);
  };

  const isZoomed = startHour !== scheduleDayRange.start_hour || endHour !== scheduleDayRange.end_hour;
  const isToday = selectedDate === currentWorkingDate(scheduleDayRange);
  const [nowMinutes, setNowMinutes] = useState(() => currentWorkingMinute(scheduleDayRange));

  useEffect(() => {
    if (!isToday) return;
    const interval = setInterval(() => {
      setNowMinutes(currentWorkingMinute(scheduleDayRange));
    }, 30_000);
    return () => clearInterval(interval);
  }, [isToday, scheduleDayRange]);

  const nowTop = isToday
    ? ((nowMinutes - startHour * 60) / 60) * PX_PER_HOUR
    : null;

  return (
    <div className="overflow-x-auto">
      <div className="flex items-center gap-2 border-b border-gray-200 bg-gray-50 px-2 py-1.5 dark:border-gray-700 dark:bg-gray-800/50">
        <button
          onClick={handleAutoFit}
          disabled={itemsForDate.length === 0}
          className="rounded border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-gray-600 dark:bg-gray-800 dark:hover:bg-gray-700"
          title="Fit timeline to schedule range"
        >
          Auto-Fit
        </button>
        {isZoomed && (
          <button
            onClick={handleResetZoom}
            className="rounded border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium hover:bg-gray-100 dark:border-gray-600 dark:bg-gray-800 dark:hover:bg-gray-700"
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
        <div className="w-8 flex-shrink-0 border-r border-gray-200 dark:border-gray-700">
          {hours.map((hour) => (
            <div
              key={hour}
              className="relative h-[120px] border-b border-gray-200 text-xs text-gray-400 dark:border-gray-700 dark:text-gray-500"
            >
              <span
                className="absolute left-1/2 top-1 -translate-x-1/2 whitespace-nowrap"
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

        <div className="min-w-[300px] flex-1">
          <div className="relative" style={{ height: `${hours.length * PX_PER_HOUR}px` }}>
            {hours.map((_, index) => (
              <React.Fragment key={index}>
                <div
                  className="absolute h-[60px] w-full border-b border-gray-100 dark:border-gray-800"
                  style={{ top: `${index * PX_PER_HOUR}px` }}
                />
                <div
                  className="absolute h-[60px] w-full border-b border-gray-200 dark:border-gray-700"
                  style={{ top: `${index * PX_PER_HOUR + 60}px` }}
                />
              </React.Fragment>
            ))}

            {itemsForDate.map((item) => {
              const pos = getItemPosition(item);
              const layout = itemLayout.get(item.id);
              const leftPct = layout?.leftPercentage ?? 0;
              const widthPct = layout?.widthPercentage ?? 100;
              const isFirstLane = (layout?.laneIndex ?? 0) === 0;
              const reachesLastLane = layout
                ? layout.laneIndex + layout.laneSpan === layout.laneCount
                : true;

              return (
                <div
                  key={item.id}
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
                  <PublicScheduleGridCard
                    item={item}
                    onOpen={(trigger) => openDetails(item, trigger)}
                  />
                </div>
              );
            })}

            {nowTop !== null &&
              nowTop >= 0 &&
              nowTop <= hours.length * PX_PER_HOUR && (
                <div
                  className="pointer-events-none absolute left-0 right-0 z-30"
                  style={{ top: `${nowTop}px` }}
                >
                  <div className="relative h-[10px] -translate-y-1/2">
                    <div className="absolute -left-1 top-1/2 h-[10px] w-[10px] -translate-y-1/2 rounded-full bg-red-500" />
                    <div className="absolute left-0 top-1/2 h-[2px] w-full -translate-y-1/2 bg-red-500" />
                  </div>
                </div>
              )}
          </div>
        </div>
      </div>
      {selectedItem &&
        createPortal(
          <PublicScheduleDetailModal item={selectedItem} onClose={closeDetails} />,
          document.body,
        )}
    </div>
  );
}

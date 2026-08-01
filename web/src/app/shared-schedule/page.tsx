"use client";

import { useEffect, useMemo, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";

import { Logo } from "@/components/Logo";
import { PublicScheduleCalendarGrid } from "@/components/PublicScheduleCalendarGrid";
import { ThemeToggle } from "@/components/ThemeToggle";
import { ServiceStatusPanel } from "@/components/ServiceStatusPanel";
import { useServiceAvailability } from "@/contexts/ServiceAvailabilityContext";
import { getApiUrl } from "@/lib/environment";
import {
  captureRouteSecret,
  clearRouteSecret,
  isDefinitiveSecretRejection,
} from "@/lib/routeSecret";
import {
  currentWorkingDate,
  DEFAULT_SCHEDULE_DAY_RANGE,
  normaliseScheduleDayRange,
  type ScheduleDayRange,
} from "@/lib/scheduleDays";

interface SharedScheduleView {
  id: number;
  name: string;
  sort_order: number;
}

interface SharedScheduleItem {
  id: number;
  view_id: number;
  title: string;
  date: string;
  working_date?: string;
  start_time: string;
  end_time: string;
  location_name: string | null;
  location_address: string | null;
  responsible: string | null;
  audience_teams: Array<{
    name?: string | null;
    short_name?: string | null;
    colour?: string | null;
  }>;
  description: string | null;
  type_name: string | null;
  colour: string | null;
  sort_order: number;
}

interface SharedScheduleResponse {
  event: {
    name: string;
    start_date: string | null;
    end_date: string | null;
    day_aliases: Record<string, string> | null;
    schedule_day_range?: ScheduleDayRange;
  };
  views: SharedScheduleView[];
  items: SharedScheduleItem[];
}

function datesBetween(start: string | null, end: string | null): string[] {
  if (!start || !end || start > end) return [];
  const dates: string[] = [];
  const current = new Date(`${start}T00:00:00Z`);
  const last = new Date(`${end}T00:00:00Z`);
  while (current <= last && dates.length < 3660) {
    dates.push(current.toISOString().slice(0, 10));
    current.setUTCDate(current.getUTCDate() + 1);
  }
  return dates;
}

function formatDate(value: string): string {
  return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

function tokenFromBrowser(): string {
  return captureRouteSecret("/shared-schedule");
}

/** Render the token-authenticated, read-only Public Schedule page. */
export default function SharedSchedulePage() {
  const { isReady } = useServiceAvailability();
  const [data, setData] = useState<SharedScheduleResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const [selectedViewId, setSelectedViewId] = useState<number | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  useEffect(() => {
    if (!isReady) {
      setLoading(false);
      setUnavailable(false);
      return;
    }
    const token = tokenFromBrowser();
    if (!token) {
      setUnavailable(true);
      setLoading(false);
      return;
    }

    const load = async () => {
      try {
        const response = await fetch(`${getApiUrl()}/api/v1/public-schedule/shared`, {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
        });
        if (!response.ok) {
          if (isDefinitiveSecretRejection(response.status)) {
            clearRouteSecret("/shared-schedule");
          }
          throw new Error("Unavailable");
        }
        const payload = (await response.json()) as SharedScheduleResponse;
        if (payload.views.length === 0) throw new Error("Unavailable");
        setData(payload);
        setSelectedViewId(payload.views[0].id);

        const eventDates = datesBetween(payload.event.start_date, payload.event.end_date);
        const itemDates = Array.from(new Set(payload.items.map((item) => item.working_date ?? item.date))).sort();
        const dates = eventDates.length > 0 ? eventDates : itemDates;
        const today = currentWorkingDate(
          normaliseScheduleDayRange(payload.event.schedule_day_range),
        );
        setSelectedDate(
          dates.includes(today)
            ? today
            : payload.event.start_date && dates.includes(payload.event.start_date)
              ? payload.event.start_date
              : dates[0] ?? null,
        );
      } catch {
        setUnavailable(true);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [isReady]);

  const dates = useMemo(() => {
    if (!data) return [];
    const eventDates = datesBetween(data.event.start_date, data.event.end_date);
    if (eventDates.length > 0) return eventDates;
    return Array.from(new Set(data.items.map((item) => item.working_date ?? item.date))).sort();
  }, [data]);

  const visibleItems = useMemo(
    () => data?.items.filter((item) => item.view_id === selectedViewId) ?? [],
    [data, selectedViewId],
  );
  const dateIndex = selectedDate ? dates.indexOf(selectedDate) : -1;

  return (
    <div className="flex min-h-screen flex-col bg-gray-50 dark:bg-gray-900">
      <header className="border-b border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-800">
        <div className="mx-auto flex w-full max-w-6xl items-center justify-between px-4 py-3 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <span className="hidden sm:inline-flex"><Logo height={30} href="https://info.mp-opt.net" /></span>
            <div className="min-w-0">
              <p className="text-xs font-medium text-gray-500 dark:text-gray-400">
                Public Schedule
              </p>
              <h1 className="truncate text-lg font-bold leading-tight text-gray-900 dark:text-gray-100">
                {data?.event.name ?? "Shared Schedule"}
              </h1>
            </div>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-5 sm:px-6">
        {!isReady ? (
          <div className="flex justify-center py-12"><ServiceStatusPanel /></div>
        ) : loading ? (
          <p className="py-16 text-center text-sm text-gray-500 dark:text-gray-400">
            Loading schedule...
          </p>
        ) : unavailable || !data ? (
          <div className="py-16 text-center">
            <CalendarDays size={28} className="mx-auto text-gray-400" />
            <h2 className="mt-3 text-base font-semibold text-gray-900 dark:text-gray-100">
              Shared schedule unavailable
            </h2>
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
              This link is invalid, expired or no longer available.
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div
                className="flex max-w-full overflow-x-auto rounded-lg border border-gray-300 bg-white dark:border-gray-600 dark:bg-gray-800"
                role="tablist"
                aria-label="Public Schedule views"
              >
                {data.views.map((view) => (
                  <button
                    key={view.id}
                    type="button"
                    role="tab"
                    aria-selected={selectedViewId === view.id}
                    onClick={() => setSelectedViewId(view.id)}
                    className={`whitespace-nowrap px-3 py-1.5 text-sm font-medium transition-colors ${
                      selectedViewId === view.id
                        ? "bg-blue-600 text-white"
                        : "text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
                    }`}
                  >
                    {view.name}
                  </button>
                ))}
              </div>

              {selectedDate && (
                <div className="sticky top-0 z-20 -mx-4 flex min-w-0 items-center justify-between gap-2 border-y border-gray-200 bg-gray-50/95 px-3 py-2 backdrop-blur sm:static sm:mx-0 sm:justify-end sm:border-0 sm:bg-transparent sm:px-0 sm:py-0 dark:border-gray-700 dark:bg-gray-900/95 sm:dark:bg-transparent">
                  <button
                    type="button"
                    onClick={() => dateIndex > 0 && setSelectedDate(dates[dateIndex - 1])}
                    disabled={dateIndex <= 0}
                    className="flex h-11 w-11 items-center justify-center rounded-lg text-gray-600 transition-colors hover:bg-gray-200 disabled:opacity-30 dark:text-gray-300 dark:hover:bg-gray-700"
                    aria-label="Previous day"
                    title="Previous day"
                  >
                    <ChevronLeft size={19} />
                  </button>
                  <div className="min-w-0 text-center">
                    <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                      {formatDate(selectedDate)}
                    </p>
                    {data.event.day_aliases?.[selectedDate] && (
                      <p className="text-xs text-gray-500 dark:text-gray-400">
                        {data.event.day_aliases[selectedDate]}
                      </p>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() =>
                      dateIndex >= 0 &&
                      dateIndex < dates.length - 1 &&
                      setSelectedDate(dates[dateIndex + 1])
                    }
                    disabled={dateIndex < 0 || dateIndex >= dates.length - 1}
                    className="flex h-11 w-11 items-center justify-center rounded-lg text-gray-600 transition-colors hover:bg-gray-200 disabled:opacity-30 dark:text-gray-300 dark:hover:bg-gray-700"
                    aria-label="Next day"
                    title="Next day"
                  >
                    <ChevronRight size={19} />
                  </button>
                </div>
              )}
            </div>

            {selectedDate && visibleItems.some((item) => (item.working_date ?? item.date) === selectedDate) ? (
              <div className="-mx-4 overflow-hidden border-y border-gray-200 bg-white sm:mx-0 sm:rounded-xl sm:border dark:border-gray-700 dark:bg-gray-800">
                <PublicScheduleCalendarGrid
                  items={visibleItems}
                  selectedDate={selectedDate}
                  scheduleDayRange={normaliseScheduleDayRange(
                    data.event.schedule_day_range ?? DEFAULT_SCHEDULE_DAY_RANGE,
                  )}
                />
              </div>
            ) : (
              <div className="rounded-lg border border-gray-200 bg-white px-4 py-12 text-center text-sm text-gray-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400">
                No Session Elements are published in this view for this day.
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}

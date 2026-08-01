/** Published schedule display range shared by server calendar views. */
export interface ScheduleDayRange {
  start_hour: number;
  end_hour: number;
}

export const DEFAULT_SCHEDULE_DAY_RANGE: ScheduleDayRange = {
  start_hour: 6,
  end_hour: 24,
};

const MINUTES_PER_DAY = 24 * 60;

/** Return a validated schedule range or the standard server default. */
export function normaliseScheduleDayRange(
  value: Partial<ScheduleDayRange> | null | undefined,
): ScheduleDayRange {
  const start = Number(value?.start_hour);
  const end = Number(value?.end_hour);
  if (
    !Number.isInteger(start) ||
    !Number.isInteger(end) ||
    start < 0 ||
    start > 23 ||
    end <= start ||
    end > 36
  ) {
    return DEFAULT_SCHEDULE_DAY_RANGE;
  }
  return { start_hour: start, end_hour: end };
}

function dateSerial(value: string): number {
  const [year, month, day] = value.split("-").map(Number);
  return Math.floor(Date.UTC(year, month - 1, day) / 86_400_000);
}

/** Return linear minutes for an actual date/time relative to a working date. */
export function toWorkingDayMinutes(
  actualDate: string,
  clockTime: string,
  workingDate: string,
): number {
  const [hour, minute] = clockTime.slice(0, 5).split(":").map(Number);
  return (
    (dateSerial(actualDate) - dateSerial(workingDate)) * MINUTES_PER_DAY +
    hour * 60 +
    minute
  );
}

/** Return a linear end minute, rolling a clock-only end into the next day. */
export function toWorkingDayEndMinutes(
  actualDate: string,
  startTime: string,
  endTime: string,
  workingDate: string,
): number {
  const start = toWorkingDayMinutes(actualDate, startTime, workingDate);
  let end = toWorkingDayMinutes(actualDate, endTime, workingDate);
  if (end <= start) end += MINUTES_PER_DAY;
  return end;
}

/** Format a linear schedule hour with explicit next-day context. */
export function formatWorkingHour(hour: number): string {
  const clockHour = ((hour % 24) + 24) % 24;
  return hour >= 24
    ? `${String(clockHour).padStart(2, "0")}:00 (+1)`
    : `${String(clockHour).padStart(2, "0")}:00`;
}

/** Return the local working date containing the current time. */
export function currentWorkingDate(range: ScheduleDayRange): string {
  const now = new Date();
  const offsetHour = Math.max(0, range.end_hour - 24);
  if (offsetHour > 0 && now.getHours() < offsetHour) {
    now.setDate(now.getDate() - 1);
  }
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

/** Return the current time in linear minutes for its working day. */
export function currentWorkingMinute(range: ScheduleDayRange): number {
  const now = new Date();
  const offsetHour = Math.max(0, range.end_hour - 24);
  const minute = now.getHours() * 60 + now.getMinutes();
  return offsetHour > 0 && now.getHours() < offsetHour
    ? minute + MINUTES_PER_DAY
    : minute;
}

/** Derive the working date for a local ISO date-time and schedule range. */
export function workingDateForDateTime(
  iso: string,
  range: ScheduleDayRange,
): string {
  const [date, time = "00:00"] = iso.split("T");
  const hour = Number(time.slice(0, 2));
  const overnightBoundary = Math.max(0, range.end_hour - 24);
  if (!date || !Number.isFinite(hour) || overnightBoundary === 0 || hour >= overnightBoundary) {
    return date;
  }
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() - 1);
  return value.toISOString().slice(0, 10);
}

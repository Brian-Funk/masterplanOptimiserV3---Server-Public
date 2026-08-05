export const EVENT_DATE_RANGE_ERROR = "End date must be on or after start date.";

export function eventDateRangeError(startDate: string, endDate: string): string | null {
  if (!startDate || !endDate || endDate >= startDate) return null;
  return EVENT_DATE_RANGE_ERROR;
}

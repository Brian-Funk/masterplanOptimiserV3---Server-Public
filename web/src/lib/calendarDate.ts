export function getLocalDateString(date: Date = new Date()): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function chooseInitialScheduleDate(
  dates: string[],
  today = getLocalDateString(),
): string | null {
  if (dates.length === 0) return null;
  if (dates.includes(today)) return today;

  const upcoming = dates.find((date) => date > today);
  if (upcoming) return upcoming;

  return dates[dates.length - 1] ?? null;
}
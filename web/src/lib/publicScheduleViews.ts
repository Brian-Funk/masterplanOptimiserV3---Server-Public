/** Public schedule view option returned by the calendar API. */
export interface PublicScheduleViewOption {
  id: string;
  name: string;
  sort_order: number;
}

/** Public schedule item data needed for view filtering. */
export interface PublicScheduleViewItem {
  date: string;
  working_date?: string;
  category_id: number | null;
  start_time: string;
  sort_order: number;
  title: string;
}

/** Keep every configured public view available and order it predictably. */
export function getOrderedPublicScheduleViews<T extends PublicScheduleViewOption>(
  views: T[],
): T[] {
  return [...views].sort((a, b) => (
    a.sort_order - b.sort_order || a.name.localeCompare(b.name)
  ));
}

/** Return public schedule items for one selected date and schedule view. */
export function getPublicScheduleItemsForView<T extends PublicScheduleViewItem>(
  items: T[],
  selectedDate: string | null,
  viewId: string | null,
): T[] {
  if (!selectedDate || viewId === null) return [];
  return items
    .filter((item) => (item.working_date ?? item.date) === selectedDate)
    .filter((item) => {
      if (viewId === "legacy") return item.category_id === null;
      return String(item.category_id) === viewId;
    })
    .sort((a, b) => (
      a.date.localeCompare(b.date)
      || a.start_time.localeCompare(b.start_time)
      || (a.sort_order ?? 0) - (b.sort_order ?? 0)
      || a.title.localeCompare(b.title)
    ));
}

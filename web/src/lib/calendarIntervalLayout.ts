/** Identifier supported by the calendar interval layout algorithm. */
export type CalendarIntervalId = string | number;

/** Time interval to position horizontally within a calendar timeline. */
export interface CalendarInterval {
  id: CalendarIntervalId;
  start: number;
  end: number;
}

/** Horizontal placement assigned to a calendar interval. */
export interface CalendarIntervalLayout {
  laneIndex: number;
  laneCount: number;
  laneSpan: number;
  leftPercentage: number;
  widthPercentage: number;
}

interface PositionedInterval extends CalendarInterval {
  displayEnd: number;
}

function intervalsOverlap(
  first: PositionedInterval,
  second: PositionedInterval,
): boolean {
  return first.start < second.displayEnd && second.start < first.displayEnd;
}

function compareIds(
  first: CalendarIntervalId,
  second: CalendarIntervalId,
): number {
  if (typeof first === "number" && typeof second === "number") {
    return first - second;
  }
  const firstText = String(first);
  const secondText = String(second);
  if (firstText < secondText) return -1;
  if (firstText > secondText) return 1;
  return 0;
}

/**
 * Lay out calendar intervals in collision-free lanes and expand them into
 * adjacent lanes that remain unused for their complete displayed duration.
 *
 * Intervals are treated as half-open ranges. `minimumDurationMinutes` extends
 * only their collision footprint, allowing short cards to retain a readable
 * minimum height without visually covering a following card.
 */
export function layoutCalendarIntervals(
  intervals: CalendarInterval[],
  minimumDurationMinutes = 0,
): Map<CalendarIntervalId, CalendarIntervalLayout> {
  const minimumDuration = Math.max(0, minimumDurationMinutes);
  const positioned = intervals
    .map((interval): PositionedInterval => ({
      ...interval,
      displayEnd: Math.max(interval.end, interval.start + minimumDuration),
    }))
    .sort(
      (first, second) =>
        first.start - second.start ||
        second.displayEnd - first.displayEnd ||
        compareIds(first.id, second.id),
    );

  const groups: PositionedInterval[][] = [];
  let currentGroup: PositionedInterval[] = [];
  let currentGroupEnd = Number.NEGATIVE_INFINITY;

  positioned.forEach((interval) => {
    if (currentGroup.length > 0 && interval.start >= currentGroupEnd) {
      groups.push(currentGroup);
      currentGroup = [];
      currentGroupEnd = Number.NEGATIVE_INFINITY;
    }
    currentGroup.push(interval);
    currentGroupEnd = Math.max(currentGroupEnd, interval.displayEnd);
  });
  if (currentGroup.length > 0) groups.push(currentGroup);

  const layout = new Map<CalendarIntervalId, CalendarIntervalLayout>();

  groups.forEach((group) => {
    const lanes: PositionedInterval[][] = [];
    const laneById = new Map<CalendarIntervalId, number>();

    group.forEach((interval) => {
      const availableLane = lanes.findIndex((lane) => {
        const previous = lane[lane.length - 1];
        return previous.displayEnd <= interval.start;
      });
      const laneIndex = availableLane === -1 ? lanes.length : availableLane;
      if (availableLane === -1) lanes.push([]);
      lanes[laneIndex].push(interval);
      laneById.set(interval.id, laneIndex);
    });

    group.forEach((interval) => {
      const laneIndex = laneById.get(interval.id) ?? 0;
      let laneSpan = 1;

      for (
        let candidateLane = laneIndex + 1;
        candidateLane < lanes.length;
        candidateLane += 1
      ) {
        if (
          lanes[candidateLane].some((candidate) =>
            intervalsOverlap(interval, candidate),
          )
        ) {
          break;
        }
        laneSpan += 1;
      }

      layout.set(interval.id, {
        laneIndex,
        laneCount: lanes.length,
        laneSpan,
        leftPercentage: (laneIndex / lanes.length) * 100,
        widthPercentage: (laneSpan / lanes.length) * 100,
      });
    });
  });

  return layout;
}

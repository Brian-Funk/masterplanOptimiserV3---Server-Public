export type SnapshotComparisonLevel = "healthy" | "review" | "blocked" | "unknown";

export type SnapshotComparisonSectionId =
  | "added"
  | "removed"
  | "time"
  | "location"
  | "assignments"
  | "details"
  | "day"
  | "other";

export type SnapshotTaskSummary = {
  title?: string;
  day?: string | null;
  startTime?: string | null;
  endTime?: string | null;
  location?: string | null;
  assignedPeople?: string[];
  description?: string | null;
};

export type SnapshotComparisonItem = {
  taskId?: string;
  taskName: string;
  dayLabel?: string;
  affectedPeople?: string[];
  changeSummary: string;
  before?: SnapshotTaskSummary | null;
  after?: SnapshotTaskSummary | null;
};

export type SnapshotComparisonSection = {
  id: SnapshotComparisonSectionId;
  title: string;
  count: number;
  items: SnapshotComparisonItem[];
};

export type SnapshotComparisonSummary = {
  level: SnapshotComparisonLevel;
  snapshotId: string;
  snapshotLabel: string;
  comparedAt: string;
  totalChanges: number;
  addedCount: number;
  removedCount: number;
  timeChangeCount: number;
  locationChangeCount: number;
  assignmentChangeCount: number;
  detailsChangeCount: number;
  dayChangeCount: number;
  headline: string;
  description: string;
  sections: SnapshotComparisonSection[];
};

type ComparableTask = Record<string, unknown>;

type NormalisedTask = {
  key: string;
  taskId?: string;
  title: string;
  day: string | null;
  start: string | null;
  end: string | null;
  location: string | null;
  assignedPeople: string[];
  summary: string | null;
  description: string | null;
  taskType: string | null;
  detailsFingerprint: string;
  source: ComparableTask;
};

const SECTION_TITLES: Record<SnapshotComparisonSectionId, string> = {
  added: "Added tasks",
  removed: "Removed tasks",
  time: "Time changes",
  location: "Location changes",
  assignments: "Assignment changes",
  details: "Details changes",
  day: "Day changes",
  other: "Other changes",
};

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function parseTimestamp(value?: string | null): Date | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function sameLocalDate(left: Date, right: Date): boolean {
  return (
    left.getFullYear() === right.getFullYear() &&
    left.getMonth() === right.getMonth() &&
    left.getDate() === right.getDate()
  );
}

/** Format comparison timestamps for compact operations summaries. */
export function formatSnapshotComparisonTimestamp(
  value?: string | null,
  now: Date = new Date(),
): string | null {
  const date = parseTimestamp(value);
  if (!date) return null;
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  if (sameLocalDate(date, now)) return `today at ${time}`;

  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameLocalDate(date, yesterday)) return `yesterday at ${time}`;

  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()} at ${time}`;
}

function stringValue(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const text = String(value).trim();
  return text.length > 0 ? text : null;
}

function numberValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function canonicalise(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalise);
  if (value && typeof value === "object") {
    return Object.keys(value as Record<string, unknown>)
      .sort()
      .reduce<Record<string, unknown>>((acc, key) => {
        acc[key] = canonicalise((value as Record<string, unknown>)[key]);
        return acc;
      }, {});
  }
  return value ?? null;
}

function stableString(value: unknown): string {
  return JSON.stringify(canonicalise(value));
}

function datePart(value?: string | null): string | null {
  if (!value) return null;
  if (value.includes("T")) return value.split("T")[0] || null;
  return value.slice(0, 10) || null;
}

function timePart(value?: string | null): string | null {
  if (!value) return null;
  const raw = value.includes("T") ? value.split("T")[1] : value;
  if (!raw) return null;
  const [hour = "00", minute = "00"] = raw.split(":");
  return `${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;
}

function formatDateLabel(value?: string | null): string | undefined {
  const day = datePart(value);
  if (!day) return undefined;
  const [year, month, date] = day.split("-");
  if (!year || !month || !date) return day;
  return `${date}.${month}.${year}`;
}

function formatTimeRange(start?: string | null, end?: string | null): string | null {
  const from = timePart(start);
  const to = timePart(end);
  if (from && to) return `${from} - ${to}`;
  if (from) return from;
  if (to) return to;
  return null;
}

function attendeeName(attendee: unknown): string | null {
  if (!attendee || typeof attendee !== "object") return null;
  const record = attendee as Record<string, unknown>;
  return stringValue(record.name) ?? stringValue(record.display_name);
}

function collectAssignedPeople(task: ComparableTask): string[] {
  const names = new Set<string>();
  const attendees = Array.isArray(task.attendees) ? task.attendees : [];
  for (const attendee of attendees) {
    const name = attendeeName(attendee);
    if (name) names.add(name);
  }

  const fieldAssignments = task.field_assignments;
  if (fieldAssignments && typeof fieldAssignments === "object") {
    for (const value of Object.values(fieldAssignments as Record<string, unknown>)) {
      if (!Array.isArray(value)) continue;
      for (const attendee of value) {
        const name = attendeeName(attendee);
        if (name) names.add(name);
      }
    }
  }

  return Array.from(names).sort((a, b) => a.localeCompare(b));
}

function fallbackKey(task: ComparableTask): string {
  const title = stringValue(task.name) ?? stringValue(task.title) ?? "task";
  const start = stringValue(task.start) ?? stringValue(task.start_datetime) ?? "no-start";
  const location = stringValue(task.location_name) ?? "no-location";
  return `fallback:${title.toLowerCase()}|${start}|${location.toLowerCase()}`;
}

function taskKey(task: ComparableTask): string {
  const externalTaskId = numberValue(task.external_task_id);
  if (externalTaskId && externalTaskId > 0) return `external:${externalTaskId}`;
  const id = stringValue(task.id);
  if (id) return `id:${id}`;
  return fallbackKey(task);
}

function normaliseTask(task: ComparableTask): NormalisedTask {
  const start = stringValue(task.start) ?? stringValue(task.start_datetime);
  const end = stringValue(task.end) ?? stringValue(task.end_datetime);
  const title = stringValue(task.name) ?? stringValue(task.title) ?? "Untitled task";
  const location = stringValue(task.location_name) ?? stringValue(task.location);
  const assignedPeople = collectAssignedPeople(task);
  const taskType = stringValue(task.task_type_name) ?? stringValue(task.task_type_code);
  const summary = stringValue(task.summary);
  const description = stringValue(task.description);
  const detailsFingerprint = stableString({
    title,
    summary,
    description,
    taskType,
    field_values: task.field_values ?? null,
    additional: task.additional ?? null,
  });

  return {
    key: taskKey(task),
    taskId: stringValue(task.external_task_id) ?? stringValue(task.id) ?? undefined,
    title,
    day: datePart(start),
    start,
    end,
    location,
    assignedPeople,
    summary,
    description,
    taskType,
    detailsFingerprint,
    source: task,
  };
}

function toSummary(task: NormalisedTask): SnapshotTaskSummary {
  return {
    title: task.title,
    day: formatDateLabel(task.day),
    startTime: timePart(task.start),
    endTime: timePart(task.end),
    location: task.location,
    assignedPeople: task.assignedPeople,
    description: task.description,
  };
}

function joinPeople(people: string[]): string {
  return people.length > 0 ? people.join(", ") : "No assigned people";
}

function comparePeople(before: string[], after: string[]): string {
  const beforeSet = new Set(before);
  const afterSet = new Set(after);
  const added = after.filter((person) => !beforeSet.has(person));
  const removed = before.filter((person) => !afterSet.has(person));
  const parts = [
    removed.length > 0 ? `${removed.join(", ")} removed` : null,
    added.length > 0 ? `${added.join(", ")} added` : null,
  ].filter(Boolean);
  return parts.join("; ") || `${joinPeople(before)} -> ${joinPeople(after)}`;
}

function sectionItem(
  task: NormalisedTask,
  changeSummary: string,
  before: NormalisedTask | null,
  after: NormalisedTask | null,
): SnapshotComparisonItem {
  const displayTask = after ?? before ?? task;
  return {
    taskId: displayTask.taskId,
    taskName: displayTask.title,
    dayLabel: formatDateLabel(displayTask.day),
    affectedPeople: displayTask.assignedPeople,
    changeSummary,
    before: before ? toSummary(before) : null,
    after: after ? toSummary(after) : null,
  };
}

function pushSection(
  sections: Record<SnapshotComparisonSectionId, SnapshotComparisonItem[]>,
  id: SnapshotComparisonSectionId,
  item: SnapshotComparisonItem,
) {
  sections[id].push(item);
}

function buildMap(tasks: ComparableTask[]): Map<string, NormalisedTask> {
  const map = new Map<string, NormalisedTask>();
  for (const task of tasks) {
    const normalised = normaliseTask(task);
    if (!map.has(normalised.key)) map.set(normalised.key, normalised);
  }
  return map;
}

function buildSections(
  sections: Record<SnapshotComparisonSectionId, SnapshotComparisonItem[]>,
): SnapshotComparisonSection[] {
  return (Object.keys(SECTION_TITLES) as SnapshotComparisonSectionId[])
    .map((id) => ({
      id,
      title: SECTION_TITLES[id],
      count: sections[id].length,
      items: sections[id],
    }))
    .filter((section) => section.count > 0);
}

/** Compare a stored publish snapshot with the current live schedule. */
export function compareSnapshotToCurrent(
  snapshotTasks: ComparableTask[] | undefined | null,
  currentTasks: ComparableTask[] | undefined | null,
  options: {
    snapshotId: string;
    snapshotLabel?: string | null;
    snapshotCreatedAt?: string | null;
    now?: Date;
  },
): SnapshotComparisonSummary {
  const now = options.now ?? new Date();
  const snapshotLabel = options.snapshotCreatedAt
    ? `snapshot from ${formatSnapshotComparisonTimestamp(options.snapshotCreatedAt, now) ?? options.snapshotCreatedAt}`
    : options.snapshotLabel || `snapshot ${options.snapshotId}`;

  if (!Array.isArray(snapshotTasks) || !Array.isArray(currentTasks)) {
    return createUnavailableSnapshotComparison({
      snapshotId: options.snapshotId,
      snapshotLabel,
      reason: "The selected snapshot does not contain comparable schedule data.",
      now,
    });
  }

  const sections: Record<SnapshotComparisonSectionId, SnapshotComparisonItem[]> = {
    added: [],
    removed: [],
    time: [],
    location: [],
    assignments: [],
    details: [],
    day: [],
    other: [],
  };
  const beforeMap = buildMap(snapshotTasks);
  const afterMap = buildMap(currentTasks);

  for (const [key, after] of afterMap.entries()) {
    if (!beforeMap.has(key)) {
      pushSection(sections, "added", sectionItem(after, "Added to the live schedule", null, after));
    }
  }

  for (const [key, before] of beforeMap.entries()) {
    const after = afterMap.get(key);
    if (!after) {
      pushSection(sections, "removed", sectionItem(before, "Removed from the live schedule", before, null));
      continue;
    }

    if (before.day !== after.day) {
      pushSection(
        sections,
        "day",
        sectionItem(
          after,
          `${formatDateLabel(before.day) ?? "No day"} -> ${formatDateLabel(after.day) ?? "No day"}`,
          before,
          after,
        ),
      );
    }

    if (before.start !== after.start || before.end !== after.end) {
      pushSection(
        sections,
        "time",
        sectionItem(
          after,
          `${formatTimeRange(before.start, before.end) ?? "No time"} -> ${formatTimeRange(after.start, after.end) ?? "No time"}`,
          before,
          after,
        ),
      );
    }

    if ((before.location ?? "") !== (after.location ?? "")) {
      pushSection(
        sections,
        "location",
        sectionItem(
          after,
          `${before.location || "No location"} -> ${after.location || "No location"}`,
          before,
          after,
        ),
      );
    }

    if (before.assignedPeople.join("|") !== after.assignedPeople.join("|")) {
      pushSection(
        sections,
        "assignments",
        sectionItem(after, comparePeople(before.assignedPeople, after.assignedPeople), before, after),
      );
    }

    if (before.detailsFingerprint !== after.detailsFingerprint) {
      const changedDetails = [
        before.title !== after.title ? "Title changed" : null,
        before.summary !== after.summary ? "Summary changed" : null,
        before.description !== after.description ? "Description changed" : null,
        before.taskType !== after.taskType ? "Task type changed" : null,
      ].filter(Boolean);
      pushSection(
        sections,
        "details",
        sectionItem(after, changedDetails.join("; ") || "Visible task details changed", before, after),
      );
    }
  }

  const sectionList = buildSections(sections);
  const totalChanges = sectionList.reduce((sum, section) => sum + section.count, 0);
  const addedCount = sections.added.length;
  const removedCount = sections.removed.length;
  const changedCount = Math.max(totalChanges - addedCount - removedCount, 0);

  if (totalChanges === 0) {
    return {
      level: "healthy",
      snapshotId: options.snapshotId,
      snapshotLabel,
      comparedAt: now.toISOString(),
      totalChanges,
      addedCount,
      removedCount,
      timeChangeCount: 0,
      locationChangeCount: 0,
      assignmentChangeCount: 0,
      detailsChangeCount: 0,
      dayChangeCount: 0,
      headline: "No changes since this snapshot",
      description: "The current live schedule matches the selected snapshot.",
      sections: [],
    };
  }

  return {
    level: "review",
    snapshotId: options.snapshotId,
    snapshotLabel,
    comparedAt: now.toISOString(),
    totalChanges,
    addedCount,
    removedCount,
    timeChangeCount: sections.time.length,
    locationChangeCount: sections.location.length,
    assignmentChangeCount: sections.assignments.length,
    detailsChangeCount: sections.details.length,
    dayChangeCount: sections.day.length,
    headline: "Changes found since this snapshot",
    description: `${totalChanges} schedule change${totalChanges === 1 ? "" : "s"} found: ${addedCount} added, ${removedCount} removed, ${changedCount} changed.`,
    sections: sectionList,
  };
}

export function createUnavailableSnapshotComparison({
  snapshotId,
  snapshotLabel,
  reason,
  now = new Date(),
}: {
  snapshotId: string;
  snapshotLabel?: string | null;
  reason: string;
  now?: Date;
}): SnapshotComparisonSummary {
  return {
    level: "blocked",
    snapshotId,
    snapshotLabel: snapshotLabel || `snapshot ${snapshotId}`,
    comparedAt: now.toISOString(),
    totalChanges: 0,
    addedCount: 0,
    removedCount: 0,
    timeChangeCount: 0,
    locationChangeCount: 0,
    assignmentChangeCount: 0,
    detailsChangeCount: 0,
    dayChangeCount: 0,
    headline: "Snapshot comparison unavailable",
    description: reason,
    sections: [],
  };
}
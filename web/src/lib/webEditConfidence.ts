export type WebEditConfidence = "healthy" | "review" | "blocked" | "unknown";

export type WebEditItem = {
  task_id: number;
  task_name: string;
  day?: string | null;
  start?: string | null;
  end?: string | null;
  location?: string | null;
  edited_at?: string | null;
  edited_by?: string | null;
  edited_by_user_id?: number | null;
  change_summary: string[];
  original_summary: string;
  current_summary: string;
};

export type WebEditSummary = {
  level: WebEditConfidence;
  edited_task_count: number;
  last_edited_at?: string | null;
  last_edited_by?: string | null;
  has_published_baseline: boolean;
  headline: string;
  description: string;
  items: WebEditItem[];
};

export type WebEditDisplaySummary = {
  level: WebEditConfidence;
  headline: string;
  description: string;
  countLabel: string;
};

export type WebEditTaskMarker = {
  has_web_edit: boolean;
  web_edit_edited_at?: string | null;
  web_edit_edited_by?: string | null;
  web_edit_change_summary?: string[] | null;
};

function parseTimestamp(value?: string | null): Date | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function sameLocalDate(left: Date, right: Date): boolean {
  return (
    left.getFullYear() === right.getFullYear() &&
    left.getMonth() === right.getMonth() &&
    left.getDate() === right.getDate()
  );
}

/** Format edit timestamps for compact operations summaries. */
export function formatWebEditTimestamp(
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

  const day = String(date.getDate());
  const month = date.toLocaleString("en-GB", { month: "short" });
  if (date.getFullYear() === now.getFullYear()) {
    return `${day} ${month} at ${time}`;
  }
  return `${day} ${month} ${date.getFullYear()} at ${time}`;
}

/** Build the one-line web edit confidence message for admin UI. */
export function summariseWebEditState(
  summary: WebEditSummary | null,
  now: Date = new Date(),
): WebEditDisplaySummary {
  if (!summary) {
    return {
      level: "unknown",
      headline: "Web edit state unavailable",
      description: "The server could not load web edit information.",
      countLabel: "Unknown",
    };
  }

  if (!summary.has_published_baseline) {
    return {
      level: "unknown",
      headline: "Web edit state unknown",
      description: "No published desktop baseline is available yet.",
      countLabel: "No baseline",
    };
  }

  if (summary.edited_task_count === 0) {
    return {
      level: "healthy",
      headline: "No web edits",
      description: "Live schedule matches the published desktop source.",
      countLabel: "0 edits",
    };
  }

  const when = formatWebEditTimestamp(summary.last_edited_at, now);
  const lastEditSentence = when
    ? summary.last_edited_by
      ? ` Last edited by ${summary.last_edited_by} ${when}.`
      : ` Last edit ${when}.`
    : "";
  return {
    level: summary.level,
    headline: "Review needed",
    description: `${summary.edited_task_count} web edit${
      summary.edited_task_count === 1 ? "" : "s"
    } since the last desktop publish.${lastEditSentence}`,
    countLabel: `${summary.edited_task_count} edit${
      summary.edited_task_count === 1 ? "" : "s"
    }`,
  };
}

/** Return the task-level tooltip text for a committed web edit marker. */
export function describeWebEditTask(
  task: WebEditTaskMarker,
  now: Date = new Date(),
): string {
  if (!task.has_web_edit) return "";
  const when = formatWebEditTimestamp(task.web_edit_edited_at, now);
  const prefix = [
    "Edited on the web",
    task.web_edit_edited_by ? `by ${task.web_edit_edited_by}` : null,
    when,
  ]
    .filter(Boolean)
    .join(" ");
  const changes = (task.web_edit_change_summary ?? []).filter(Boolean);
  return changes.length > 0
    ? `${prefix}. ${changes.join("; ")}.`
    : `${prefix}.`;
}

/** Group review-list items by their schedule day. */
export function groupWebEditItemsByDay(
  items: WebEditItem[] = [],
): Array<{ day: string; items: WebEditItem[] }> {
  const groups = new Map<string, WebEditItem[]>();
  for (const item of items) {
    const key = item.day || "No day";
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  return Array.from(groups.entries()).map(([day, groupedItems]) => ({
    day,
    items: groupedItems,
  }));
}

/** Shared, order-preserving presentation of bounded published task data. */

export interface PresentedAttendee {
  name: string;
  person_id: number;
}

export interface PresentedFieldDefinition {
  id: string;
  name: string;
  type: string;
}

export interface PresentableTask {
  name: string;
  task_type_name?: string | null;
  attendees: PresentedAttendee[];
  field_assignments?: Record<string, PresentedAttendee[]> | null;
  field_values?: Record<string, unknown> | null;
  field_definitions?: PresentedFieldDefinition[] | null;
}

export interface TaskAllocation {
  fieldId: string | null;
  label: string | null;
  attendees: PresentedAttendee[];
  legacy: boolean;
}

export interface TaskOperationalField {
  fieldId: string;
  label: string;
  type: string;
  value: string;
  secondary?: string;
  href?: string;
}

function safeHttpUrl(value: string): string | null {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? parsed.href
      : null;
  } catch {
    return null;
  }
}

/**
 * Return structured allocation buckets in their published order. The legacy
 * flat attendee list is used only when the task has no allocation definition.
 */
export function taskAllocations(
  task: PresentableTask,
  includeEmpty = false,
): TaskAllocation[] {
  const definitions = (task.field_definitions ?? []).filter(
    (definition) => definition.type === "persons_list",
  );
  if (definitions.length > 0) {
    return definitions
      .map((definition) => ({
        fieldId: definition.id,
        label: definition.name,
        attendees: task.field_assignments?.[definition.id] ?? [],
        legacy: false,
      }))
      .filter((allocation) => includeEmpty || allocation.attendees.length > 0);
  }
  if (task.attendees.length === 0 && !includeEmpty) return [];
  return [{
    fieldId: null,
    label: null,
    attendees: task.attendees,
    legacy: true,
  }];
}

/** Return a type badge only when it adds information beyond the task title. */
export function taskTypeBadge(task: PresentableTask): string | null {
  const typeName = task.task_type_name?.trim();
  if (!typeName) return null;
  return typeName.localeCompare(task.name.trim(), undefined, {
    sensitivity: "accent",
  }) === 0
    ? null
    : typeName;
}

/** Convert each bounded non-allocation field into one labelled display row. */
export function taskOperationalFields(
  task: PresentableTask,
): TaskOperationalField[] {
  const values = task.field_values ?? {};
  const rows: TaskOperationalField[] = [];
  for (const definition of task.field_definitions ?? []) {
    if (definition.type === "persons_list") continue;
    const raw = values[definition.id];
    if (raw === null || raw === undefined || raw === "") continue;

    if (definition.type === "text") {
      const value = String(raw);
      if (value.trim()) rows.push({
        fieldId: definition.id,
        label: definition.name,
        type: definition.type,
        value,
      });
      continue;
    }
    if (definition.type === "link") {
      const candidate = typeof raw === "object" && raw !== null
        ? raw as { url?: unknown; text?: unknown }
        : { url: raw, text: raw };
      const href = safeHttpUrl(String(candidate.url ?? ""));
      if (href) rows.push({
        fieldId: definition.id,
        label: definition.name,
        type: definition.type,
        value: String(candidate.text || candidate.url),
        href,
      });
      continue;
    }
    if (definition.type === "location") {
      const location = typeof raw === "object" && raw !== null
        ? raw as { name?: unknown; address?: unknown }
        : { name: raw };
      const name = String(location.name ?? "").trim();
      if (name) rows.push({
        fieldId: definition.id,
        label: definition.name,
        type: definition.type,
        value: name,
        secondary: location.address ? String(location.address) : undefined,
      });
      continue;
    }
    if (definition.type === "capabilities_list" && Array.isArray(raw)) {
      const value = raw.map(String).filter(Boolean).join(", ");
      if (value) rows.push({
        fieldId: definition.id,
        label: definition.name,
        type: definition.type,
        value,
      });
      continue;
    }
    if (
      (definition.type === "start_end_time" || definition.type === "time_range")
      && typeof raw === "object"
      && raw !== null
    ) {
      const range = raw as { start?: unknown; end?: unknown };
      if (range.start !== undefined && range.end !== undefined) rows.push({
        fieldId: definition.id,
        label: definition.name,
        type: definition.type,
        value: `${String(range.start)} – ${String(range.end)}`,
      });
      continue;
    }
    if (definition.type === "number" || definition.type === "duration") {
      rows.push({
        fieldId: definition.id,
        label: definition.name,
        type: definition.type,
        value: String(raw),
      });
    }
  }
  return rows;
}

/** Convert FastAPI and application error payloads into safe user-facing text. */
export function responseMessage(data: unknown, fallback: string): string {
  if (!data || typeof data !== "object") return fallback;
  const record = data as Record<string, unknown>;
  if (typeof record.message === "string") return record.message;
  if (typeof record.detail === "string") return record.detail;
  if (record.detail && typeof record.detail === "object") {
    if (Array.isArray(record.detail)) {
      const validation = record.detail.find(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === "object" && typeof (item as Record<string, unknown>).msg === "string",
      );
      if (validation) {
        const location = Array.isArray(validation.loc) ? validation.loc : [];
        const field = location.length > 0 ? String(location[location.length - 1]) : "Input";
        const label = field.replaceAll("_", " ").replace(/^./, (value) => value.toUpperCase());
        return `${label}: ${String(validation.msg)}`;
      }
    }
    const detail = record.detail as Record<string, unknown>;
    if (typeof detail.message === "string") return detail.message;
  }
  return fallback;
}

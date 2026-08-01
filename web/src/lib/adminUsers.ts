/** Derive a simple username from a display name for bulk user creation. */
export function deriveUsernameFromDisplayName(displayName: string): string {
  return displayName
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\s._-]/g, "")
    .replace(/\s+/g, ".")
    .replace(/\.+/g, ".")
    .replace(/^\.+|\.+$/g, "");
}

/** Split a comma separated tag input into normalised unique tag values. */
export function parseTagList(value: string): string[] {
  const tags: string[] = [];
  const seen = new Set<string>();
  value
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean)
    .forEach((tag) => {
      if (seen.has(tag)) return;
      seen.add(tag);
      tags.push(tag);
    });
  return tags;
}

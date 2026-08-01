/**
 * Preset task colours matching the desktop app palette.
 * Only these colours are available when editing tasks on the web.
 */
export const TASK_COLORS: { hex: string; label: string }[] = [
  { hex: "#3b82f6", label: "Blue" },
  { hex: "#ef4444", label: "Red" },
  { hex: "#10b981", label: "Green" },
  { hex: "#f59e0b", label: "Amber" },
  { hex: "#8b5cf6", label: "Purple" },
  { hex: "#ec4899", label: "Pink" },
  { hex: "#06b6d4", label: "Cyan" },
  { hex: "#84cc16", label: "Lime" },
  { hex: "#f97316", label: "Orange" },
  { hex: "#6366f1", label: "Indigo" },
  { hex: "#14b8a6", label: "Teal" },
  { hex: "#e11d48", label: "Rose" },
  { hex: "#a855f7", label: "Violet" },
  { hex: "#0ea5e9", label: "Sky" },
  { hex: "#78716c", label: "Stone" },
];

/** Bright alert colour for marking manually changed tasks. Shown separately. */
export const ALERT_COLOR = { hex: "#FF1744", label: "Alert Red" };

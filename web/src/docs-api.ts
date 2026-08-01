/**
 * Curated public TypeScript documentation entrypoint for the server web app.
 *
 * The exports here are the frontend symbols intended for generated API
 * documentation. Route pages remain documented through the manual MkDocs
 * pages because they are application screens rather than reusable APIs.
 */

export { apiFetch } from "./lib/api";
export { BRAND } from "./lib/brand";
export { TASK_COLORS, ALERT_COLOR } from "./lib/colors";
export { getApiUrl } from "./lib/environment";
export {
  clearOfflineCalendarCacheForUser,
  getOfflineCalendarPayload,
  storeOfflineCalendarPayload,
} from "./lib/offlineCalendarCache";
export type { OfflineCalendarCacheEntry } from "./lib/offlineCalendarCache";
export type { OfflineAccessMarker } from "./lib/offlineAccess";
export { performReauth, withReauth } from "./lib/reauth";
export {
  currentWorkingDate,
  currentWorkingMinute,
  formatWorkingHour,
  normaliseScheduleDayRange,
  toWorkingDayEndMinutes,
  toWorkingDayMinutes,
  workingDateForDateTime,
} from "./lib/scheduleDays";
export type { ScheduleDayRange } from "./lib/scheduleDays";

export { AuthProvider, useAuth } from "./contexts/AuthContext";
export type { AuthContextType, AuthStatus, User } from "./contexts/AuthContext";
export { ThemeProvider, useTheme } from "./contexts/ThemeContext";
export type { Theme, ThemeContextType } from "./contexts/ThemeContext";

export { AnnouncementBanner } from "./components/AnnouncementBanner";
export type { AnnouncementBannerProps } from "./components/AnnouncementBanner";
export { CalendarGrid } from "./components/CalendarGrid";
export type {
  Attendee as CalendarAttendee,
  CalendarGridProps,
  Task as CalendarTask,
} from "./components/CalendarGrid";
export { DailyUnavailabilityIndicator } from "./components/DailyUnavailabilityIndicator";
export type {
  AvailabilityPerson,
  DailyUnavailabilityIndicatorProps,
  PublishedUnavailability,
} from "./components/DailyUnavailabilityIndicator";
export { ChangesModal } from "./components/ChangesModal";
export type {
  ChangeRecord,
  ChangesModalProps,
  FieldChange,
  ModifiedTask,
  PendingChange,
  TaskSummary,
} from "./components/ChangesModal";
export { CreateTaskModal } from "./components/CreateTaskModal";
export type {
  Attendee as CreateTaskAttendee,
  CreateTaskModalProps,
  DraftNewTask as CreateTaskDraftNewTask,
  Person as CreateTaskPerson,
} from "./components/CreateTaskModal";
export { DeleteMyDataLink } from "./components/DeleteMyDataLink";
export { DraftChangesPanel } from "./components/DraftChangesPanel";
export type {
  Attendee as DraftChangesPanelAttendee,
  DraftEdit as DraftChangesPanelEdit,
  DraftChangesPanelProps,
  DraftNewTask as DraftChangesPanelNewTask,
} from "./components/DraftChangesPanel";
export { DynamicPWA } from "./components/DynamicPWA";
export type { DynamicPWAProps } from "./components/DynamicPWA";
export { Footer } from "./components/Footer";
export { InstallPrompt } from "./components/InstallPrompt";
export { Logo } from "./components/Logo";
export type { LogoProps } from "./components/Logo";
export { MobileActionSheet } from "./components/MobileActionSheet";
export type { MobileActionSheetProps } from "./components/MobileActionSheet";
export { MobileBottomNavigation } from "./components/MobileBottomNavigation";
export type {
  MobileBottomNavigationProps,
  MobileNavigationItem,
} from "./components/MobileBottomNavigation";
export { NotificationBell } from "./components/NotificationBell";
export type { NotificationBellProps } from "./components/NotificationBell";
export { PasskeyManager } from "./components/PasskeyManager";
export type {
  Credential as PasskeyCredential,
  PasskeyManagerProps,
} from "./components/PasskeyManager";
export {
  canManagePublicScheduleLinks,
  PublicScheduleLinksTab,
} from "./components/PublicScheduleLinksTab";
export type {
  PublicScheduleLinkPermission,
  PublicScheduleLinkRecord,
  PublicScheduleLinkRole,
  PublicScheduleLinksTabProps,
  PublicScheduleLinkViewOption,
} from "./components/PublicScheduleLinksTab";
export { PublicScheduleCalendarGrid } from "./components/PublicScheduleCalendarGrid";
export type {
  PublicScheduleAudience,
  PublicScheduleCalendarGridProps,
  PublicScheduleCalendarItem,
} from "./components/PublicScheduleCalendarGrid";
export { TaskDetailModal } from "./components/TaskDetailModal";
export type {
  Attendee as TaskDetailAttendee,
  DraftEdit as TaskDetailDraftEdit,
  Person as TaskDetailPerson,
  Task as TaskDetailTask,
  TaskDetailModalProps,
} from "./components/TaskDetailModal";
export { ThemeToggle } from "./components/ThemeToggle";

export { Button } from "./components/ui/Button";
export type { ButtonProps } from "./components/ui/Button";
export { Card } from "./components/ui/Card";
export type { CardProps } from "./components/ui/Card";
export { Input } from "./components/ui/Input";
export type { InputProps } from "./components/ui/Input";

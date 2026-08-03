"use client";

import { useRouter } from "next/navigation";
import {
  Activity,
  FileCheck2,
  FileText,
  History,
  Megaphone,
  Plus,
  Share2,
  Shield,
  Users,
} from "lucide-react";

export type AdminTab =
  | "events"
  | "users"
  | "announcements"
  | "history"
  | "public-links"
  | "security"
  | "privacy"
  | "ha"
  | "audit";

export type AdminDestination = AdminTab | "policies";

type AdminGroup = "operations" | "publishing" | "governance" | "system";

type NavigationItem = {
  key: AdminDestination;
  label: string;
  group: AdminGroup;
  icon: React.ReactNode;
  rootOnly?: boolean;
  hiddenForIssuer?: boolean;
  publicLinksPermission?: boolean;
};

const GROUPS: Array<{ key: AdminGroup; label: string }> = [
  { key: "operations", label: "Operations" },
  { key: "publishing", label: "Publishing" },
  { key: "governance", label: "Governance" },
  { key: "system", label: "System" },
];

const ITEMS: NavigationItem[] = [
  { key: "events", label: "Events", group: "operations", icon: <Plus size={15} />, hiddenForIssuer: true },
  { key: "users", label: "Users", group: "operations", icon: <Users size={15} /> },
  { key: "history", label: "Event history", group: "operations", icon: <History size={15} /> },
  { key: "announcements", label: "Announcements", group: "publishing", icon: <Megaphone size={15} /> },
  { key: "public-links", label: "Public schedule links", group: "publishing", icon: <Share2 size={15} />, publicLinksPermission: true },
  { key: "policies", label: "Policies & notices", group: "governance", icon: <FileCheck2 size={15} />, rootOnly: true },
  { key: "privacy", label: "Deletion evidence", group: "governance", icon: <Shield size={15} />, rootOnly: true },
  { key: "audit", label: "Audit log", group: "governance", icon: <FileText size={15} />, hiddenForIssuer: true },
  { key: "security", label: "Security", group: "system", icon: <Shield size={15} />, rootOnly: true },
  { key: "ha", label: "High availability", group: "system", icon: <Activity size={15} />, rootOnly: true },
];

function destinationHref(destination: AdminDestination): string {
  return destination === "policies" ? "/admin/governance" : `/admin?tab=${destination}`;
}

export function AdminNavigation({
  active,
  isRootAdmin,
  isIssuerOnly,
  canManagePublicLinks,
  onSelect,
}: {
  active: AdminDestination;
  isRootAdmin: boolean;
  isIssuerOnly: boolean;
  canManagePublicLinks: boolean;
  onSelect?: (tab: AdminTab) => void;
}) {
  const router = useRouter();
  const visibleItems = ITEMS.filter((item) => {
    if (item.rootOnly && !isRootAdmin) return false;
    if (item.hiddenForIssuer && isIssuerOnly) return false;
    if (item.publicLinksPermission && !canManagePublicLinks) return false;
    return true;
  });
  const activeGroup = ITEMS.find((item) => item.key === active)?.group ?? "operations";
  const visibleGroups = GROUPS.filter((group) => visibleItems.some((item) => item.group === group.key));
  const secondaryItems = visibleItems.filter((item) => item.group === activeGroup);

  const navigate = (destination: AdminDestination) => {
    if (destination !== "policies" && onSelect) {
      onSelect(destination);
      return;
    }
    router.push(destinationHref(destination));
  };

  const selectGroup = (group: AdminGroup) => {
    const currentInGroup = visibleItems.some((item) => item.group === group && item.key === active);
    if (currentInGroup) return;
    const first = visibleItems.find((item) => item.group === group);
    if (first) navigate(first.key);
  };

  return (
    <nav aria-label="Administration sections" className="space-y-2">
      <div className="hidden flex-wrap gap-1 rounded-xl border border-gray-200 bg-white p-1.5 shadow-sm dark:border-gray-700 dark:bg-gray-800 md:flex">
        {visibleGroups.map((group) => (
          <button
            key={group.key}
            type="button"
            onClick={() => selectGroup(group.key)}
            aria-current={activeGroup === group.key ? "page" : undefined}
            className={`rounded-lg px-4 py-2 text-sm font-semibold transition-colors ${
              activeGroup === group.key
                ? "bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900"
                : "text-gray-600 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-700 dark:hover:text-white"
            }`}
          >
            {group.label}
          </button>
        ))}
      </div>

      <div className="hidden flex-wrap gap-1 border-b border-gray-200 pb-2 dark:border-gray-700 md:flex">
        {secondaryItems.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => navigate(item.key)}
            aria-current={active === item.key ? "page" : undefined}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
              active === item.key
                ? "bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300"
                : "text-gray-500 hover:bg-gray-100 hover:text-gray-800 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100"
            }`}
          >
            {item.icon}
            {item.label}
          </button>
        ))}
      </div>

      <div className="grid gap-2 md:hidden">
        <label className="text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
          Administration area
          <select
            value={activeGroup}
            onChange={(event) => selectGroup(event.target.value as AdminGroup)}
            className="mt-1 min-h-11 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
          >
            {visibleGroups.map((group) => <option key={group.key} value={group.key}>{group.label}</option>)}
          </select>
        </label>
        <label className="text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
          Page
          <select
            value={active}
            onChange={(event) => navigate(event.target.value as AdminDestination)}
            className="mt-1 min-h-11 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
          >
            {secondaryItems.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
          </select>
        </label>
      </div>
    </nav>
  );
}

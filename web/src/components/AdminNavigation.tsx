"use client";

import { useRouter } from "next/navigation";
import {
  Activity,
  CalendarRange,
  ChevronRight,
  FileCheck2,
  FileText,
  History,
  KeyRound,
  Megaphone,
  Plus,
  RadioTower,
  Settings2,
  Share2,
  Shield,
  Scale,
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

export type AdminDestination = AdminTab | "policies" | "trust";

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

const GROUPS: Array<{
  key: AdminGroup;
  label: string;
  description: string;
  icon: React.ReactNode;
}> = [
  { key: "operations", label: "Operations", description: "Events and access", icon: <CalendarRange size={17} /> },
  { key: "publishing", label: "Publishing", description: "Updates and links", icon: <RadioTower size={17} /> },
  { key: "governance", label: "Governance", description: "Policy and evidence", icon: <Scale size={17} /> },
  { key: "system", label: "System", description: "Security and resilience", icon: <Settings2 size={17} /> },
];

const ITEMS: NavigationItem[] = [
  { key: "events", label: "Events", group: "operations", icon: <Plus size={16} />, hiddenForIssuer: true },
  { key: "users", label: "Users", group: "operations", icon: <Users size={16} /> },
  { key: "history", label: "Event history", group: "operations", icon: <History size={16} /> },
  { key: "announcements", label: "Announcements", group: "publishing", icon: <Megaphone size={16} /> },
  { key: "public-links", label: "Public schedule links", group: "publishing", icon: <Share2 size={16} />, publicLinksPermission: true },
  { key: "policies", label: "Policies & notices", group: "governance", icon: <FileCheck2 size={16} />, rootOnly: true },
  { key: "trust", label: "Trust & keys", group: "governance", icon: <KeyRound size={16} />, rootOnly: true },
  { key: "privacy", label: "Deletion evidence", group: "governance", icon: <Shield size={16} />, rootOnly: true },
  { key: "audit", label: "Audit log", group: "governance", icon: <FileText size={16} />, hiddenForIssuer: true },
  { key: "security", label: "Security", group: "system", icon: <Shield size={16} />, rootOnly: true },
  { key: "ha", label: "High availability", group: "system", icon: <Activity size={16} />, rootOnly: true },
];

function destinationHref(destination: AdminDestination): string {
  if (destination === "policies") return "/admin/governance";
  if (destination === "trust") return "/admin/governance/trust";
  return `/admin?tab=${destination}`;
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
  const currentGroup = GROUPS.find((group) => group.key === activeGroup);

  const navigate = (destination: AdminDestination) => {
    if (destination !== "policies" && destination !== "trust" && onSelect) {
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

  if (isRootAdmin) {
    return (
      <nav aria-label="Root administration" className="min-w-0">
        <div className="lg:hidden">
          <label className="block text-xs font-semibold uppercase tracking-[0.14em] text-gray-500 dark:text-gray-400">
            Administration page
            <select
              value={active}
              onChange={(event) => navigate(event.target.value as AdminDestination)}
              className="mt-2 min-h-11 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-base font-medium text-gray-900 shadow-sm dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
            >
              {visibleGroups.map((group) => (
                <optgroup key={group.key} label={group.label}>
                  {visibleItems.filter((item) => item.group === group.key).map((item) => (
                    <option key={item.key} value={item.key}>{item.label}</option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
        </div>

        <div className="sticky top-24 hidden overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-800 lg:block">
          <div className="border-b border-gray-100 px-4 py-4 dark:border-gray-700">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-400 dark:text-gray-500">Workspace</p>
            <p className="mt-1 text-base font-semibold text-gray-900 dark:text-gray-100">Root administration</p>
          </div>

          <div className="space-y-1 p-2" aria-label="Administration areas">
            {visibleGroups.map((group) => (
              <button
                key={group.key}
                type="button"
                onClick={() => selectGroup(group.key)}
                aria-label={group.label}
                aria-current={activeGroup === group.key ? "page" : undefined}
                className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-colors ${
                  activeGroup === group.key
                    ? "bg-slate-100 text-slate-950 dark:bg-gray-700 dark:text-white"
                    : "text-gray-600 hover:bg-gray-50 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-700/60 dark:hover:text-white"
                }`}
              >
                <span className={activeGroup === group.key ? "text-blue-600 dark:text-blue-300" : "text-gray-400 dark:text-gray-500"}>{group.icon}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold">{group.label}</span>
                  <span className="block truncate text-xs font-normal text-gray-500 dark:text-gray-400">{group.description}</span>
                </span>
                <ChevronRight size={15} className="text-gray-300 dark:text-gray-600" aria-hidden="true" />
              </button>
            ))}
          </div>

          <div className="border-t border-gray-100 px-2 pb-3 pt-3 dark:border-gray-700">
            <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-gray-400 dark:text-gray-500">{currentGroup?.label}</p>
            <div className="space-y-0.5">
              {secondaryItems.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => navigate(item.key)}
                  aria-current={active === item.key ? "page" : undefined}
                  className={`flex min-h-10 w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                    active === item.key
                      ? "bg-blue-50 font-semibold text-blue-800 dark:bg-blue-950/50 dark:text-blue-200"
                      : "font-medium text-gray-600 hover:bg-gray-50 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-700/60 dark:hover:text-white"
                  }`}
                >
                  <span className={active === item.key ? "text-blue-600 dark:text-blue-300" : "text-gray-400 dark:text-gray-500"}>{item.icon}</span>
                  <span>{item.label}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </nav>
    );
  }

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

      <label className="grid gap-1 text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400 md:hidden">
        Page
        <select
          value={active}
          onChange={(event) => navigate(event.target.value as AdminDestination)}
          className="min-h-11 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
        >
          {visibleItems.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
        </select>
      </label>
    </nav>
  );
}

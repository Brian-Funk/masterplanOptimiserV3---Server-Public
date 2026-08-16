"use client";

import { CalendarDays, CalendarRange, Megaphone, MoreHorizontal, Users } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { useAuth } from "@/contexts/AuthContext";
import { MobileBottomNavigation, type MobileNavigationItem } from "@/components/MobileBottomNavigation";

const SHELL_ROUTES = new Set(["/calendar", "/admin", "/account/security", "/more", "/unassigned"]);

/** Keep the same four phone destinations mounted throughout an event session. */
export function AuthenticatedMobileShell() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  const { user, isLoading } = useAuth();

  if (
    isLoading
    || !user
    || user.is_root_admin
    || !user.event_id
    || !SHELL_ROUTES.has(pathname)
  ) {
    return null;
  }

  const eventId = user.event_id;
  const calendarView = searchParams.get("view") ?? (user.linked_person_id ? "mine" : "all");
  const adminTab = searchParams.get("tab") ?? "users";
  const isEventManager = user.is_admin || user.is_issuer;
  const navigate = (href: string) => () => router.push(href);

  const items: MobileNavigationItem[] = isEventManager
    ? [
        {
          id: "schedule",
          label: "Schedule",
          icon: <CalendarDays size={20} />,
          active: pathname === "/calendar",
          onSelect: navigate(`/calendar?event=${eventId}&view=all`),
        },
        {
          id: "people",
          label: "People",
          icon: <Users size={20} />,
          active: pathname === "/admin" && adminTab === "users",
          onSelect: navigate(`/admin?tab=users&event=${eventId}`),
        },
        {
          id: "updates",
          label: "Updates",
          icon: <Megaphone size={20} />,
          active: pathname === "/admin" && adminTab === "announcements",
          onSelect: navigate(`/admin?tab=announcements&event=${eventId}`),
        },
        {
          id: "more",
          label: "More",
          icon: <MoreHorizontal size={20} />,
          active: pathname === "/more" || pathname === "/account/security",
          onSelect: navigate(`/more?event=${eventId}`),
        },
      ]
    : [
        {
          id: "schedule",
          label: "Schedule",
          icon: <CalendarDays size={20} />,
          active: pathname === "/calendar" && calendarView === "all",
          onSelect: navigate(`/calendar?event=${eventId}&view=all`),
        },
        ...(user.linked_person_id
          ? [{
              id: "mine",
              label: "My schedule",
              icon: <Users size={20} />,
              active: pathname === "/calendar" && calendarView === "mine",
              onSelect: navigate(`/calendar?event=${eventId}&view=mine`),
            }]
          : []),
        {
          id: "programme",
          label: "Programme",
          icon: <CalendarRange size={20} />,
          active: pathname === "/calendar" && calendarView === "programme",
          onSelect: navigate(`/calendar?event=${eventId}&view=programme`),
        },
        {
          id: "more",
          label: "More",
          icon: <MoreHorizontal size={20} />,
          active: pathname === "/more" || pathname === "/account/security",
          onSelect: navigate(`/more?event=${eventId}`),
        },
      ];

  return <MobileBottomNavigation items={items} elevated />;
}

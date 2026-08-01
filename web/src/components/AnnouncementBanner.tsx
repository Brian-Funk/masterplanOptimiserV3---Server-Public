"use client";

import { useState, useEffect } from "react";
import { Megaphone, X } from "lucide-react";
import { apiFetch } from "@/lib/api";

interface Announcement {
  id: number;
  title: string;
  body: string | null;
  created_by: string | null;
  created_at: string;
}

/** Props for `AnnouncementBanner`. */
export interface AnnouncementBannerProps {
  eventId: number;
}

/**
 * Scrollable announcement banner for the calendar page.
 * Shows the most recent announcements from admins.
 */
export function AnnouncementBanner({ eventId }: AnnouncementBannerProps) {
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (!eventId) return;

    apiFetch(`/api/v1/notifications/announcements/${eventId}`)
      .then((res) => {
        if (res.ok) return res.json();
        return [];
      })
      .then((data: Announcement[]) => setAnnouncements(data))
      .catch(() => {});
  }, [eventId]);

  // Load dismissed set from sessionStorage
  useEffect(() => {
    try {
      const stored = sessionStorage.getItem(`mp-dismissed-${eventId}`);
      if (stored) setDismissed(new Set(JSON.parse(stored)));
    } catch {
      // ignore
    }
  }, [eventId]);

  const handleDismiss = (id: number) => {
    setDismissed((prev) => {
      const next = new Set(prev);
      next.add(id);
      try {
        sessionStorage.setItem(
          `mp-dismissed-${eventId}`,
          JSON.stringify([...next]),
        );
      } catch {
        // ignore
      }
      return next;
    });
  };

  const visible = announcements.filter((a) => !dismissed.has(a.id));
  if (visible.length === 0) return null;

  return (
    <div className="space-y-2 mb-4">
      {visible.map((ann) => (
        <div
          key={ann.id}
          className="flex items-start gap-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg px-4 py-3"
        >
          <Megaphone
            size={18}
            className="text-blue-600 dark:text-blue-400 flex-shrink-0 mt-0.5"
          />
          <div className="flex-1 min-w-0">
            <p className="font-medium text-blue-900 dark:text-blue-100 text-sm">
              {ann.title}
            </p>
            {ann.body && (
              <p className="text-blue-700 dark:text-blue-300 text-sm mt-0.5">
                {ann.body}
              </p>
            )}
          </div>
          <button
            onClick={() => handleDismiss(ann.id)}
            className="p-1 rounded text-blue-400 hover:text-blue-600 dark:text-blue-500 dark:hover:text-blue-300 transition-colors flex-shrink-0"
            aria-label="Dismiss announcement"
          >
            <X size={16} />
          </button>
        </div>
      ))}
    </div>
  );
}

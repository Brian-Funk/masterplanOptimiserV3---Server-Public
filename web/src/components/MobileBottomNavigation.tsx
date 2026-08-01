"use client";

import type { ReactNode } from "react";

/** A destination rendered in the phone bottom navigation. */
export interface MobileNavigationItem {
  id: string;
  label: string;
  icon: ReactNode;
  active?: boolean;
  onSelect: () => void;
}

/** Props for the role-aware phone bottom navigation. */
export interface MobileBottomNavigationProps {
  items: MobileNavigationItem[];
  ariaLabel?: string;
  elevated?: boolean;
}

/** Render up to four stable phone destinations with device safe-area spacing. */
export function MobileBottomNavigation({
  items,
  ariaLabel = "Primary navigation",
  elevated = false,
}: MobileBottomNavigationProps) {
  const visibleItems = items.slice(0, 4);

  return (
    <nav
      aria-label={ariaLabel}
      className={`safe-area-bottom fixed inset-x-0 bottom-0 z-40 border-t border-gray-200 bg-white/95 backdrop-blur-md dark:border-gray-700 dark:bg-gray-900/95 md:hidden ${
        elevated ? "shadow-[0_-8px_24px_rgba(15,23,42,0.10)]" : ""
      }`}
    >
      <div
        className="mx-auto grid h-16 max-w-lg"
        style={{ gridTemplateColumns: `repeat(${visibleItems.length}, minmax(0, 1fr))` }}
      >
        {visibleItems.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={item.onSelect}
            aria-current={item.active ? "page" : undefined}
            className={`flex min-w-0 flex-col items-center justify-center gap-1 px-1 text-[11px] font-medium transition-colors ${
              item.active
                ? "text-blue-700 dark:text-blue-300"
                : "text-gray-500 dark:text-gray-400"
            }`}
          >
            <span
              className={`flex h-7 w-11 items-center justify-center rounded-full transition-colors ${
                item.active ? "bg-blue-50 dark:bg-blue-900/35" : ""
              }`}
              aria-hidden="true"
            >
              {item.icon}
            </span>
            <span className="max-w-full truncate">{item.label}</span>
          </button>
        ))}
      </div>
    </nav>
  );
}

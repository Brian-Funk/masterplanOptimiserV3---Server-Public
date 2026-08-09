"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { Logo } from "@/components/Logo";
import { PUBLIC_TEXT_LINK_CLASS } from "@/lib/publicLinks";

export type PublicInformationSection =
  | "about"
  | "security"
  | "licence"
  | "third-party-notices"
  | "disclaimer";

const navigation: Array<{ href: string; label: string; section?: PublicInformationSection }> = [
  { href: "/about", label: "About", section: "about" },
  { href: "/security", label: "Security", section: "security" },
  { href: "/licence", label: "Licence", section: "licence" },
  { href: "/third-party-notices", label: "Notices", section: "third-party-notices" },
  { href: "/disclaimer", label: "Disclaimer", section: "disclaimer" },
  { href: "/privacy", label: "Legal centre" },
];

/** Shared, calm presentation for public project and deployment information. */
export function PublicInformationShell({
  section,
  title,
  lead,
  children,
}: {
  section: PublicInformationSection;
  title: string;
  lead: string;
  children: ReactNode;
}) {
  return (
    <main className="min-h-screen bg-[var(--color-surface-alt)] px-3 py-3 text-[var(--color-foreground)] sm:px-6 sm:py-8">
      <article className="mx-auto flex min-h-[calc(100vh-1.5rem)] max-w-5xl flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-800 sm:min-h-[calc(100vh-4rem)]">
        <header className="border-t-4 border-t-blue-600 px-5 py-6 dark:border-t-blue-400 sm:px-9 sm:py-8">
          <div className="mb-7 flex items-center gap-3">
            <Logo height={46} />
            <div className="min-w-0">
              <p className="font-semibold text-gray-900 dark:text-gray-100">Masterplan Optimiser</p>
              <p className="text-sm text-gray-500 dark:text-gray-400">Open-source scheduling platform</p>
            </div>
          </div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-[0.14em] text-blue-600 dark:text-blue-300">
            Public information
          </p>
          <h1 className="max-w-3xl text-3xl font-semibold tracking-tight text-gray-900 dark:text-gray-100 sm:text-4xl">
            {title}
          </h1>
          <p className="mt-3 max-w-3xl text-base text-gray-600 dark:text-gray-400">{lead}</p>
        </header>

        <nav
          className="flex flex-wrap gap-1 border-y border-gray-200 bg-gray-50 px-4 py-2.5 dark:border-gray-700 dark:bg-gray-700/60 sm:px-7"
          aria-label="Public information"
        >
          {navigation.map((item) => {
            const active = item.section === section;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`rounded-lg px-2.5 py-1.5 text-sm font-semibold no-underline transition-colors ${
                  active
                    ? "bg-blue-50 text-blue-700 dark:bg-blue-950/60 dark:text-blue-200"
                    : "text-gray-600 hover:bg-white hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-800 dark:hover:text-white"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="flex-1 px-5 py-8 sm:px-9 sm:py-10">{children}</div>

        <footer className="flex flex-col gap-1 border-t border-gray-200 bg-gray-50 px-5 py-4 text-xs text-gray-500 dark:border-gray-700 dark:bg-gray-700/60 dark:text-gray-400 sm:flex-row sm:items-center sm:justify-between sm:px-9">
          <span>Masterplan Optimiser</span>
          <Link className={PUBLIC_TEXT_LINK_CLASS} href="/privacy">
            Open the instance legal centre
          </Link>
        </footer>
      </article>
    </main>
  );
}

export function PublicInformationSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="mt-7 border-t border-gray-200 pt-7 first:mt-0 first:border-t-0 dark:border-gray-700">
      <h2 className="mb-3 text-xl font-semibold tracking-tight text-gray-900 dark:text-gray-100">{title}</h2>
      <div className="max-w-[78ch] space-y-3 text-gray-600 dark:text-gray-300">{children}</div>
    </section>
  );
}

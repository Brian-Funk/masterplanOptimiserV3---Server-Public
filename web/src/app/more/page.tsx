"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { History, LogOut, RefreshCw, Shield, Share2 } from "lucide-react";

import { useAuth } from "@/contexts/AuthContext";
import { ThemeToggle } from "@/components/ThemeToggle";
import { NotificationBell } from "@/components/NotificationBell";
import { OfflineScheduleSettings } from "@/components/OfflineScheduleSettings";
import { DeleteMyDataLink } from "@/components/DeleteMyDataLink";

const INFORMATION_LINKS = [
  ["About", "/about"], ["Privacy", "/privacy"], ["Legal", "/legal"],
  ["Terms", "/terms"], ["AGPL-3.0-only", "/licence"],
  ["Notices", "/third-party-notices"], ["Security", "/security"],
  ["Disclaimer", "/disclaimer"],
] as const;

/** Stable phone destination for account, event, offline, and information links. */
export default function MorePage() {
  const router = useRouter();
  const { user, isLoading, logout, isLoggingOut } = useAuth();
  const eventId = Number(user?.event_id ?? 0);
  const [dataPolicy, setDataPolicy] = useState<{
    version: number;
    sha256: string;
  } | null>(null);

  useEffect(() => {
    if (!isLoading && user?.is_root_admin) router.replace("/admin");
  }, [isLoading, router, user?.is_root_admin]);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/v1/governance/public", {
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => response.ok ? response.json() : null)
      .then((notice) => {
        if (
          notice?.configured
          && Number.isInteger(notice.version)
          && typeof notice.content_sha256 === "string"
          && /^[0-9a-f]{64}$/.test(notice.content_sha256)
        ) {
          setDataPolicy({
            version: notice.version,
            sha256: notice.content_sha256,
          });
        }
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
      });
    return () => controller.abort();
  }, []);

  if (isLoading || !user || !eventId) {
    return <main className="flex min-h-screen items-center justify-center bg-gray-50 dark:bg-gray-900"><RefreshCw className="animate-spin text-blue-600" aria-label="Loading" /></main>;
  }
  if (user.is_root_admin) {
    return null;
  }

  const eventManager = user.is_admin || user.is_issuer;
  const handleLogout = async () => {
    if (await logout()) router.replace("/login");
  };

  return (
    <div className="mobile-page-with-nav min-h-screen bg-gray-50 text-gray-900 dark:bg-gray-900 dark:text-gray-100">
      <header className="sticky top-0 z-20 border-b border-gray-200 bg-white/95 px-4 py-3 backdrop-blur dark:border-gray-700 dark:bg-gray-800/95">
        <div className="mx-auto max-w-3xl"><h1 className="text-xl font-semibold">More</h1><p className="text-sm text-gray-500 dark:text-gray-400">Account, event and information</p></div>
      </header>
      <main className="mx-auto max-w-3xl space-y-5 px-4 py-5">
        {eventManager && (
          <section className="grid grid-cols-2 gap-3">
            <Link href={`/admin?tab=history&event=${eventId}`} className="flex min-h-12 items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 text-sm font-medium dark:border-gray-700 dark:bg-gray-800"><History size={18} /> History</Link>
            {user.is_issuer && <Link href={`/admin?tab=public-links&event=${eventId}`} className="flex min-h-12 items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 text-sm font-medium dark:border-gray-700 dark:bg-gray-800"><Share2 size={18} /> Public links</Link>}
          </section>
        )}

        <section className="space-y-2 rounded-2xl border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-800">
          <h2 className="font-semibold">Account</h2>
          <Link href="/account/security" className="flex min-h-11 items-center gap-3 rounded-xl px-3 text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700"><Shield size={18} /> Account security</Link>
          <div className="flex min-h-11 items-center justify-between rounded-xl px-3"><span className="text-sm font-medium">Appearance</span><ThemeToggle /></div>
          <div className="flex min-h-11 items-center justify-between rounded-xl px-3"><span className="text-sm font-medium">Notifications</span><NotificationBell eventId={eventId} /></div>
          <button type="button" disabled={isLoggingOut} aria-busy={isLoggingOut} onClick={() => void handleLogout()} className="flex min-h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-sm font-medium text-red-600 hover:bg-red-50 disabled:opacity-60 dark:text-red-400 dark:hover:bg-red-950"><LogOut size={18} /> {isLoggingOut ? "Logging out…" : "Log out"}</button>
        </section>

        <OfflineScheduleSettings eventId={eventId} />

        <section className="space-y-2 rounded-2xl border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-800">
          <h2 className="font-semibold">Your data</h2>
          <DeleteMyDataLink standalone />
        </section>

        <section className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-800">
          <h2 className="font-semibold">Information</h2>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <a
              href={dataPolicy ? `/api/v1/governance/public/versions/${dataPolicy.version}/data-policy.html` : "/data-policy"}
              className="col-span-2 flex min-h-11 items-center justify-between gap-3 rounded-xl px-3 py-2 text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700"
            >
              <span>Permitted-data policy{dataPolicy ? ` v${dataPolicy.version}` : ""}</span>
              {dataPolicy && <code className="shrink-0 text-xs font-normal text-gray-500 dark:text-gray-400">{dataPolicy.sha256.slice(0, 12)}...</code>}
            </a>
            {INFORMATION_LINKS.map(([label, href]) => <Link key={href} href={href} className="min-h-11 rounded-xl px-3 py-2 text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700">{label}</Link>)}
            <a href={process.env.NEXT_PUBLIC_SOURCE_URL} rel="noreferrer" target="_blank" className="min-h-11 rounded-xl px-3 py-2 text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700">Source {process.env.NEXT_PUBLIC_SOURCE_REVISION?.slice(0, 12)}</a>
          </div>
          <p className="mt-3 text-xs text-gray-500 dark:text-gray-400">Masterplan Optimiser self-hosted instance · v{process.env.NEXT_PUBLIC_APP_VERSION}</p>
        </section>
      </main>
    </div>
  );
}

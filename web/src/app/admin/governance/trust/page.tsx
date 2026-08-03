"use client";

import { AdminNavigation } from "@/components/AdminNavigation";
import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";
import { TrustKeysPanel } from "@/components/TrustKeysPanel";
import { Card } from "@/components/ui/Card";
import { useAuth } from "@/contexts/AuthContext";

export default function TrustKeysPage() {
  const { user, isLoading } = useAuth();
  if (isLoading) return <main className="flex min-h-screen items-center justify-center bg-gray-50 dark:bg-gray-900"><p>Loading trust status...</p></main>;
  if (!user?.is_root_admin) return <main className="flex min-h-screen items-center justify-center bg-gray-50 p-8 dark:bg-gray-900"><Card className="max-w-md p-6"><h1 className="text-xl font-semibold">Root access required</h1><p className="mt-2 text-sm text-gray-500">Only the root administrator can activate or revoke deployment trust.</p></Card></main>;

  return <div className="min-h-screen bg-gray-50 text-gray-900 dark:bg-gray-900 dark:text-gray-100">
    <header className="border-b border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800"><div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3"><Logo height={32} href="https://info.mp-opt.net" /><ThemeToggle /></div></header>
    <main className="mx-auto max-w-6xl space-y-6 px-4 py-8">
      <AdminNavigation active="trust" isRootAdmin isIssuerOnly={false} canManagePublicLinks />
      <TrustKeysPanel />
    </main>
  </div>;
}

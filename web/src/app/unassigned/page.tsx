"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { CalendarClock, RefreshCw, Shield } from "lucide-react";

import { useAuth } from "@/contexts/AuthContext";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";

export default function UnassignedPage() {
  const router = useRouter();
  const { user, isLoading, logout } = useAuth();

  useEffect(() => {
    if (isLoading) return;
    if (!user) router.replace("/login");
    else if (user.is_root_admin || user.is_admin) router.replace("/admin");
    else if (user.event_id) router.replace(`/calendar?event=${user.event_id}`);
  }, [isLoading, router, user]);

  if (isLoading || !user || user.event_id || user.is_root_admin || user.is_admin) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-50 dark:bg-gray-900">
        <RefreshCw className="animate-spin text-blue-600" aria-label="Loading account assignment" />
      </main>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 dark:bg-gray-900 dark:text-gray-100">
      <header className="border-b border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800">
        <div className="mx-auto flex max-w-2xl items-center justify-between px-4 py-3">
          <Logo height={32} href="https://info.mp-opt.net" />
          <ThemeToggle />
        </div>
      </header>
      <main className="mx-auto max-w-2xl px-4 py-12">
        <Card className="p-6 text-center sm:p-8">
          <CalendarClock className="mx-auto text-blue-600" size={36} aria-hidden="true" />
          <h1 className="mt-4 text-2xl font-semibold">Waiting for event assignment</h1>
          <p className="mt-3 text-sm text-gray-600 dark:text-gray-300">
            Your account is active, but it has not been assigned to an event yet.
            Contact the event organiser and sign in again after they assign it.
          </p>
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            Signed in as {user.display_name}.
          </p>
          <div className="mt-6 flex flex-col justify-center gap-2 sm:flex-row">
            <Button variant="outline" onClick={() => router.push("/account/security")}>
              <Shield size={16} className="mr-1" /> Account security
            </Button>
            <Button
              onClick={async () => {
                if (await logout()) router.replace("/login");
              }}
            >
              Sign out
            </Button>
          </div>
        </Card>
      </main>
    </div>
  );
}

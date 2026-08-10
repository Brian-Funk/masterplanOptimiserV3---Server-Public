"use client";

import { LogOut, RefreshCw, Shield } from "lucide-react";
import { useRouter } from "next/navigation";

import { ThemeToggle } from "@/components/ThemeToggle";
import { useAuth } from "@/contexts/AuthContext";

/** Stable authenticated header actions shared by every normal application role. */
export function AuthenticatedHeaderActions({
  iconSize = 18,
  accountSecurityActive = false,
}: {
  iconSize?: number;
  accountSecurityActive?: boolean;
}) {
  const router = useRouter();
  const { user, logout, isLoggingOut } = useAuth();

  const handleLogout = async () => {
    if (await logout()) router.replace("/login");
  };

  return (
    <div className="flex items-center gap-1" aria-label="Account actions">
      <ThemeToggle />
      {user && (
        <button
          type="button"
          onClick={() => router.push("/account/security")}
          aria-current={accountSecurityActive ? "page" : undefined}
          className={`rounded-lg p-2 transition-colors ${
            accountSecurityActive
              ? "bg-blue-50 text-blue-700 dark:bg-blue-950/60 dark:text-blue-200"
              : "text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700"
          }`}
          aria-label="Open account security"
          title="Account security"
        >
          <Shield size={iconSize} />
        </button>
      )}
      {user && (
        <button
          type="button"
          onClick={handleLogout}
          disabled={isLoggingOut}
          aria-busy={isLoggingOut}
          className="rounded-lg p-2 text-gray-500 transition-colors hover:bg-gray-100 disabled:cursor-wait disabled:opacity-60 dark:text-gray-400 dark:hover:bg-gray-700"
          aria-label={isLoggingOut ? "Logging out" : "Logout"}
          title={isLoggingOut ? "Logging out…" : "Logout"}
        >
          {isLoggingOut ? <RefreshCw size={iconSize} className="animate-spin" /> : <LogOut size={iconSize} />}
        </button>
      )}
    </div>
  );
}

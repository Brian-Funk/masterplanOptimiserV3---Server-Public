"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Clock3, Key, Laptop, RefreshCw, Shield, Trash2 } from "lucide-react";

import { useAuth } from "@/contexts/AuthContext";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";
import { PasskeyManager } from "@/components/PasskeyManager";

interface ActiveSession {
  id: number;
  current: boolean;
  device: string;
  created_at: string;
  last_seen_at: string | null;
  expires_at: string;
}

function displayTime(value: string | null): string {
  if (!value) return "Not recorded";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "Not recorded" : parsed.toLocaleString();
}

export default function AccountSecurityPage() {
  const router = useRouter();
  const { user, isLoading: authLoading } = useAuth();
  const [sessions, setSessions] = useState<ActiveSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revoking, setRevoking] = useState<number | null>(null);
  const [showPasskeys, setShowPasskeys] = useState(false);

  const backHref = useMemo(() => {
    if (!user) return "/login";
    if (user.is_root_admin || user.is_admin || user.is_issuer) return "/admin";
    return user.event_id ? `/calendar?event=${user.event_id}` : "/";
  }, [user]);

  const loadSessions = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch("/api/v1/auth/sessions");
      if (!response.ok) throw new Error("Active sessions could not be loaded.");
      const payload: unknown = await response.json();
      if (!Array.isArray(payload)) throw new Error("The session response was invalid.");
      setSessions(payload as ActiveSession[]);
    } catch (sessionError) {
      setError(
        sessionError instanceof Error
          ? sessionError.message
          : "Active sessions could not be loaded.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (authLoading) return;
    if (!user) {
      router.replace("/login");
      return;
    }
    void loadSessions();
  }, [authLoading, loadSessions, router, user]);

  const revokeSession = async (session: ActiveSession) => {
    setRevoking(session.id);
    setError("");
    try {
      const response = await apiFetch(`/api/v1/auth/sessions/${session.id}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || "The session could not be revoked.");
      }
      if (session.current) {
        router.replace("/login");
        return;
      }
      setSessions((current) => current.filter((item) => item.id !== session.id));
    } catch (sessionError) {
      setError(
        sessionError instanceof Error
          ? sessionError.message
          : "The session could not be revoked.",
      );
    } finally {
      setRevoking(null);
    }
  };

  if (authLoading || !user) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-gray-50 dark:bg-gray-900">
        <RefreshCw className="animate-spin text-blue-600" aria-label="Loading account security" />
      </main>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 dark:bg-gray-900 dark:text-gray-100">
      <header className="sticky top-0 z-10 border-b border-gray-200 bg-white/95 shadow-sm backdrop-blur dark:border-gray-700 dark:bg-gray-800/95">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-4 py-3 sm:px-6">
          <Logo height={32} href="https://info.mp-opt.net" />
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => router.push(backHref)}
              className="inline-flex min-h-10 items-center gap-2 rounded-lg px-3 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-100 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-700 dark:hover:text-white"
            >
              <ArrowLeft size={17} /> Back
            </button>
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-4xl space-y-6 px-4 py-6 sm:px-6 sm:py-8">
        <div className="flex items-start gap-4">
          <div className="rounded-xl bg-blue-50 p-3 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300">
            <Shield size={24} aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight">Account security</h1>
            <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
              Manage passkeys and signed-in browsers for {user.display_name}.
            </p>
          </div>
        </div>

        {error && <div role="alert" className="rounded-xl border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">{error}</div>}

        <Card className="p-5 sm:p-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="flex items-center gap-2">
                <Key size={18} className="text-blue-600 dark:text-blue-400" />
                <h2 className="text-base font-semibold">Passkeys</h2>
              </div>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-gray-600 dark:text-gray-300">
                Add, rename, or remove your passkeys. Sensitive changes require reauthentication, and the final passkey cannot be removed.
              </p>
            </div>
            <Button className="shrink-0" variant="outline" onClick={() => setShowPasskeys(true)}>
              Manage passkeys
            </Button>
          </div>
        </Card>

        <Card className="p-5 sm:p-6">
          <div className="mb-5">
            <div className="flex items-center gap-2">
              <Laptop size={18} className="text-blue-600 dark:text-blue-400" />
              <h2 className="text-base font-semibold">Active sessions</h2>
            </div>
            <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">
              Review browsers signed in to this account. Device labels contain only a coarse browser and operating-system family; versions, models, and raw IP details are not shown.
            </p>
          </div>
          {loading ? (
            <p className="flex items-center gap-2 text-sm text-gray-500"><RefreshCw size={16} className="animate-spin" /> Loading sessions...</p>
          ) : sessions.length === 0 ? (
            <p className="text-sm text-gray-500">No active sessions were returned.</p>
          ) : (
            <div className="space-y-3">
              {sessions.map((session) => (
                <section key={session.id} className="rounded-xl border border-gray-200 bg-gray-50/70 p-4 dark:border-gray-700 dark:bg-gray-900/30">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="flex flex-wrap items-center gap-2 font-medium">{session.device} {session.current && <span className="rounded-md bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-800 dark:bg-blue-900 dark:text-blue-200">Current</span>}</p>
                      <p className="mt-2 flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400"><Clock3 size={14} /> Last active {displayTime(session.last_seen_at)}</p>
                      <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Started {displayTime(session.created_at)}. Expires {displayTime(session.expires_at)}.</p>
                    </div>
                    <Button variant="danger" size="sm" disabled={revoking !== null} onClick={() => void revokeSession(session)}>
                      {revoking === session.id ? <RefreshCw size={15} className="mr-1 animate-spin" /> : <Trash2 size={15} className="mr-1" />}
                      {session.current ? "Revoke and log out" : "Revoke"}
                    </Button>
                  </div>
                </section>
              ))}
            </div>
          )}
        </Card>

        <p className="border-t border-gray-200 pt-4 text-xs leading-5 text-gray-500 dark:border-gray-700 dark:text-gray-400">Session and device metadata remains subject to the server retention settings. See the <a href="/privacy" className="font-medium underline underline-offset-2">privacy notice</a> for the current instance disclosure.</p>
      </main>
      <PasskeyManager open={showPasskeys} onClose={() => setShowPasskeys(false)} />
    </div>
  );
}

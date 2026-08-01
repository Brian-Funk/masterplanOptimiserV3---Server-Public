"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Clock3, Laptop, RefreshCw, Shield, Trash2 } from "lucide-react";

import { useAuth } from "@/contexts/AuthContext";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";

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
      <header className="border-b border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-3">
          <Logo height={32} href="https://info.mp-opt.net" />
          <ThemeToggle />
        </div>
      </header>
      <main className="mx-auto max-w-3xl space-y-5 px-4 py-8">
        <div>
          <button type="button" onClick={() => router.push(backHref)} className="mb-3 text-sm font-medium text-blue-700 hover:underline dark:text-blue-300">
            Back to Masterplan
          </button>
          <div className="flex items-center gap-3">
            <Shield className="text-blue-600" aria-hidden="true" />
            <div>
              <h1 className="text-2xl font-semibold">Account security</h1>
              <p className="text-sm text-gray-600 dark:text-gray-300">Review and revoke browsers signed in as {user.display_name}.</p>
            </div>
          </div>
        </div>

        {error && <div role="alert" className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">{error}</div>}

        <Card title="Active sessions">
          <p className="mb-4 text-sm text-gray-600 dark:text-gray-300">Device labels contain only a coarse browser and operating-system family. Version, model and raw IP details are not shown.</p>
          {loading ? (
            <p className="flex items-center gap-2 text-sm text-gray-500"><RefreshCw size={16} className="animate-spin" /> Loading sessions...</p>
          ) : sessions.length === 0 ? (
            <p className="text-sm text-gray-500">No active sessions were returned.</p>
          ) : (
            <div className="space-y-3">
              {sessions.map((session) => (
                <section key={session.id} className="rounded-lg border border-gray-200 p-4 dark:border-gray-700">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="flex items-center gap-2 font-medium"><Laptop size={17} /> {session.device} {session.current && <span className="rounded bg-blue-100 px-2 py-0.5 text-xs text-blue-800 dark:bg-blue-900 dark:text-blue-200">Current</span>}</p>
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

        <p className="text-xs text-gray-500 dark:text-gray-400">Session and device metadata remains subject to the server retention settings. See the <a href="/privacy" className="underline">privacy notice</a> for the current instance disclosure.</p>
      </main>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { startRegistration } from "@simplewebauthn/browser";

import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { getApiUrl } from "@/lib/environment";
import { hardNavigate } from "@/lib/hardNavigation";
import { passkeyErrorMessage } from "@/lib/passkeyError";

type State = "checking" | "ready" | "registering" | "already-done" | "error";

export default function BootstrapPage() {
  const [state, setState] = useState<State>("checking");
  const [error, setError] = useState("");
  const [bootstrapCode, setBootstrapCode] = useState("");
  const [policyAcknowledged, setPolicyAcknowledged] = useState(false);
  const [policy, setPolicy] = useState<{ version: string; sha256: string; text: string } | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const response = await fetch(`${getApiUrl()}/api/v1/passkey/bootstrap-status`, { credentials: "include", cache: "no-store" });
        if (!response.ok) throw new Error("Failed to check bootstrap status");
        const data = await response.json();
        setPolicy({ version: data.policy_version, sha256: data.policy_sha256, text: data.policy_text });
        if (data.needs_bootstrap) {
          if (!data.bootstrap_configured) throw new Error("Root bootstrap is not configured on the Server.");
          setState("ready");
        } else {
          setState("already-done");
        }
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Cannot reach the Server");
        setState("error");
      }
    })();
  }, []);

  const register = async () => {
    setState("registering"); setError("");
    try {
      const api = getApiUrl();
      const begin = await fetch(`${api}/api/v1/passkey/bootstrap/begin`, {
        method: "POST", credentials: "include", referrerPolicy: "no-referrer",
        headers: { "X-Bootstrap-Token": bootstrapCode },
      });
      const beginBody = await begin.json().catch(() => ({}));
      if (!begin.ok) throw new Error(passkeyErrorMessage(beginBody, "Failed to start root registration"));
      const credential = await startRegistration({ optionsJSON: JSON.parse(beginBody.options) });
      const complete = await fetch(`${api}/api/v1/passkey/bootstrap/complete`, {
        method: "POST", credentials: "include", referrerPolicy: "no-referrer",
        headers: { "Content-Type": "application/json", "X-Bootstrap-Token": bootstrapCode },
        body: JSON.stringify({
          ceremony_id: beginBody.ceremony_id, credential,
          policy_version: policy?.version, policy_sha256: policy?.sha256,
        }),
      });
      const completed = await complete.json().catch(() => ({}));
      if (!complete.ok) throw new Error(passkeyErrorMessage(completed, "Registration verification failed"));
      const exchange = await fetch(`${api}/api/v1/auth/exchange`, {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: completed.exchange_code }),
      });
      if (!exchange.ok) throw new Error("The root passkey was registered, but the restricted setup session could not be opened. Sign in normally to resume.");
      setBootstrapCode("");
      hardNavigate("/setup");
    } catch (caught) {
      if (caught instanceof Error && caught.name === "NotAllowedError") { setState("ready"); return; }
      setError(caught instanceof Error ? caught.message : "Registration failed");
      setState("error");
    }
  };

  return <div className="flex min-h-screen items-center justify-center bg-gray-50 p-4 dark:bg-gray-900">
    <div className="absolute right-4 top-4"><ThemeToggle /></div>
    <Card className="w-full max-w-md p-8">
      <div className="mb-4 flex justify-center"><Logo height={64} href="https://info.mp-opt.net" /></div>
      <h1 className="mb-2 text-center text-3xl font-bold">Welcome</h1>
      {state === "checking" && <p className="text-center text-gray-500">Checking setup status…</p>}
      {state === "ready" && <div className="space-y-5">
        <p className="text-center text-gray-600 dark:text-gray-300">Register the first root passkey. The bootstrap code is permanently retired immediately afterwards.</p>
        <section className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
          <h2 className="mb-2 font-semibold">Permitted data for this instance</h2><p>{policy?.text}</p>
          <p className="mt-2 break-all text-xs">Version {policy?.version}; SHA-256 {policy?.sha256}</p>
        </section>
        <label className="flex items-start gap-2 text-sm"><input className="mt-1" type="checkbox" checked={policyAcknowledged} onChange={(event) => setPolicyAcknowledged(event.target.checked)} /><span>I understand the permitted-data boundary and the controller&apos;s responsibility.</span></label>
        <label className="block text-sm font-medium">Bootstrap code<input type="password" autoComplete="one-time-code" value={bootstrapCode} onChange={(event) => setBootstrapCode(event.target.value)} className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 dark:border-gray-600 dark:bg-gray-800" /></label>
        <Button fullWidth onClick={() => void register()} disabled={!policyAcknowledged || bootstrapCode.length < 32}>Register root passkey</Button>
      </div>}
      {state === "registering" && <p className="text-center text-gray-500">Waiting for your passkey manager…</p>}
      {state === "already-done" && <div className="space-y-4 text-center"><p>The root passkey is registered. Sign in to resume commissioning at its last verified step.</p><Button fullWidth onClick={() => hardNavigate("/login")}>Continue to sign in</Button></div>}
      {state === "error" && <div className="space-y-4"><p role="alert" className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800">{error}</p><Button fullWidth variant="outline" onClick={() => window.location.reload()}>Try again</Button></div>}
    </Card>
  </div>;
}

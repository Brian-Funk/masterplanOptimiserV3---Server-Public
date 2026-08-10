"use client";

import { useCallback, useEffect, useState } from "react";
import { generateAgeRecoveryIdentity } from "@/lib/ageIdentity";
import { apiFetch } from "@/lib/api";
import { withReauth } from "@/lib/reauth";

export default function RecoveryKeyPage() {
  const [recipient, setRecipient] = useState("");
  const [identity, setIdentity] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [access, setAccess] = useState<"checking" | "ready" | "denied">("checking");

  const unlock = useCallback(async () => {
    setAccess("checking");
    setError("");
    try {
      const response = await withReauth(() =>
        apiFetch("/api/v1/auth/recovery-key-access"),
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || "Root passkey access is required");
      }
      setAccess("ready");
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Root passkey verification failed",
      );
      setAccess("denied");
    }
  }, []);

  useEffect(() => {
    void unlock();
  }, [unlock]);

  async function generate() {
    setBusy(true);
    setError("");
    try {
      const generated = await generateAgeRecoveryIdentity();
      setRecipient(generated.recipient);
      setIdentity(generated.identity);
    } catch {
      setError("This browser cannot create an X25519 recovery identity. Use a current Chrome, Edge, Firefox or Safari release.");
    } finally {
      setBusy(false);
    }
  }

  function saveIdentity() {
    if (!identity || !recipient) return;
    const contents = [
      "# MP-OPT snapshot recovery identity",
      `# Public key: ${recipient}`,
      "# Store this file in a protected password manager and a second encrypted/offline location.",
      identity,
      "",
    ].join("\n");
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([contents], { type: "text/plain" }));
    link.download = "mp-opt-recovery.agekey";
    link.click();
    URL.revokeObjectURL(link.href);
  }

  return (
    <main className="min-h-screen bg-gray-50 px-4 py-10 text-gray-900 dark:bg-gray-950 dark:text-gray-100">
      <div className="mx-auto max-w-2xl rounded-2xl border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-800 dark:bg-gray-900 sm:p-8">
        <h1 className="text-2xl font-semibold">Create snapshot recovery key</h1>
        <p className="mt-3 text-sm leading-6 text-gray-600 dark:text-gray-300">
          The key is generated entirely in this browser. The private identity is never sent to the server. Only paste the public <code>age1…</code> recipient into the server TUI.
        </p>
        {access === "checking" && (
          <p className="mt-6 text-sm text-gray-600 dark:text-gray-300">
            Verifying the root passkey session...
          </p>
        )}
        {access === "denied" && (
          <section className="mt-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">
            <p>{error}</p>
            <button type="button" onClick={unlock}
              className="mt-3 font-medium text-blue-700 hover:underline dark:text-blue-300">
              Verify with root passkey
            </button>
          </section>
        )}
        {access === "ready" && <>
        <div className="mt-6 flex flex-wrap gap-3">
          <button type="button" onClick={generate} disabled={busy}
            className="rounded-lg bg-blue-600 px-4 py-2 font-medium text-white disabled:opacity-60">
            {busy ? "Creating…" : identity ? "Create a new key" : "Create recovery key"}
          </button>
          {identity && (
            <button type="button" onClick={saveIdentity}
              className="rounded-lg border border-gray-300 px-4 py-2 font-medium dark:border-gray-700">
              Save private identity
            </button>
          )}
        </div>
        {error && <p role="alert" className="mt-4 text-sm text-red-700 dark:text-red-300">{error}</p>}
        {recipient && (
          <section className="mt-7 space-y-3">
            <h2 className="font-semibold">Public recipient for the TUI</h2>
            <textarea readOnly value={recipient} rows={2}
              className="w-full rounded-lg border border-gray-300 bg-gray-50 p-3 font-mono text-xs dark:border-gray-700 dark:bg-gray-950" />
            <button type="button" onClick={() => navigator.clipboard.writeText(recipient)}
              className="text-sm font-medium text-blue-700 hover:underline dark:text-blue-300">
              Copy public recipient
            </button>
            <div className="rounded-lg bg-amber-50 p-4 text-sm leading-6 text-amber-950 dark:bg-amber-950/40 dark:text-amber-100">
              Save the private identity in two protected places before continuing. Never paste an <code>AGE-SECRET-KEY-…</code> value into the VPS, email, chat, Git, or the TUI.
            </div>
          </section>
        )}
        </>}
      </div>
    </main>
  );
}

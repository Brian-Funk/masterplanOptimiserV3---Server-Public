"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Logo } from "@/components/Logo";
import { getApiUrl } from "@/lib/environment";
import { passkeyErrorMessage } from "@/lib/passkeyError";
import { startRegistration } from "@simplewebauthn/browser";

export default function BootstrapPage() {
  const router = useRouter();
  const [status, setStatus] = useState<
    "checking" | "ready" | "registering" | "done" | "already-done" | "error"
  >("checking");
  const [error, setError] = useState("");
  const [bootstrapCode, setBootstrapCode] = useState("");
  const [policyAcknowledged, setPolicyAcknowledged] = useState(false);
  const [policyIdentity, setPolicyIdentity] = useState<{
    version: string;
    sha256: string;
    text: string;
  } | null>(null);

  useEffect(() => {
    checkBootstrap();
  }, []);

  const checkBootstrap = async () => {
    try {
      const apiUrl = getApiUrl();
      const res = await fetch(`${apiUrl}/api/v1/passkey/bootstrap-status`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error("Failed to check bootstrap status");

      const data = await res.json();
      setPolicyIdentity({
        version: data.policy_version,
        sha256: data.policy_sha256,
        text: data.policy_text,
      });
      if (data.needs_bootstrap) {
        if (data.bootstrap_configured === false) {
          setError(
            "Root bootstrap is not configured on the server. Set ROOT_BOOTSTRAP_TOKEN and restart the service.",
          );
          setStatus("error");
          return;
        }
        setStatus("ready");
      } else {
        setStatus("already-done");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cannot reach server");
      setStatus("error");
    }
  };

  const handleRegister = async () => {
    setStatus("registering");
    setError("");

    try {
      const apiUrl = getApiUrl();

      // 1. Begin passkey registration ceremony
      const beginRes = await fetch(`${apiUrl}/api/v1/passkey/bootstrap/begin`, {
        method: "POST",
        headers: { "X-Bootstrap-Token": bootstrapCode },
        credentials: "include",
        referrerPolicy: "no-referrer",
      });
      if (!beginRes.ok) {
        const err = await beginRes.json().catch(() => ({}));
        throw new Error(passkeyErrorMessage(err, "Failed to start registration"));
      }
      const beginData = await beginRes.json();
      const options = JSON.parse(beginData.options);
      const ceremonyId = beginData.ceremony_id;

      // 2. Prompt the browser/passkey manager to create a credential
      const credential = await startRegistration({ optionsJSON: options });

      // 3. Send the credential back to the server
      const completeRes = await fetch(
        `${apiUrl}/api/v1/passkey/bootstrap/complete`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Bootstrap-Token": bootstrapCode,
          },
          credentials: "include",
          referrerPolicy: "no-referrer",
          body: JSON.stringify({
            ceremony_id: ceremonyId,
            credential,
            policy_version: policyIdentity?.version,
            policy_sha256: policyIdentity?.sha256,
          }),
        },
      );
      if (!completeRes.ok) {
        const err = await completeRes.json().catch(() => ({}));
        throw new Error(
          passkeyErrorMessage(err, "Registration verification failed"),
        );
      }

      setStatus("done");
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "NotAllowedError") {
        setStatus("ready");
        return;
      }
      setError(err instanceof Error ? err.message : "Registration failed");
      setStatus("error");
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900 p-4">
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>
      <Card className="w-full max-w-md">
        <div className="p-8">
          <div className="flex justify-center mb-4">
            <Logo height={64} href="https://info.mp-opt.net" />
          </div>
          <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100 mb-2 text-center">
            Welcome
          </h1>

          {status === "checking" && (
            <p className="text-gray-600 dark:text-gray-400 text-center">
              Checking setup status...
            </p>
          )}

          {status === "ready" && (
            <>
              <p className="text-gray-600 dark:text-gray-400 mb-6 text-center">
                No root admin passkey has been registered yet. Register one now
                to secure your account.
              </p>
              <p className="text-sm text-gray-500 dark:text-gray-500 mb-8 text-center">
                You&apos;ll be prompted by your browser or password manager to
                create a passkey.
              </p>
              <section className="mb-5 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
                <h2 className="mb-2 font-semibold">Permitted data for this instance</h2>
                <p className="mb-2">{policyIdentity?.text}</p>
                <p className="break-all text-xs">Version {policyIdentity?.version}; SHA-256 {policyIdentity?.sha256}</p>
              </section>
              <label className="mb-5 flex items-start gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input
                  type="checkbox"
                  checked={policyAcknowledged}
                  onChange={(event) => setPolicyAcknowledged(event.target.checked)}
                  className="mt-1"
                />
                <span>I understand the permitted-data boundary and the controller&apos;s responsibility.</span>
              </label>
              <label
                htmlFor="bootstrap-code"
                className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
              >
                Bootstrap code
              </label>
              <input
                id="bootstrap-code"
                type="password"
                autoComplete="one-time-code"
                value={bootstrapCode}
                onChange={(event) => setBootstrapCode(event.target.value)}
                className="mb-4 w-full rounded border border-gray-300 bg-white px-3 py-2 text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
              />
              <Button
                type="button"
                variant="primary"
                fullWidth
                onClick={handleRegister}
                disabled={bootstrapCode.length < 32 || !policyAcknowledged || !policyIdentity}
              >
                Register Root Passkey
              </Button>
            </>
          )}

          {status === "registering" && (
            <p className="text-gray-600 dark:text-gray-400 text-center">
              Waiting for passkey manager...
            </p>
          )}

          {status === "done" && (
            <>
              <div className="bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-800 text-green-700 dark:text-green-300 px-4 py-3 rounded-lg text-sm mb-6 text-center">
                Root passkey registered successfully!
              </div>
              <Button
                type="button"
                variant="primary"
                fullWidth
                onClick={() => router.push("/login?next=/recovery-key")}
              >
                Sign in and create recovery key
              </Button>
            </>
          )}

          {status === "already-done" && (
            <>
              <p className="text-gray-600 dark:text-gray-400 mb-6 text-center">
                Root admin already has a passkey. Redirecting to login...
              </p>
              <Button
                type="button"
                variant="primary"
                fullWidth
                onClick={() => router.push("/login")}
              >
                Go to Login
              </Button>
            </>
          )}

          {status === "error" && (
            <>
              <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 px-4 py-3 rounded-lg text-sm mb-6 text-center">
                {error}
              </div>
              <Button
                type="button"
                variant="primary"
                fullWidth
                onClick={checkBootstrap}
              >
                Retry
              </Button>
            </>
          )}
        </div>
      </Card>
    </div>
  );
}

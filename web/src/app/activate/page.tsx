"use client";

import { useState, useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Logo } from "@/components/Logo";
import { getApiUrl } from "@/lib/environment";
import { passkeyErrorMessage } from "@/lib/passkeyError";
import {
  captureRouteSecret,
  clearRouteSecret,
  isDefinitiveSecretRejection,
} from "@/lib/routeSecret";
import { startRegistration } from "@simplewebauthn/browser";

type ActivationPurpose =
  | "initial_setup"
  | "additional_passkey"
  | "credential_reset";

type ProcessingConsent = {
  format: string;
  statement_sha256: string;
  policy_version: number;
  policy_sha256: string;
  controller_identity: string;
  privacy_contact: string;
  processing_purposes: string[];
  data_categories: string[];
  authenticated_audience: string;
  privacy_url: string;
  rights_url: string;
  event_privacy_url?: string | null;
  statement: string;
};

/** Return the public heading for a purpose-bound passkey invitation. */
function activationHeading(purpose?: ActivationPurpose): string {
  if (purpose === "additional_passkey") return "Add another passkey";
  if (purpose === "credential_reset") return "Reset passkeys";
  return "Account setup";
}

function ActivateContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryToken = searchParams.get("token") || "";
  const [token, setToken] = useState("");

  const [status, setStatus] = useState<
    "validating" | "ready" | "registering" | "done" | "error"
  >("validating");
  const [info, setInfo] = useState<{
    username: string;
    display_name: string;
    purpose: ActivationPurpose;
    processing_consent?: ProcessingConsent;
  } | null>(null);
  const [consentConfirmed, setConsentConfirmed] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const resolvedToken = captureRouteSecret("/activate") || queryToken;
    if (!resolvedToken) {
      setError("No activation token provided");
      setStatus("error");
      return;
    }
    setToken(resolvedToken);
    void validateToken(resolvedToken);
  }, [queryToken]);

  async function validateToken(tokenToValidate: string) {
    try {
      const apiUrl = getApiUrl();
      const res = await fetch(`${apiUrl}/api/v1/activation/validate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        referrerPolicy: "no-referrer",
        body: JSON.stringify({ token: tokenToValidate }),
      });
      if (!res.ok) {
        if (isDefinitiveSecretRejection(res.status)) clearRouteSecret("/activate");
        const err = await res.json().catch(() => ({}));
        throw new Error(passkeyErrorMessage(err, "Failed to validate token"));
      }

      const data = await res.json();
      if (!data.valid) {
        clearRouteSecret("/activate");
        setError("This activation link is invalid or has expired.");
        setStatus("error");
        return;
      }

      setInfo({
        username: data.username,
        display_name: data.display_name,
        purpose: data.purpose as ActivationPurpose,
        processing_consent: data.processing_consent || undefined,
      });
      setConsentConfirmed(false);
      setStatus("ready");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Validation failed");
      setStatus("error");
    }
  }

  const handleRegister = async () => {
    setStatus("registering");
    setError("");

    try {
      const apiUrl = getApiUrl();

      // 1. Begin passkey registration with activation token
      const consent = info?.processing_consent;
      const beginHeaders: Record<string, string> = {
        "X-Activation-Token": token,
      };
      let beginBody: string | undefined;
      if (info?.purpose === "initial_setup") {
        if (!consent || !consentConfirmed) {
          throw new Error("Review and confirm the processing information before registering a passkey.");
        }
        beginHeaders["Content-Type"] = "application/json";
        beginBody = JSON.stringify({
          confirmed: true,
          statement_version: consent.format,
          statement_sha256: consent.statement_sha256,
          policy_version: consent.policy_version,
          policy_sha256: consent.policy_sha256,
        });
      }
      const beginRes = await fetch(
        `${apiUrl}/api/v1/passkey/register/begin`,
        {
          method: "POST",
          headers: beginHeaders,
          credentials: "include",
          referrerPolicy: "no-referrer",
          body: beginBody,
        },
      );
      if (!beginRes.ok) {
        if (isDefinitiveSecretRejection(beginRes.status)) clearRouteSecret("/activate");
        const err = await beginRes.json().catch(() => ({}));
        throw new Error(passkeyErrorMessage(err, "Failed to start registration"));
      }
      const beginData = await beginRes.json();
      const options = JSON.parse(beginData.options);
      const ceremonyId = beginData.ceremony_id;

      // 2. Prompt the browser/passkey manager
      const credential = await startRegistration({ optionsJSON: options });

      // 3. Complete registration
      const completeRes = await fetch(
        `${apiUrl}/api/v1/passkey/register/complete`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Activation-Token": token,
          },
          credentials: "include",
          referrerPolicy: "no-referrer",
          body: JSON.stringify({
            ceremony_id: ceremonyId,
            credential,
          }),
        },
      );
      if (!completeRes.ok) {
        if (isDefinitiveSecretRejection(completeRes.status)) clearRouteSecret("/activate");
        const err = await completeRes.json().catch(() => ({}));
        throw new Error(passkeyErrorMessage(err, "Registration failed"));
      }

      clearRouteSecret("/activate");
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
      <Card className="w-full max-w-md border-0 shadow-none sm:border sm:shadow-sm">
        <div className="px-2 py-6 sm:p-8">
          <div className="flex justify-center mb-6">
            <Logo height={48} />
          </div>
          <h1 className="mb-2 text-center text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-100 sm:text-3xl">
            {activationHeading(info?.purpose)}
          </h1>

          {status === "validating" && (
            <p className="text-gray-600 dark:text-gray-400 text-center">
              Validating your link...
            </p>
          )}

          {status === "ready" && info && (
            <>
              <p className="text-gray-600 dark:text-gray-400 mb-2 text-center">
                Welcome, <strong>{info.display_name}</strong>
              </p>
              <p className="text-sm text-gray-500 dark:text-gray-500 mb-8 text-center">
                {info.purpose === "credential_reset"
                  ? "Register a replacement passkey to regain access. After it succeeds, all previous passkeys and sessions will stop working."
                  : info.purpose === "additional_passkey"
                    ? "Register one more passkey for this account. Your existing passkeys and signed-in sessions will remain valid."
                    : "Register a passkey to activate your account. You'll be prompted by your browser or password manager."}
              </p>
              {info.purpose === "initial_setup" && info.processing_consent && (
                <section className="mb-6 rounded-xl border border-gray-200 bg-gray-50 p-4 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-800/60 dark:text-gray-200">
                  <h2 className="mb-2 text-base font-semibold text-gray-900 dark:text-gray-100">
                    Processing for your account
                  </h2>
                  <dl className="space-y-2">
                    <div>
                      <dt className="font-medium">Controller</dt>
                      <dd>{info.processing_consent.controller_identity}</dd>
                    </div>
                    <div>
                      <dt className="font-medium">Purpose</dt>
                      <dd>{info.processing_consent.processing_purposes.join(" ")}</dd>
                    </div>
                    <div>
                      <dt className="font-medium">Operational information</dt>
                      <dd>{info.processing_consent.data_categories.join(", ")}</dd>
                    </div>
                    <div>
                      <dt className="font-medium">Who can access it</dt>
                      <dd>{info.processing_consent.authenticated_audience}</dd>
                    </div>
                  </dl>
                  <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1">
                    <a className="font-medium text-blue-700 underline underline-offset-2 dark:text-blue-300" href={info.processing_consent.privacy_url} target="_blank" rel="noreferrer">
                      Privacy notice
                    </a>
                    <a className="font-medium text-blue-700 underline underline-offset-2 dark:text-blue-300" href={info.processing_consent.rights_url} target="_blank" rel="noreferrer">
                      Your rights
                    </a>
                    {info.processing_consent.event_privacy_url && (
                      <a className="font-medium text-blue-700 underline underline-offset-2 dark:text-blue-300" href={info.processing_consent.event_privacy_url} target="_blank" rel="noreferrer">
                        Event privacy details
                      </a>
                    )}
                  </div>
                  <label className="mt-4 flex cursor-pointer items-start gap-3 rounded-lg border border-gray-300 bg-white p-3 dark:border-gray-600 dark:bg-gray-900">
                    <input
                      type="checkbox"
                      className="mt-1 h-4 w-4 shrink-0 accent-blue-600"
                      checked={consentConfirmed}
                      onChange={(event) => setConsentConfirmed(event.target.checked)}
                    />
                    <span>{info.processing_consent.statement}</span>
                  </label>
                </section>
              )}
              <Button
                type="button"
                variant="primary"
                fullWidth
                onClick={handleRegister}
                disabled={
                  info.purpose === "initial_setup" &&
                  (!info.processing_consent || !consentConfirmed)
                }
              >
                {info.purpose === "additional_passkey"
                  ? "Add passkey"
                  : info.purpose === "credential_reset"
                    ? "Register replacement passkey"
                    : "Register passkey"}
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
                {info?.purpose === "additional_passkey"
                  ? "Your additional passkey is ready. Existing passkeys and sessions remain valid."
                  : info?.purpose === "credential_reset"
                    ? "Your replacement passkey is ready. Previous passkeys and sessions have been revoked."
                    : "Passkey registered. Your account is now active."}
              </div>
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
                variant="outline"
                fullWidth
                onClick={() => router.push("/login")}
              >
                Go to Login
              </Button>
            </>
          )}
        </div>
      </Card>
    </div>
  );
}

export default function ActivatePage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900">
          <p className="text-gray-500 dark:text-gray-400">Loading...</p>
        </div>
      }
    >
      <ActivateContent />
    </Suspense>
  );
}

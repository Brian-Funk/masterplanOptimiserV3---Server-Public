"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Logo } from "@/components/Logo";
import { Footer } from "@/components/Footer";
import { getApiUrl } from "@/lib/environment";
import { passkeyErrorMessage } from "@/lib/passkeyError";
import { startAuthentication } from "@simplewebauthn/browser";
import { useServiceAvailability } from "@/contexts/ServiceAvailabilityContext";
import { ServiceStatusPanel } from "@/components/ServiceStatusPanel";

const PASSKEY_LOOKUP_ERROR =
  "No usable passkey was found on this device. Sign in on another device and add a discoverable passkey for this phone.";

function isPasskeyCancellation(err: unknown): boolean {
  if (!(err instanceof Error)) {
    return false;
  }

  const name = err.name.toLowerCase();
  const message = err.message.toLowerCase();

  return (
    name === "notallowederror" ||
    name === "aborterror" ||
    name === "invalidstateerror" ||
    message.includes("operation either timed out or was not allowed") ||
    message.includes("user cancelled") ||
    message.includes("user canceled") ||
    message.includes("cancelled") ||
    message.includes("canceled")
  );
}

function isCredentialManagerLookupError(err: unknown): boolean {
  if (!(err instanceof Error)) {
    return false;
  }

  const name = err.name.toLowerCase();
  const message = err.message.toLowerCase();

  return (
    name === "unknownerror" ||
    message.includes("credential manager") ||
    message.includes("no credentials available") ||
    message.includes("no passkey")
  );
}

function logPasskeyError(stage: string, err: unknown): void {
  if (err instanceof Error) {
    console.error("[Passkey login]", {
      stage,
      name: err.name,
      message: err.message,
    });
    return;
  }

  console.error("[Passkey login]", { stage, error: String(err) });
}

export default function LoginPage() {
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const loginInFlightRef = useRef(false);
  const { user, offlineAccess, offlineAccessExpired, refreshUser } = useAuth();
  const { isReady } = useServiceAvailability();
  const router = useRouter();

  useEffect(() => {
    // Check bootstrap status first
    const checkBootstrap = async () => {
      if (!isReady) return;
      try {
        const apiUrl = getApiUrl();
        const res = await fetch(`${apiUrl}/api/v1/passkey/bootstrap-status`, {
          credentials: "include",
        });
        if (res.ok) {
          const data = await res.json();
          if (data.needs_bootstrap) {
            router.push("/bootstrap");
            return;
          }
        }
      } catch {
        // Backend unavailable  -  stay on login
      }
    };
    checkBootstrap();
  }, [isReady, router]);

  useEffect(() => {
    if (user && isReady) {
      if (user.is_admin || user.is_root_admin) {
        const requested = new URLSearchParams(window.location.search).get("next");
        router.push(
          user.is_root_admin && requested === "/recovery-key"
            ? "/recovery-key"
            : "/admin",
        );
      } else if (user.is_issuer && user.event_id) {
        // Issuers see the calendar first; they reach admin via the top bar
        router.push(`/calendar?event=${user.event_id}`);
      } else if (user.event_id) {
        router.push(`/calendar?event=${user.event_id}`);
      } else {
        router.push("/unassigned");
      }
    }
  }, [user, isReady, router]);

  const handlePasskeyLogin = async () => {
    if (!isReady) return;
    if (loginInFlightRef.current) {
      return;
    }

    loginInFlightRef.current = true;
    setError("");
    setIsLoading(true);

    let stage = "auth-begin";
    try {
      const apiUrl = getApiUrl();

      // 1. Start the authentication ceremony
      const beginRes = await fetch(
        `${apiUrl}/api/v1/passkey/auth/begin`,
        {
          method: "POST",
          credentials: "include",
        },
      );
      if (!beginRes.ok) {
        const err = await beginRes.json().catch(() => ({}));
        throw new Error(
          passkeyErrorMessage(err, "Failed to start passkey authentication"),
        );
      }
      const beginData = await beginRes.json();
      const options = JSON.parse(beginData.options);
      const ceremonyId = beginData.ceremony_id;

      // 2. Prompt the browser / passkey manager
      stage = "credential-manager";
      const credential = await startAuthentication({ optionsJSON: options });

      // 3. Send the credential to the backend for verification
      stage = "auth-complete";
      const completeRes = await fetch(
        `${apiUrl}/api/v1/passkey/auth/complete`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            ceremony_id: ceremonyId,
            credential,
          }),
        },
      );
      if (!completeRes.ok) {
        const err = await completeRes.json().catch(() => ({}));
        throw new Error(passkeyErrorMessage(err, "Passkey verification failed"));
      }
      const completeData = await completeRes.json();

      // 4. Exchange the one-time code for a session cookie
      stage = "auth-exchange";
      const exchangeRes = await fetch(`${apiUrl}/api/v1/auth/exchange`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ code: completeData.exchange_code }),
      });
      if (!exchangeRes.ok) {
        const err = await exchangeRes.json().catch(() => ({}));
        throw new Error(passkeyErrorMessage(err, "Failed to establish session"));
      }

      // Refresh the user from /me to populate AuthContext
      await refreshUser();
    } catch (err: unknown) {
      if (isPasskeyCancellation(err)) {
        return;
      }
      logPasskeyError(stage, err);
      if (isCredentialManagerLookupError(err)) {
        setError(PASSKEY_LOOKUP_ERROR);
        return;
      }
      setError(err instanceof Error ? err.message : "Passkey login failed");
    } finally {
      loginInFlightRef.current = false;
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col bg-gray-50 dark:bg-gray-900">
      <div className="flex-1 flex items-center justify-center px-4 py-16 sm:p-6">
        <div className="absolute top-4 right-4">
          <ThemeToggle />
        </div>
        {!isReady ? (
          <ServiceStatusPanel
            offlineAccess={offlineAccess}
            offlineAccessExpired={offlineAccessExpired}
          />
        ) : <Card className="w-full max-w-md border-0 shadow-none sm:border sm:shadow-sm">
          <div className="px-2 py-6 sm:p-8">
            <div className="flex justify-center mb-4">
              <Logo height={64} href="https://info.mp-opt.net" />
            </div>
            <h1 className="mb-2 text-center text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-100 sm:text-3xl">
              Masterplan Optimiser
            </h1>
            <p className="text-gray-600 dark:text-gray-400 mb-8 text-center">
              Sign in to your account
            </p>

            {error && (
              <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 px-4 py-3 rounded-lg text-sm mb-6 text-center">
                {error}
              </div>
            )}

            <Button
              type="button"
              variant="primary"
              fullWidth
              disabled={isLoading}
              onClick={handlePasskeyLogin}
            >
              {isLoading ? "Waiting for passkey..." : "Sign in with Passkey"}
            </Button>
          </div>
        </Card>}
      </div>
      <Footer />
    </div>
  );
}

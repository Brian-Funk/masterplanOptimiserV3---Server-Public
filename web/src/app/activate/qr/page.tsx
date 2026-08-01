"use client";

import { Suspense, useState, useEffect, useCallback, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { QRCodeSVG } from "qrcode.react";
import { Logo } from "@/components/Logo";
import { apiFetch } from "@/lib/api";
import { withReauth } from "@/lib/reauth";
import { RefreshCw } from "lucide-react";
import {
  captureRouteSecret,
  clearRouteSecret,
  rememberRouteSecret,
} from "@/lib/routeSecret";

type ActivationPurpose =
  | "initial_setup"
  | "additional_passkey"
  | "credential_reset";

/** Safely parse a purpose supplied by the administrator's fragment URL. */
function activationPurpose(value: unknown): ActivationPurpose {
  if (value === "additional_passkey" || value === "credential_reset") {
    return value;
  }
  return "initial_setup";
}

/** Extract an activation token from either a fragment URL or a legacy query URL. */
function activationTokenFromUrl(value: string): string {
  const url = new URL(value, "https://activation.invalid");
  return (
    new URLSearchParams(url.hash.replace(/^#/, "")).get("token") ||
    url.searchParams.get("token") ||
    ""
  );
}

function QRContent() {
  const searchParams = useSearchParams();
  const queryToken = searchParams.get("token") || "";
  const queryName = searchParams.get("name") || "";
  const queryUserId = searchParams.get("userId") || "";
  const queryPurpose = activationPurpose(searchParams.get("purpose"));

  const [token, setToken] = useState(queryToken);
  const [name, setName] = useState(queryName);
  const [userId, setUserId] = useState(queryUserId);
  const [purpose, setPurpose] = useState<ActivationPurpose>(queryPurpose);
  const [initialised, setInitialised] = useState(false);
  const [used, setUsed] = useState(false);
  const [generating, setGenerating] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const activationUrl = `${origin}/activate#token=${token}`;

  useEffect(() => {
    const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const remembered = window.history.state?.mpOptActivationQr as
      | { name?: string; userId?: string; purpose?: ActivationPurpose }
      | undefined;
    const resolvedToken = captureRouteSecret("/activate/qr") || queryToken;
    const resolvedName = fragment.get("name") || queryName || remembered?.name || "";
    const resolvedUserId = fragment.get("userId") || queryUserId || remembered?.userId || "";
    const resolvedPurpose = activationPurpose(
      fragment.get("purpose") || remembered?.purpose || queryPurpose,
    );
    if (resolvedToken) {
      window.history.replaceState(
        {
          ...(window.history.state ?? {}),
          mpOptActivationQr: {
            name: resolvedName,
            userId: resolvedUserId,
            purpose: resolvedPurpose,
          },
        },
        "",
        "/activate/qr",
      );
      setToken(resolvedToken);
      setName(resolvedName);
      setUserId(resolvedUserId);
      setPurpose(resolvedPurpose);
    }
    setInitialised(true);
  }, [queryName, queryPurpose, queryToken, queryUserId]);

  // Poll token validity every 3 seconds
  const pollValidity = useCallback(async () => {
    if (!token || used) return;
    try {
      const res = await apiFetch("/api/v1/activation/validate", {
        method: "POST",
        referrerPolicy: "no-referrer",
        body: JSON.stringify({ token }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.valid && data.purpose) {
          setPurpose(activationPurpose(data.purpose));
        }
        if (!data.valid) {
          clearRouteSecret("/activate/qr");
          setUsed(true);
        }
      }
    } catch {
      // Network error - ignore, will retry
    }
  }, [token, used]);

  useEffect(() => {
    if (!token || used) return;
    intervalRef.current = setInterval(pollValidity, 3000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [token, used, pollValidity]);

  // Stop polling when used
  useEffect(() => {
    if (used && intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, [used]);

  const handleGenerateNew = async () => {
    if (!userId) return;
    setGenerating(true);
    try {
      const res = await withReauth(() =>
        apiFetch(`/api/v1/admin/users/${userId}/activation-link`, {
          method: "POST",
          body: JSON.stringify(purpose === "initial_setup" ? {} : { purpose }),
        }),
      );
      if (res.ok) {
        const data = await res.json();
        const newToken = activationTokenFromUrl(data.activation_url);
        if (!newToken) throw new Error("No activation token returned");
        setToken(newToken);
        rememberRouteSecret("/activate/qr", newToken);
        setPurpose(activationPurpose(data.purpose));
        setUsed(false);
      }
    } catch {
      // The passkey prompt was cancelled or re-authentication failed.
    } finally {
      setGenerating(false);
    }
  };

  if (!initialised) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900 p-4">
        <p className="text-gray-500 dark:text-gray-400">Loading...</p>
      </div>
    );
  }

  if (!token) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900 p-4">
        <p className="text-red-500">No activation token provided.</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-gray-50 dark:bg-gray-900 p-6">
      <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-lg p-8 max-w-sm w-full text-center">
        <Logo height={40} />

        <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100 mt-4">
          {used
            ? "Code used"
            : purpose === "additional_passkey"
              ? "Scan to add a passkey"
              : purpose === "credential_reset"
                ? "Scan to reset passkeys"
                : "Scan to activate"}
        </h1>

        {name && (
          <p className="text-lg text-gray-600 dark:text-gray-300 mt-1">
            {name}
          </p>
        )}

        <div
          className={`mt-6 inline-block p-4 rounded-xl transition-colors duration-500 ${
            used ? "bg-red-50" : "bg-white"
          }`}
        >
          <QRCodeSVG
            value={activationUrl}
            size={240}
            level="M"
            includeMargin={false}
            fgColor={used ? "#DC2626" : "#000000"}
          />
        </div>

        {used ? (
          <p className="text-sm text-red-600 dark:text-red-400 mt-4 font-medium">
            This one-time code is no longer available.
          </p>
        ) : (
          <>
            <p className="text-xs text-gray-400 dark:text-gray-500 mt-4 break-all">
              {activationUrl}
            </p>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-4">
              {purpose === "additional_passkey"
                ? "Scan with the device that should hold the additional passkey. Existing access remains valid."
                : purpose === "credential_reset"
                  ? "Scan to register a replacement. Previous passkeys and sessions will be revoked after registration succeeds."
                  : "Scan this QR code with your phone to register your passkey."}
            </p>
          </>
        )}

        {used && userId && (
          <button
            onClick={handleGenerateNew}
            disabled={generating}
            className="mt-4 inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            <RefreshCw size={16} className={generating ? "animate-spin" : ""} />
            {generating ? "Generating..." : "Generate new access code"}
          </button>
        )}
      </div>
    </div>
  );
}

export default function QRActivatePage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900">
          <p className="text-gray-500 dark:text-gray-400">Loading...</p>
        </div>
      }
    >
      <QRContent />
    </Suspense>
  );
}

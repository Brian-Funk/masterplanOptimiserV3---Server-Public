/**
 * Re-authentication via passkey for destructive admin operations.
 *
 * Calls /admin/reauth/begin, opens the browser passkey prompt, then calls
 * /admin/reauth/complete. On success the server stamps the current session so
 * subsequent destructive requests within the 5-minute window are permitted.
 */
import { startAuthentication } from "@simplewebauthn/browser";
import { apiFetch } from "./api";
import { passkeyErrorMessage } from "./passkeyError";

/**
 * Perform a passkey re-authentication ceremony.
 *
 * Resolves on success and throws on failure or cancellation.
 */
export async function performReauth(): Promise<void> {
  // 1. Request a re-auth challenge.
  const beginRes = await apiFetch("/api/v1/admin/reauth/begin", {
    method: "POST",
    body: JSON.stringify({}),
  });
  if (!beginRes.ok) {
    const err = await beginRes.json().catch(() => ({}));
    throw new Error(
      passkeyErrorMessage(err, "Failed to start re-authentication"),
    );
  }
  const beginData = await beginRes.json();
  const options = JSON.parse(beginData.options);
  const ceremonyId = beginData.ceremony_id;

  // 2. Prompt the browser or passkey manager.
  const credential = await startAuthentication({ optionsJSON: options });

  // 3. Send the credential back for verification.
  const completeRes = await apiFetch("/api/v1/admin/reauth/complete", {
    method: "POST",
    body: JSON.stringify({
      ceremony_id: ceremonyId,
      credential,
    }),
  });
  if (!completeRes.ok) {
    const err = await completeRes.json().catch(() => ({}));
    throw new Error(passkeyErrorMessage(err, "Re-authentication failed"));
  }
}

/**
 * Wrap an async action so it re-authenticates first when required.
 *
 * Calls `action()`. If the server responds with 403 "Re-authentication required",
 * it triggers a passkey prompt, then retries the action once.
 */
export async function withReauth(
  action: () => Promise<Response>,
): Promise<Response> {
  const res = await action();

  if (res.status === 403) {
    const body = await res.json().catch(() => ({}));
    if (body.detail === "Re-authentication required") {
      await performReauth();
      return action();
    }
    // Some other 403 - return as-is.
    return new Response(JSON.stringify(body), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    });
  }

  return res;
}

const RATE_LIMIT_PATTERN = /rate limit exceeded|too many requests/i;

/**
 * Extract a safe user-facing message from FastAPI or SlowAPI error payloads.
 *
 * FastAPI uses `detail`, while SlowAPI uses `error` for rate-limit responses.
 * Unknown response shapes return the caller-provided fallback.
 */
export function passkeyErrorMessage(
  body: unknown,
  fallback: string,
): string {
  if (!body || typeof body !== "object") {
    return fallback;
  }

  const payload = body as Record<string, unknown>;
  const message =
    typeof payload.detail === "string"
      ? payload.detail
      : typeof payload.error === "string"
        ? payload.error
        : fallback;

  if (RATE_LIMIT_PATTERN.test(message)) {
    return "Too many passkey attempts. Please wait a minute and try again.";
  }

  return message;
}

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
  const detail = payload.detail;
  const message =
    typeof detail === "string"
      ? detail
      : detail &&
          typeof detail === "object" &&
          typeof (detail as Record<string, unknown>).message === "string"
        ? ((detail as Record<string, unknown>).message as string)
      : typeof payload.error === "string"
        ? payload.error
        : fallback;

  if (RATE_LIMIT_PATTERN.test(message)) {
    return "Too many passkey attempts. Please wait a minute and try again.";
  }

  return message;
}

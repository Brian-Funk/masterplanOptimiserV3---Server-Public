/**
 * Thin fetch wrapper with CSRF support.
 */
import { getApiUrl } from "./environment";

function getCsrfToken(): string {
  if (typeof window === "undefined") return "";
  const match = document.cookie.match(/(?:^|;\s*)(?:__Host-mp_csrf|csrf_token)=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

/**
 * Fetch with credentials and a CSRF header on mutating methods.
 *
 * Returns the raw response so callers can decide how to handle status codes.
 */
export async function apiFetch(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const url = `${getApiUrl()}${path}`;
  const method = (options.method || "GET").toUpperCase();

  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };

  // Attach CSRF token on state-changing requests
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    const csrf = getCsrfToken();
    if (csrf) {
      headers["X-CSRF-Token"] = csrf;
    }
  }

  // Default Content-Type for JSON bodies
  if (
    options.body &&
    typeof options.body === "string" &&
    !headers["Content-Type"]
  ) {
    headers["Content-Type"] = "application/json";
  }

  return fetch(url, {
    ...options,
    headers,
    credentials: "include",
    cache: "no-store",
  });
}

const DEFAULT_TRANSITION_RETRY_DELAYS_MS = [1_000, 2_000, 4_000, 8_000, 8_000];

/**
 * Retry an idempotent request while Caddy is temporarily unable to reach the
 * active backend during a guarded service transition.
 *
 * Callers must supply an idempotent request: a naturally idempotent method
 * such as PUT, or a POST carrying a stable server-enforced idempotency key.
 */
export async function retryServiceTransition(
  request: () => Promise<Response>,
  options: {
    delaysMs?: number[];
    wait?: (delayMs: number) => Promise<void>;
  } = {},
): Promise<Response> {
  const delaysMs = options.delaysMs ?? DEFAULT_TRANSITION_RETRY_DELAYS_MS;
  const wait = options.wait ?? ((delayMs: number) =>
    new Promise<void>((resolve) => window.setTimeout(resolve, delayMs)));
  let lastResponse: Response | null = null;
  let lastError: unknown;

  for (let attempt = 0; attempt <= delaysMs.length; attempt += 1) {
    try {
      const response = await request();
      if (![502, 503, 504].includes(response.status)) return response;
      lastResponse = response;
    } catch (error) {
      lastError = error;
    }
    if (attempt < delaysMs.length) await wait(delaysMs[attempt]);
  }

  if (lastResponse) return lastResponse;
  throw lastError instanceof Error
    ? lastError
    : new Error("The service remained unavailable during the protected update.");
}

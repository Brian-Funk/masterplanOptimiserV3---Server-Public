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

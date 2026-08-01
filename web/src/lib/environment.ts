/**
 * Environment utility.
 * V3 is web-only with no desktop or Electron shell. All API calls use relative paths
 * since Caddy reverse-proxies /api/v1/* to the backend container.
 */

/**
 * Return the base URL for backend API calls.
 *
 * The hosted server uses same-origin requests so cookies, CSRF protection,
 * and the Caddy reverse proxy remain aligned.
 */
export function getApiUrl(): string {
  return "";
}

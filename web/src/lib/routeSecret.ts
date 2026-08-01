const PREFIX = "mp-opt-route-secret:";

function storageKey(route: string, name: string): string {
  return `${PREFIX}${route}:${name}`;
}

function sessionGet(key: string): string {
  try {
    return window.sessionStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function sessionSet(key: string, value: string): void {
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    // History state still preserves the secret for this tab when storage is blocked.
  }
}

function sessionDelete(key: string): void {
  try {
    window.sessionStorage.removeItem(key);
  } catch {
    // The matching history entry is cleared below.
  }
}

function replaceRouteState(route: string, state: Record<string, unknown>): void {
  try {
    window.history.replaceState(state, "", route);
  } catch {
    // Keeping the incoming fragment is safer than losing an activation secret.
  }
}

/**
 * Capture a bearer-style route secret before removing it from the visible URL.
 * Session storage survives reloads in this tab but does not create a durable,
 * cross-session credential like localStorage would.
 */
export function captureRouteSecret(
  route: string,
  name = "token",
): string {
  if (typeof window === "undefined") return "";
  const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const query = new URLSearchParams(window.location.search);
  const incoming = fragment.get(name) || query.get(name) || "";
  const historyValue = window.history.state?.[storageKey(route, name)];
  const stored = sessionGet(storageKey(route, name));
  const value = incoming || (typeof historyValue === "string" ? historyValue : "") || stored;

  if (value) {
    sessionSet(storageKey(route, name), value);
    replaceRouteState(
      route,
      { ...(window.history.state ?? {}), [storageKey(route, name)]: value },
    );
  }
  return value;
}

export function rememberRouteSecret(
  route: string,
  value: string,
  name = "token",
): void {
  if (typeof window === "undefined" || !value) return;
  sessionSet(storageKey(route, name), value);
  replaceRouteState(
    route,
    { ...(window.history.state ?? {}), [storageKey(route, name)]: value },
  );
}

export function clearRouteSecret(route: string, name = "token"): void {
  if (typeof window === "undefined") return;
  const key = storageKey(route, name);
  sessionDelete(key);
  const state = { ...(window.history.state ?? {}) };
  delete state[key];
  replaceRouteState(route, state);
}

export function isDefinitiveSecretRejection(status: number): boolean {
  return status >= 400 && status < 500 && status !== 408 && status !== 429;
}

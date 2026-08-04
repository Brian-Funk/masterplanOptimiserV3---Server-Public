"use client";

import React, { createContext, useCallback, useContext, useState, useEffect, useRef } from "react";
import { getApiUrl } from "@/lib/environment";
import { apiFetch } from "@/lib/api";
import {
  clearLegacyPrivateCaches,
  clearOfflineCalendarCacheForUser,
  pruneExpiredOfflineCalendarPayloads,
} from "@/lib/offlineCalendarCache";
import {
  clearOfflineAccessMarker,
  getOfflineAccessMarker,
  isOfflineAccessValid,
  storeOfflineAccessForUser,
  type OfflineAccessMarker,
} from "@/lib/offlineAccess";
import { useServiceAvailability } from "@/contexts/ServiceAvailabilityContext";
import { hardNavigate } from "@/lib/hardNavigation";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
/** Authenticated server user and their effective permissions. */
export interface User {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  is_root_admin: boolean;
  is_admin: boolean;
  is_issuer: boolean;
  can_edit: boolean;
  is_active: boolean;
  is_activated: boolean;
  linked_person_id: number | null;
  event_id: number | null;
  offline_access_ttl_hours: number;
  recovery_setup_required?: boolean;
}

/** High-level authentication state, including offline session uncertainty. */
export type AuthStatus =
  | "checking"
  | "authenticated"
  | "unauthenticated"
  | "offline";

/** Authentication state and operations exposed by `useAuth`. */
export interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  authStatus: AuthStatus;
  offlineAccess: OfflineAccessMarker | null;
  offlineAccessExpired: boolean;
  isLoggingOut: boolean;
  logoutError: string | null;
  logout: () => Promise<boolean>;
  dismissLogoutError: () => void;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

async function readOfflineAccessState(): Promise<{
  marker: OfflineAccessMarker | null;
  expired: boolean;
}> {
  try {
    await pruneExpiredOfflineCalendarPayloads();
  } catch (storageError) {
    console.warn("Expired offline schedules could not be physically removed.", storageError);
  }
  const marker = getOfflineAccessMarker();
  if (!marker || isOfflineAccessValid(marker)) {
    return { marker, expired: false };
  }
  try {
    await clearOfflineCalendarCacheForUser(marker.user_id);
  } catch (storageError) {
    console.warn("The expired offline schedule could not be physically removed.", storageError);
  }
  try {
    clearOfflineAccessMarker();
  } catch (storageError) {
    console.warn("The expired offline marker could not be removed.", storageError);
  }
  return { marker: null, expired: true };
}

/**
 * Provide session state, logout handling, and user refresh operations to the app.
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const { isReady } = useServiceAvailability();
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [authStatus, setAuthStatus] = useState<AuthStatus>("checking");
  const [offlineAccess, setOfflineAccess] = useState<OfflineAccessMarker | null>(null);
  const [offlineAccessExpired, setOfflineAccessExpired] = useState(false);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const logoutPromise = useRef<Promise<boolean> | null>(null);
  const userRequestSequence = useRef(0);
  const userIdRef = useRef<number | null>(null);
  const authenticatedUserRef = useRef<User | null>(null);

  const fetchUser = useCallback(async () => {
    const sequence = ++userRequestSequence.current;
    setIsLoading(true);
    if (!isReady) {
      const offlineState = await readOfflineAccessState();
      if (sequence !== userRequestSequence.current) return;
      setOfflineAccess(offlineState.marker);
      setOfflineAccessExpired(offlineState.expired);
      setAuthStatus("offline");
      // Keep an already-open root control panel mounted while ownership is
      // changing. This grants no server access: every protected request still
      // has to validate the HttpOnly session, and a fresh login remains fenced.
      if (!authenticatedUserRef.current?.is_root_admin) {
        authenticatedUserRef.current = null;
        userIdRef.current = null;
        setUser(null);
      }
      setIsLoading(false);
      return;
    }
    setAuthStatus("checking");
    try {
      const apiUrl = getApiUrl();
      const response = await fetch(`${apiUrl}/api/v1/auth/me`, {
        credentials: "include",
      });
      if (sequence !== userRequestSequence.current) return;

      if (response.ok) {
        const userData = await response.json();
        if (sequence !== userRequestSequence.current) return;
        authenticatedUserRef.current = userData;
        userIdRef.current = userData.id;
        setUser(userData);
        if (userData.recovery_setup_required) {
          setOfflineAccess(null);
          const path = window.location.pathname;
          if (path !== "/bootstrap" && path !== "/login") {
            hardNavigate("/bootstrap");
          }
        } else {
          setOfflineAccess(storeOfflineAccessForUser(userData));
        }
        setOfflineAccessExpired(false);
        setAuthStatus("authenticated");
      } else if (response.status === 401 || response.status === 403) {
        const marker = getOfflineAccessMarker();
        const userIdToClear = userIdRef.current ?? marker?.user_id;
        if (userIdToClear) {
          await clearOfflineCalendarCacheForUser(userIdToClear);
        }
        await clearLegacyPrivateCaches();
        try {
          clearOfflineAccessMarker();
        } catch (storageError) {
          console.warn("The expired offline marker could not be removed.", storageError);
        }
        setOfflineAccess(null);
        setOfflineAccessExpired(false);
        setAuthStatus("unauthenticated");
        authenticatedUserRef.current = null;
        userIdRef.current = null;
        setUser(null);
      } else {
        throw new TypeError(`Session check unavailable (${response.status})`);
      }
    } catch (err) {
      if (sequence !== userRequestSequence.current) return;
      console.error("Failed to fetch user:", err);
      const offlineState = await readOfflineAccessState();
      if (sequence !== userRequestSequence.current) return;
      setOfflineAccess(offlineState.marker);
      setOfflineAccessExpired(offlineState.expired);
      setAuthStatus("offline");
      authenticatedUserRef.current = null;
      userIdRef.current = null;
      setUser(null);
    } finally {
      if (sequence === userRequestSequence.current) setIsLoading(false);
    }
  }, [isReady]);

  useEffect(() => {
    void fetchUser();
  }, [fetchUser]);

  const logout = (): Promise<boolean> => {
    if (logoutPromise.current) return logoutPromise.current;

    const operation = (async () => {
      // A session check that started before logout must never restore the user
      // after the server has revoked the session.
      userRequestSequence.current += 1;
      setIsLoggingOut(true);
      setLogoutError(null);
      try {
        const loggedOutUserId = user?.id;
        const response = await apiFetch("/api/v1/auth/logout", {
          method: "POST",
          body: JSON.stringify({}),
        });
        if (!response.ok && response.status !== 401) {
          throw new Error(`The server rejected logout (${response.status}). Please try again.`);
        }

        // Change the visible authentication state as soon as the server has
        // revoked (or no longer recognises) the session. Cache cleanup must not
        // make a successful logout appear stuck.
        try {
          clearOfflineAccessMarker();
        } catch (storageError) {
          console.warn("Logout completed, but the offline marker could not be removed.", storageError);
        }
        setOfflineAccess(null);
        setOfflineAccessExpired(false);
        setAuthStatus("unauthenticated");
        authenticatedUserRef.current = null;
        userIdRef.current = null;
        setUser(null);

        const cleanup = await Promise.allSettled([
          loggedOutUserId
            ? clearOfflineCalendarCacheForUser(loggedOutUserId)
            : Promise.resolve(),
          clearLegacyPrivateCaches(),
        ]);
        if (cleanup.some((result) => result.status === "rejected")) {
          console.warn("Logout completed, but some local cache cleanup failed.");
        }
        return true;
      } catch (err) {
        console.error("Logout request failed:", err);
        setLogoutError(
          err instanceof Error
            ? err.message
            : "Logout could not reach the server. Please try again.",
        );
        return false;
      } finally {
        setIsLoggingOut(false);
        logoutPromise.current = null;
      }
    })();
    logoutPromise.current = operation;
    return operation;
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: user !== null,
        isLoading,
        authStatus,
        offlineAccess,
        offlineAccessExpired,
        isLoggingOut,
        logoutError,
        logout,
        dismissLogoutError: () => setLogoutError(null),
        refreshUser: fetchUser,
      }}
    >
      {children}
      {logoutError && (
        <div
          role="alert"
          className="fixed inset-x-4 top-4 z-[100] mx-auto flex max-w-xl items-start justify-between gap-4 rounded-xl border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900 shadow-lg dark:border-red-800 dark:bg-red-950 dark:text-red-100"
        >
          <span>{logoutError}</span>
          <button
            type="button"
            onClick={() => setLogoutError(null)}
            className="shrink-0 font-medium underline underline-offset-2"
          >
            Dismiss
          </button>
        </div>
      )}
    </AuthContext.Provider>
  );
}

/**
 * Return the current authentication context.
 *
 * Must be called from a component rendered under `AuthProvider`.
 */
export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}

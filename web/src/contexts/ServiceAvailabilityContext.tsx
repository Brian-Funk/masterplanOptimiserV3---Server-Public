"use client";

import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

export type ServiceState =
  | "checking"
  | "ready"
  | "device_offline"
  | "service_unreachable"
  | "planned_handoff"
  | "failover_wait"
  | "automatic_failover_disabled"
  | "promoting"
  | "routing"
  | "control_unavailable"
  | "standby_shell";

export interface PublicServiceStatus {
  format: "mp-opt-ha-public-status-v1";
  mode: "standalone" | "ha";
  state: Exclude<ServiceState, "checking" | "device_offline" | "service_unreachable">;
  reason: string | null;
  observed_at: string;
  transition_started_at: string | null;
  earliest_failover_at: string | null;
  recovery_point_at: string | null;
  retry_after_seconds: number;
  capabilities: {
    sign_in: boolean;
    live_reads: boolean;
    writes: boolean;
    public_links: boolean;
  };
  last_recovery: {
    kind: "planned_handoff" | "automatic_failover";
    completed_at: string | null;
    recovery_seconds: number | null;
  } | null;
}

interface ServiceAvailabilityContextValue {
  state: ServiceState;
  status: PublicServiceStatus | null;
  isReady: boolean;
  refresh: () => Promise<void>;
}

const ServiceAvailabilityContext = createContext<ServiceAvailabilityContextValue | undefined>(undefined);
const STATUS_REQUEST_TIMEOUT_MS = 4_000;
const CHECKING_FAILSAFE_MS = 11_000;

function validStatus(value: unknown): value is PublicServiceStatus {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<PublicServiceStatus>;
  return candidate.format === "mp-opt-ha-public-status-v1" &&
    typeof candidate.state === "string" && Boolean(candidate.capabilities);
}

/** Observe the public HA status without depending on authentication or database ownership. */
export function ServiceAvailabilityProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<ServiceState>("checking");
  const [status, setStatus] = useState<PublicServiceStatus | null>(null);
  const inFlight = useRef(false);
  const requestSequence = useRef(0);
  const consecutiveFailures = useRef(0);

  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    if (typeof navigator !== "undefined" && !navigator.onLine) {
      setState("device_offline");
      return;
    }
    inFlight.current = true;
    const sequence = ++requestSequence.current;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), STATUS_REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch("/ha/status", {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`status-${response.status}`);
      const payload: unknown = await response.json();
      if (!validStatus(payload)) throw new Error("invalid-status");
      if (sequence !== requestSequence.current) return;
      consecutiveFailures.current = 0;
      setStatus(payload);
      setState(payload.state);
    } catch {
      if (sequence !== requestSequence.current) return;
      if (typeof navigator !== "undefined" && !navigator.onLine) {
        consecutiveFailures.current = 0;
        setState("device_offline");
      } else {
        consecutiveFailures.current += 1;
        if (consecutiveFailures.current >= 2) setState("service_unreachable");
      }
    } finally {
      window.clearTimeout(timeout);
      inFlight.current = false;
    }
  }, []);

  useEffect(() => {
    void refresh();
    const online = () => void refresh();
    const offline = () => setState("device_offline");
    window.addEventListener("online", online);
    window.addEventListener("offline", offline);
    return () => {
      window.removeEventListener("online", online);
      window.removeEventListener("offline", offline);
    };
  }, [refresh]);

  useEffect(() => {
    const interval = window.setInterval(
      () => void refresh(),
      5_000,
    );
    return () => window.clearInterval(interval);
  }, [refresh]);

  useEffect(() => {
    if (state !== "checking") return;
    const failsafe = window.setTimeout(() => {
      setState((current) => current === "checking"
        ? (navigator.onLine ? "service_unreachable" : "device_offline")
        : current);
    }, CHECKING_FAILSAFE_MS);
    return () => window.clearTimeout(failsafe);
  }, [state]);

  return (
    <ServiceAvailabilityContext.Provider value={{ state, status, isReady: state === "ready", refresh }}>
      {children}
    </ServiceAvailabilityContext.Provider>
  );
}

export function useServiceAvailability(): ServiceAvailabilityContextValue {
  const context = useContext(ServiceAvailabilityContext);
  if (!context) throw new Error("useServiceAvailability must be used within ServiceAvailabilityProvider");
  return context;
}

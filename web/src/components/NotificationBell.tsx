"use client";

import { useState, useEffect, useCallback } from "react";
import { Bell, BellOff, BellRing } from "lucide-react";
import { apiFetch } from "@/lib/api";

/** Props for `NotificationBell`. */
export interface NotificationBellProps {
  eventId: number;
}

/**
 * Notification bell that lets users subscribe to or unsubscribe from push
 * notifications for the current event.
 *
 * Shows visual state for browser permission and subscription status.
 */
export function NotificationBell({ eventId }: NotificationBellProps) {
  const [supported, setSupported] = useState(false);
  const [permission, setPermission] =
    useState<NotificationPermission>("default");
  const [subscribed, setSubscribed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [vapidKey, setVapidKey] = useState<string | null>(null);

  // Check browser support
  useEffect(() => {
    const ok =
      "serviceWorker" in navigator &&
      "PushManager" in window &&
      "Notification" in window;
    setSupported(ok);
    if (ok) {
      setPermission(Notification.permission);
    }
  }, []);

  // Fetch VAPID key + subscription status
  useEffect(() => {
    if (!supported) return;

    apiFetch("/api/v1/notifications/vapid-key")
      .then((res) => res.json())
      .then((data) => {
        if (data.public_key) setVapidKey(data.public_key);
      })
      .catch(() => {});

    apiFetch(`/api/v1/notifications/status/${eventId}`)
      .then((res) => res.json())
      .then((data) => setSubscribed(data.subscribed))
      .catch(() => {});
  }, [supported, eventId]);

  const handleToggle = useCallback(async () => {
    if (!supported || !vapidKey) return;
    setLoading(true);

    try {
      if (subscribed) {
        // Unsubscribe
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.getSubscription();
        if (sub) {
          await apiFetch("/api/v1/notifications/subscribe", {
            method: "DELETE",
            body: JSON.stringify({ endpoint: sub.endpoint }),
          });
          await sub.unsubscribe();
        }
        setSubscribed(false);
      } else {
        // Request permission if needed
        if (Notification.permission === "default") {
          const perm = await Notification.requestPermission();
          setPermission(perm);
          if (perm !== "granted") {
            setLoading(false);
            return;
          }
        } else if (Notification.permission === "denied") {
          setLoading(false);
          return;
        }

        // Subscribe via Push API
        const reg = await navigator.serviceWorker.ready;

        // Convert base64url VAPID key to Uint8Array
        const padding = "=".repeat((4 - (vapidKey.length % 4)) % 4);
        const base64 = (vapidKey + padding)
          .replace(/-/g, "+")
          .replace(/_/g, "/");
        const rawKey = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));

        const sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: rawKey,
        });

        const subJson = sub.toJSON();
        await apiFetch("/api/v1/notifications/subscribe", {
          method: "POST",
          body: JSON.stringify({
            event_id: eventId,
            endpoint: sub.endpoint,
            p256dh: subJson.keys?.p256dh || "",
            auth: subJson.keys?.auth || "",
          }),
        });

        setSubscribed(true);
      }
    } catch (err) {
      console.error("Push subscription error:", err);
    } finally {
      setLoading(false);
    }
  }, [supported, vapidKey, subscribed, eventId]);

  if (!supported || !vapidKey) return null;

  const denied = permission === "denied";

  return (
    <button
      onClick={handleToggle}
      disabled={loading || denied}
      className={`p-2 rounded-lg transition-colors ${
        denied
          ? "text-gray-300 dark:text-gray-600 cursor-not-allowed"
          : subscribed
            ? "text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20"
            : "text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800"
      }`}
      aria-label={
        denied
          ? "Notifications blocked"
          : subscribed
            ? "Disable notifications"
            : "Enable notifications"
      }
      title={
        denied
          ? "Notifications are blocked in your browser settings"
          : subscribed
            ? "Notifications enabled - click to disable"
            : "Enable push notifications"
      }
    >
      {loading ? (
        <BellRing size={20} className="animate-pulse" />
      ) : subscribed ? (
        <BellRing size={20} />
      ) : denied ? (
        <BellOff size={20} />
      ) : (
        <Bell size={20} />
      )}
    </button>
  );
}

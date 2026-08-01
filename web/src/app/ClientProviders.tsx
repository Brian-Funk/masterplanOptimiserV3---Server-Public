"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { AuthProvider } from "@/contexts/AuthContext";
import { ServiceAvailabilityProvider } from "@/contexts/ServiceAvailabilityContext";
import { InstallPrompt } from "@/components/InstallPrompt";

/** Provide route-appropriate client state and register offline support. */
export default function ClientProviders({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();

  // Check for a new worker without interrupting activation/passkey ceremonies
  // or an offline schedule. The next safe navigation naturally uses the new
  // release; controller changes must never force a reload loop.
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;

    navigator.serviceWorker
      .register("/sw.js", { updateViaCache: "none" })
      .then((registration) => registration.update())
      .catch((err) => {
        console.warn("SW registration failed:", err);
      });

  }, []);

  const themedContent = <>{children}<InstallPrompt /></>;

  if (pathname === "/shared-schedule") return (
    <ThemeProvider><ServiceAvailabilityProvider>{themedContent}</ServiceAvailabilityProvider></ThemeProvider>
  );

  return (
    <ThemeProvider>
      <ServiceAvailabilityProvider>
        <AuthProvider>{themedContent}</AuthProvider>
      </ServiceAvailabilityProvider>
    </ThemeProvider>
  );
}

"use client";

import { useEffect, useRef } from "react";
import { useTheme } from "@/contexts/ThemeContext";
import { BRAND } from "@/lib/brand";

/** Props for `DynamicPWA`. */
export interface DynamicPWAProps {
  eventName?: string | null;
}

/**
 * Invisible component that dynamically generates a fixed-brand PWA icon
 * (favicon, manifest, and apple-touch-icon) using the current light or dark
 * theme.
 *
 * Replicates the Logo component's mask gradient overlay technique on a
 * canvas, then creates a dynamic Web App Manifest with the resulting PNGs.
 */
export function DynamicPWA({ eventName }: DynamicPWAProps) {
  const { theme } = useTheme();
  const prevKey = useRef("");

  useEffect(() => {
    const c1 = BRAND.color1;
    const c2 = BRAND.color2;
    const isDark = theme === "dark";
    const key = `${c1}-${c2}-${isDark}`;

    if (prevKey.current === key) return;
    prevKey.current = key;

    Promise.all([
      generateIcon(c1, c2, isDark, 192),
      generateIcon(c1, c2, isDark, 512),
    ])
      .then(([icon192, icon512]) => {
        // ---- Favicon ----
        let favicon = document.querySelector(
          'link[rel="icon"]',
        ) as HTMLLinkElement | null;
        if (!favicon) {
          favicon = document.createElement("link");
          favicon.rel = "icon";
          document.head.appendChild(favicon);
        }
        favicon.href = icon192;
        favicon.type = "image/png";

        // ---- Apple-touch-icon ----
        let apple = document.querySelector(
          'link[rel="apple-touch-icon"]',
        ) as HTMLLinkElement | null;
        if (!apple) {
          apple = document.createElement("link");
          apple.rel = "apple-touch-icon";
          document.head.appendChild(apple);
        }
        apple.href = icon192;

        // ---- Dynamic manifest ----
        const fullName = eventName
          ? `Masterplan: ${eventName}`
          : "Masterplan Optimiser";
        const manifest = {
          name: fullName,
          short_name: fullName,
          start_url: ".",
          display: "standalone" as const,
          background_color: isDark ? "#1f2937" : "#f9fafb",
          theme_color: c1,
          icons: [
            { src: icon192, sizes: "192x192", type: "image/png" },
            { src: icon512, sizes: "512x512", type: "image/png" },
          ],
        };

        const blob = new Blob([JSON.stringify(manifest)], {
          type: "application/json",
        });
        const url = URL.createObjectURL(blob);

        let manifestLink = document.querySelector(
          'link[rel="manifest"]',
        ) as HTMLLinkElement | null;
        if (!manifestLink) {
          manifestLink = document.createElement("link");
          manifestLink.rel = "manifest";
          document.head.appendChild(manifestLink);
        }
        if (manifestLink.href.startsWith("blob:")) {
          URL.revokeObjectURL(manifestLink.href);
        }
        manifestLink.href = url;

        // ---- Theme-color meta ----
        let themeMeta = document.querySelector(
          'meta[name="theme-color"]',
        ) as HTMLMetaElement | null;
        if (!themeMeta) {
          themeMeta = document.createElement("meta");
          themeMeta.name = "theme-color";
          document.head.appendChild(themeMeta);
        }
        themeMeta.content = c1;
      })
      .catch(() => {
        // Silently fall back to the static manifest
      });
  }, [theme, eventName]);

  return null;
}

// ---------------------------------------------------------------------------
// Canvas-based icon generation
// ---------------------------------------------------------------------------

function generateIcon(
  c1: string,
  c2: string,
  isDark: boolean,
  size: number,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext("2d");
    if (!ctx) return reject(new Error("no 2d context"));

    // 1. Draw gradient --------------------------------------------------
    const gradient = ctx.createLinearGradient(0, 0, size, size);
    gradient.addColorStop(0, c1);
    gradient.addColorStop(1, c2);
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, size, size);

    // 2. Load mask and apply luminance to alpha.
    const maskImg = new Image();
    maskImg.crossOrigin = "anonymous";
    maskImg.onload = () => {
      const tmp = document.createElement("canvas");
      tmp.width = size;
      tmp.height = size;
      const tmpCtx = tmp.getContext("2d")!;
      tmpCtx.drawImage(maskImg, 0, 0, size, size);

      const maskData = tmpCtx.getImageData(0, 0, size, size);
      const d = maskData.data;
      for (let i = 0; i < d.length; i += 4) {
        // Luminance from RGB becomes the alpha channel.
        const lum = d[i] * 0.299 + d[i + 1] * 0.587 + d[i + 2] * 0.114;
        d[i] = 255;
        d[i + 1] = 255;
        d[i + 2] = 255;
        d[i + 3] = lum;
      }
      tmpCtx.putImageData(maskData, 0, 0);

      ctx.globalCompositeOperation = "destination-in";
      ctx.drawImage(tmp, 0, 0);

      // 3. Draw logo on top ---------------------------------------------
      ctx.globalCompositeOperation = "source-over";
      const logoImg = new Image();
      logoImg.crossOrigin = "anonymous";
      logoImg.onload = () => {
        ctx.drawImage(logoImg, 0, 0, size, size);
        resolve(canvas.toDataURL("image/png"));
      };
      logoImg.onerror = () => resolve(canvas.toDataURL("image/png")); // gradient only
      logoImg.src = isDark ? "/logo_dark.svg" : "/logo_normal.svg";
    };
    maskImg.onerror = () => resolve(canvas.toDataURL("image/png")); // gradient only
    maskImg.src = "/mask.svg";
  });
}

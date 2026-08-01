"use client";

import { useTheme } from "@/contexts/ThemeContext";
import { BRAND } from "@/lib/brand";
import Image from "next/image";

/** Props for `Logo`. */
export interface LogoProps {
  className?: string;
  height?: number;
  href?: string;
}

/** The fixed Masterplan Optimiser logo. */
export function Logo({
  className = "",
  height = 40,
  href,
}: LogoProps) {
  const { theme } = useTheme();
  const isDark = theme === "dark";

  const inner = (
    <div className={`relative inline-block ${className}`} style={{ height }}>
      {/* Gradient layer, masked by mask.svg (white=show, black=hide) */}
      <div
        className="absolute inset-0"
        style={
          {
            background: `linear-gradient(135deg, ${BRAND.color1} 0%, ${BRAND.color2} 100%)`,
            maskImage: `url(/mask.png)`,
            maskSize: "contain",
            maskRepeat: "no-repeat",
            maskPosition: "center",
            WebkitMaskImage: `url(/mask.png)`,
            WebkitMaskSize: "contain",
            WebkitMaskRepeat: "no-repeat",
            WebkitMaskPosition: "center",
          } as React.CSSProperties
        }
      />

      {/* Logo artwork on top */}
      <Image
        src={isDark ? "/logo_dark.svg" : "/logo_normal.svg"}
        alt="Masterplan Optimiser"
        width={height}
        height={height}
        style={{ height }}
        className="relative z-10 h-full w-auto object-contain"
      />
    </div>
  );

  if (href) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer">
        {inner}
      </a>
    );
  }

  return inner;
}

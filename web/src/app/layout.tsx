import type { Metadata } from "next";
import "./globals.css";
import ClientProviders from "./ClientProviders";

export const metadata: Metadata = {
  metadataBase: new URL("https://mp-opt.net"),
  title: "Masterplan Optimiser",
  description:
    "Collaborative scheduling and masterplan optimisation for live events.",
  openGraph: {
    title: "Masterplan Optimiser",
    description:
      "Collaborative scheduling and masterplan optimisation for live events.",
    type: "website",
    images: [{ url: "/og-image.png", width: 1200, height: 630 }],
  },
  twitter: {
    card: "summary_large_image",
    title: "Masterplan Optimiser",
    description:
      "Collaborative scheduling and masterplan optimisation for live events.",
    images: ["/og-image.png"],
  },
  robots: "noindex, nofollow",
  icons: {
    icon: { url: "/logo_normal.png", type: "image/png" },
    apple: "/logo_normal.png",
  },
  manifest: "/manifest.json",
  other: {
    "theme-color": "#2563eb",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <ClientProviders>{children}</ClientProviders>
      </body>
    </html>
  );
}

import type { Metadata } from "next";
import "./globals.css";
import ClientProviders from "./ClientProviders";

export const metadata: Metadata = {
  title: "Masterplan Optimiser",
  description:
    "Collaborative scheduling and masterplan optimisation for live events.",
  openGraph: {
    title: "Masterplan Optimiser",
    description:
      "Collaborative scheduling and masterplan optimisation for live events.",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Masterplan Optimiser",
    description:
      "Collaborative scheduling and masterplan optimisation for live events.",
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

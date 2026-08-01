import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Activate Your Account - Masterplan Optimiser",
  description: "Set up your account and register your passkey to get started.",
  openGraph: {
    title: "Activate Your Account - Masterplan Optimiser",
    description:
      "Set up your account and register your passkey to get started.",
  },
};

export default function ActivateLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}

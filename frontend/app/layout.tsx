import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Coding Helper",
  description: "AI coding mentor — get hints, not full solutions.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

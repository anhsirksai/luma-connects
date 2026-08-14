import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Luma Connects by Bright Data",
  description:
    "A Bright Data example for GTM teams building event invite lists from public web data reports.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">{children}</body>
    </html>
  );
}

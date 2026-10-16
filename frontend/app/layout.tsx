import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "VibeStack",
  description: "Describe an app. Eight AI agents design it, build it in FastAPI, Flask or Django, test it, fix it and run it.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        {/* eslint-disable-next-line @next/next/no-page-custom-font -- single-page app: the root layout is every page */}
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap"
        />
      </head>
      <body className="min-h-full font-sans text-ink-100">{children}</body>
    </html>
  );
}

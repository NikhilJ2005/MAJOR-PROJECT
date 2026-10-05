import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "VibeStack: agentic backend generator",
  description: "Natural language to a validated, self-healed FastAPI backend, orchestrated with LangGraph.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full bg-zinc-950 text-zinc-100">{children}</body>
    </html>
  );
}

import type { Metadata } from "next";
import type { ReactNode } from "react";
import { AuthProvider } from "@/lib/auth";
import { TopBar } from "@/components/topbar";
import "./globals.css";

export const metadata: Metadata = {
  title: "AEGIS AI — Operator Console",
  description:
    "Operator console for the AEGIS AI autonomous esports operating system.",
};

/**
 * AEGIS AI dark shell: top bar (wordmark + nav + auth state) and main content.
 * Individual pages live in their own route directories (built by other workers).
 */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>
          <TopBar />
          <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
            {children}
          </main>
        </AuthProvider>
      </body>
    </html>
  );
}

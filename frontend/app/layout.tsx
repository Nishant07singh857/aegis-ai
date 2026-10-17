import type { Metadata } from "next";
import type { ReactNode } from "react";
import { AuthProvider } from "@/lib/auth";
import { Chrome } from "@/components/chrome";
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
          <Chrome>{children}</Chrome>
        </AuthProvider>
      </body>
    </html>
  );
}

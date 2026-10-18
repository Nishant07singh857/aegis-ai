"use client";

/**
 * AEGIS AI — /live route (Phase 6).
 *
 * The live dashboard lives at "/"; this route exists so the role matrix
 * (viewer: /watch, operator/admin: /live) resolves to a real route.
 * Requires operator+ — viewers are bounced to /watch by the guard.
 */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { RequireRole } from "@/lib/guard";
import { Loading } from "@/components/ui";

function LiveRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/");
  }, [router]);
  return (
    <div className="flex min-h-screen items-center justify-center bg-[#0a0a0f] text-white">
      <Loading label="Opening live dashboard" />
    </div>
  );
}

export default function LivePage() {
  return (
    <RequireRole roles={["operator", "admin"]}>
      <LiveRedirect />
    </RequireRole>
  );
}

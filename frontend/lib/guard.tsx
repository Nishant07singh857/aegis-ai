"use client";

/**
 * AEGIS AI — route guard (Phase 6).
 *
 * Wraps a page and enforces the role matrix:
 *   not signed in            -> /login
 *   signed in, role not in `roles` -> /watch (the role home of the lowest
 *                                     tier every signed-in user may reach)
 *   otherwise                -> renders the page
 *
 * The backend enforces authorization on every API call; this guard only
 * decides what the signed-in user may see.
 */

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "./auth";
import { ROLE_HOME } from "./useRole";
import type { Role } from "./useRole";
import { Loading } from "@/components/ui";

export function RequireRole({
  roles,
  children,
}: {
  roles: Role[];
  children: ReactNode;
}) {
  const router = useRouter();
  const { isAuthed, role } = useAuth();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!mounted) return;
    if (!isAuthed) {
      router.replace("/login");
    } else if (!roles.includes(role)) {
      router.replace(ROLE_HOME[role]);
    }
  }, [mounted, isAuthed, role, roles, router]);

  if (!mounted || !isAuthed || !roles.includes(role)) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#0a0a0f] text-white">
        <Loading label="Checking access" />
      </div>
    );
  }

  return <>{children}</>;
}

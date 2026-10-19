"use client";

/**
 * AEGIS AI — top navigation bar. Wordmark, section nav, and operator auth
 * state (operator name + Logout, or a Login link when signed out).
 */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { ROLE_HOME } from "@/lib/useRole";
import type { Role } from "@/lib/useRole";

/**
 * Role-aware navigation (Phase 6):
 *   viewer   — Watch only
 *   operator — Watch, Live, Control, Director, Teams, Commentary
 *   admin    — everything operator sees, plus Admin
 */
const NAV_BY_ROLE: Record<Role, { href: string; label: string }[]> = {
  viewer: [{ href: "/watch", label: "Watch" }],
  observer: [{ href: "/observer", label: "Observer" }],
  spotter: [
    { href: "/watch", label: "Watch" },
    { href: "/spotter", label: "Spotter" },
  ],
  director: [
    { href: "/watch", label: "Watch" },
    { href: "/broadcast/director", label: "Director" },
  ],
  operator: [
    { href: "/watch", label: "Watch" },
    { href: "/", label: "Live" },
    { href: "/control", label: "Control" },
    { href: "/broadcast/director", label: "Director" },
    { href: "/teams", label: "Teams" },
    { href: "/commentary", label: "Commentary" },
  ],
  admin: [
    { href: "/watch", label: "Watch" },
    { href: "/", label: "Live" },
    { href: "/control", label: "Control" },
    { href: "/broadcast/director", label: "Director" },
    { href: "/teams", label: "Teams" },
    { href: "/commentary", label: "Commentary" },
    { href: "/admin", label: "Admin" },
  ],
};

const ROLE_BADGE_STYLES: Record<Role, string> = {
  viewer: "border-white/20 bg-white/5 text-zinc-300",
  operator: "border-[#00f0ff]/40 bg-[#00f0ff]/10 text-[#00f0ff]",
  admin: "border-amber-300/40 bg-amber-300/10 text-amber-300",
  director: "border-violet-300/40 bg-violet-300/10 text-violet-300",
  spotter: "border-green-300/40 bg-green-300/10 text-green-300",
  observer: "border-sky-300/40 bg-sky-300/10 text-sky-300",
};

/**
 * Read a display name from the JWT payload without verifying it.
 * Display-only: never used for authorization decisions.
 */
function operatorNameFromToken(token: string | null): string | null {
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const json = atob(parts[1].replace(/-/g, "+").replace(/_/g, "/"));
    const payload = JSON.parse(json) as { username?: unknown; sub?: unknown };
    const name =
      typeof payload.username === "string" && payload.username.length > 0
        ? payload.username
        : typeof payload.sub === "string" && payload.sub.length > 0
          ? payload.sub
          : null;
    return name;
  } catch {
    return null;
  }
}

export function TopBar() {
  const pathname = usePathname();
  const router = useRouter();
  const { isAuthed, token, role, logout } = useAuth();
  const operatorName = operatorNameFromToken(token);

  const navItems = NAV_BY_ROLE[role];

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  const handleLogout = () => {
    logout();
    router.push("/login");
  };

  return (
    <header className="sticky top-0 z-40 border-b border-white/10 bg-[#0a0a0f]/95 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-2 px-4 sm:px-6 md:gap-4">
        <Link
          href={isAuthed ? ROLE_HOME[role] : "/login"}
          className="flex items-baseline gap-2 shrink-0"
        >
          <span className="font-mono text-lg font-bold tracking-[0.25em] text-white">
            AEGIS
          </span>
          <span className="font-mono text-lg font-bold tracking-[0.25em] text-[#00f0ff]">
            AI
          </span>
        </Link>

        <nav className="flex min-w-0 items-center gap-1 overflow-x-auto" aria-label="Primary">
          {navItems.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-current={isActive(item.href) ? "page" : undefined}
              className={`rounded-lg px-3 py-1.5 font-mono text-xs uppercase tracking-wider whitespace-nowrap transition-colors ${
                isActive(item.href)
                  ? "text-[#00f0ff] bg-[#00f0ff]/10"
                  : "text-zinc-400 hover:text-zinc-100 hover:bg-white/5"
              }`}
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-3 shrink-0">
          {isAuthed ? (
            <>
              <span className="hidden font-mono text-xs text-zinc-400 md:inline">
                Signed in as{" "}
                <span className="text-[#00f0ff]">
                  {operatorName ?? "operator"}
                </span>
              </span>
              <span
                className={`rounded-md border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${ROLE_BADGE_STYLES[role]}`}
                title="Your access role"
              >
                {role}
              </span>
              <button
                type="button"
                onClick={handleLogout}
                className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-zinc-300 hover:border-[#ff3366]/60 hover:text-[#ff3366] transition-colors max-md:min-h-[44px] max-md:px-4"
              >
                Logout
              </button>
            </>
          ) : (
            <Link
              href="/login"
              className="rounded-lg border border-[#00f0ff]/40 bg-[#00f0ff]/10 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-[#00f0ff] hover:bg-[#00f0ff]/20 transition-colors max-md:inline-flex max-md:min-h-[44px] max-md:items-center max-md:px-4"
            >
              Login
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}

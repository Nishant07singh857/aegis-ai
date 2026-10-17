"use client";

/**
 * AEGIS AI — top navigation bar. Wordmark, section nav, and operator auth
 * state (operator name + Logout, or a Login link when signed out).
 */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

const NAV_ITEMS = [
  { href: "/", label: "Live" },
  { href: "/teams", label: "Teams" },
  { href: "/broadcast", label: "Broadcast" },
  { href: "/commentary", label: "Commentary" },
  { href: "/admin", label: "Admin" },
];

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
  const { isAuthed, token, logout } = useAuth();
  const operatorName = operatorNameFromToken(token);

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  const handleLogout = () => {
    logout();
    router.push("/login");
  };

  return (
    <header className="sticky top-0 z-40 border-b border-white/10 bg-[#0a0a0f]/95 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-4 px-4 sm:px-6">
        <Link href="/" className="flex items-baseline gap-2 shrink-0">
          <span className="font-mono text-lg font-bold tracking-[0.25em] text-white">
            AEGIS
          </span>
          <span className="font-mono text-lg font-bold tracking-[0.25em] text-[#00f0ff]">
            AI
          </span>
        </Link>

        <nav className="flex items-center gap-1 overflow-x-auto" aria-label="Primary">
          {NAV_ITEMS.map((item) => (
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
              <span className="font-mono text-xs text-zinc-400">
                Signed in as{" "}
                <span className="text-[#00f0ff]">
                  {operatorName ?? "operator"}
                </span>
              </span>
              <button
                type="button"
                onClick={handleLogout}
                className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-zinc-300 hover:border-[#ff3366]/60 hover:text-[#ff3366] transition-colors"
              >
                Logout
              </button>
            </>
          ) : (
            <Link
              href="/login"
              className="rounded-lg border border-[#00f0ff]/40 bg-[#00f0ff]/10 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-[#00f0ff] hover:bg-[#00f0ff]/20 transition-colors"
            >
              Login
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}

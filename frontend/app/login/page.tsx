"use client";

/**
 * AEGIS AI — role-aware login (Phase 6, redirect matrix extended Phase 16).
 * Uses useAuth().login, which stores the JWT and resolves the role from the
 * login response; surfaces the thrown error honestly. On success — and
 * whenever an already-signed-in user lands here — redirects by role:
 *   observer -> /observer
 *   spotter  -> /spotter
 *   director -> /broadcast/director
 *   admin    -> /admin
 *   operator -> /control
 *   viewer   -> /watch
 */

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { ROLE_HOME } from "@/lib/useRole";
import { Card } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const { isAuthed, role, login, logout } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Signed-in users don't stay on the login page; they land on their
  // role's home.
  useEffect(() => {
    if (isAuthed) {
      router.replace(ROLE_HOME[role]);
    }
  }, [isAuthed, role, router]);

  if (isAuthed) {
    return (
      <div className="mx-auto max-w-md pt-8 md:pt-12">
        <Card title="Session">
          <p className="text-sm text-zinc-300 mb-4">
            Signed in as{" "}
            <span className="text-[#00f0ff]">{role}</span>.
          </p>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <button
              type="button"
              onClick={() => router.replace(ROLE_HOME[role])}
              className="rounded-lg bg-[#00f0ff] px-4 py-2 font-mono text-xs font-bold uppercase tracking-wider text-black hover:opacity-90 transition-opacity max-md:min-h-[48px] max-md:text-sm"
            >
              Continue
            </button>
            <button
              type="button"
              onClick={logout}
              className="rounded-lg border border-white/15 bg-white/5 px-4 py-2 font-mono text-xs uppercase tracking-wider text-zinc-200 hover:border-[#ff3366]/60 hover:text-[#ff3366] transition-colors max-md:min-h-[48px] max-md:text-sm"
            >
              Logout
            </button>
          </div>
        </Card>
      </div>
    );
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const signedInRole = await login(username.trim(), password);
      router.replace(ROLE_HOME[signedInRole]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-md pt-8 md:pt-12">
      <Card title="Operator Login">
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1.5">
            <span className="font-mono text-xs uppercase tracking-wider text-zinc-400">
              Username
            </span>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
              className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 outline-none focus:border-[#00f0ff]/60 max-md:min-h-[52px] max-md:px-4 max-md:text-base"
              placeholder="operator username"
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="font-mono text-xs uppercase tracking-wider text-zinc-400">
              Password
            </span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
              className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 outline-none focus:border-[#00f0ff]/60 max-md:min-h-[52px] max-md:px-4 max-md:text-base"
              placeholder="operator password"
            />
          </label>

          {error && (
            <p
              role="alert"
              className="rounded-lg border border-[#ff3366]/30 bg-[#ff3366]/5 px-3 py-2 text-sm text-[#ff3366]"
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="rounded-lg bg-[#00f0ff] px-4 py-2 font-mono text-sm font-bold uppercase tracking-wider text-black transition-opacity hover:opacity-90 disabled:opacity-40 max-md:min-h-[56px] max-md:text-base"
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </Card>
    </div>
  );
}

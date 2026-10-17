"use client";

/**
 * AEGIS AI — operator login. Uses useAuth().login; surfaces the thrown
 * error honestly; on success routes to the live dashboard (/).
 */

import { useState } from "react";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { Card } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const { isAuthed, login, logout } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (isAuthed) {
    return (
      <div className="mx-auto max-w-md pt-12">
        <Card title="Session">
          <p className="text-sm text-zinc-300 mb-4">
            Signed in as <span className="text-[#00f0ff]">operator</span>.
          </p>
          <button
            type="button"
            onClick={logout}
            className="rounded-lg border border-white/15 bg-white/5 px-4 py-2 font-mono text-xs uppercase tracking-wider text-zinc-200 hover:border-[#ff3366]/60 hover:text-[#ff3366] transition-colors"
          >
            Logout
          </button>
        </Card>
      </div>
    );
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username.trim(), password);
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-md pt-12">
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
              className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 outline-none focus:border-[#00f0ff]/60"
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
              className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 outline-none focus:border-[#00f0ff]/60"
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
            className="rounded-lg bg-[#00f0ff] px-4 py-2 font-mono text-sm font-bold uppercase tracking-wider text-black transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </Card>
    </div>
  );
}

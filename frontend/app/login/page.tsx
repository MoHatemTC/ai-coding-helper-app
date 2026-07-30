"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { registerUser, loginUser, createSession } from "@/lib/api";
import { setUserToken, setSession } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [username, setUsername] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function afterAuth(userToken: string) {
    setUserToken(userToken);
    const session = await createSession(userToken);
    setSession(session.session_id, session.token.access_token, session.name);
    router.push("/chat");
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      if (mode === "register") {
        const result = await registerUser(email, password, username || undefined);
        await afterAuth(result.token.access_token);
      } else {
        const token = await loginUser(email, password);
        await afterAuth(token.access_token);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-2xl border border-slate-800 bg-slate-900 p-8 shadow-xl">
        <h1 className="mb-1 text-xl font-semibold text-slate-50">Coding Helper</h1>
        <p className="mb-6 text-sm text-slate-400">
          {mode === "login" ? "Sign in to continue." : "Create an account to get started."}
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">Email</label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500"
              placeholder="you@example.com"
            />
          </div>

          {mode === "register" && (
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-400">
                Display name (optional)
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500"
                placeholder="Jana"
              />
            </div>
          )}

          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">Password</label>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500"
              placeholder="••••••••"
            />
            {mode === "register" && (
              <p className="mt-1 text-xs text-slate-500">
                8+ characters, with an uppercase letter, lowercase letter, number, and symbol.
              </p>
            )}
          </div>

          {error && (
            <p className="rounded-lg border border-red-900 bg-red-950/50 px-3 py-2 text-sm text-red-300">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>

        <button
          type="button"
          onClick={() => {
            setError(null);
            setMode(mode === "login" ? "register" : "login");
          }}
          className="mt-4 w-full text-center text-xs text-slate-400 hover:text-slate-200"
        >
          {mode === "login" ? "Need an account? Register" : "Already have an account? Sign in"}
        </button>
      </div>
    </main>
  );
}

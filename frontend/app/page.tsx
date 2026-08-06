"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getSessionToken } from "@/lib/auth";

export default function Home() {
  const router = useRouter();

  useEffect(() => {
    router.replace(getSessionToken() ? "/chat" : "/login");
  }, [router]);

  // Redirect happens almost instantly, but render *something* rather than a
  // blank flash of white/black while the router navigates.
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950">
      <p className="text-sm text-slate-500">Loading…</p>
    </main>
  );
}

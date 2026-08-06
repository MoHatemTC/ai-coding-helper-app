"use client";

import { useEffect } from "react";

/**
 * Root error boundary (Next.js App Router convention). Catches any
 * uncaught exception thrown while rendering a page and shows a plain,
 * non-technical screen instead of Next's default dev overlay / stack trace,
 * or a blank white screen in production.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Full error (with stack trace) goes to the console for debugging —
    // never rendered into the page itself.
    console.error("Unhandled UI error:", error);
  }, [error]);

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-4">
      <div className="w-full max-w-sm rounded-2xl border border-slate-800 bg-slate-900 p-8 text-center shadow-xl">
        <h1 className="mb-2 text-lg font-semibold text-slate-50">Something went wrong</h1>
        <p className="mb-6 text-sm text-slate-400">
          That's on us, not you. Try again, and if it keeps happening let us know.
        </p>
        <button
          onClick={reset}
          className="w-full rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-indigo-500"
        >
          Try again
        </button>
      </div>
    </main>
  );
}

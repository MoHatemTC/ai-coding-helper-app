import Link from "next/link";

/**
 * Next.js App Router convention: renders for any unmatched route instead of
 * the framework's default 404 page, so a bad/stale link never shows a
 * broken or unstyled screen.
 */
export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-4">
      <div className="w-full max-w-sm rounded-2xl border border-slate-800 bg-slate-900 p-8 text-center shadow-xl">
        <h1 className="mb-2 text-lg font-semibold text-slate-50">Page not found</h1>
        <p className="mb-6 text-sm text-slate-400">
          That page doesn't exist. Head back to the chat.
        </p>
        <Link
          href="/"
          className="inline-block w-full rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-indigo-500"
        >
          Go home
        </Link>
      </div>
    </main>
  );
}

"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  getMessages,
  clearMessages,
  streamChat,
  type Message,
} from "@/lib/api";
import { getSessionToken, getSessionName, clearAuth } from "@/lib/auth";

// Matches ChatRequest.code max_length in app/schemas/chat.py — keep in sync.
const MAX_UPLOAD_BYTES = 20000;

const EXTENSION_LANGUAGE_MAP: Record<string, string> = {
  py: "python",
  js: "javascript",
  jsx: "javascript",
  ts: "typescript",
  tsx: "typescript",
  java: "java",
  c: "c",
  h: "c",
  cpp: "cpp",
  cc: "cpp",
  cxx: "cpp",
  hpp: "cpp",
  cs: "csharp",
  go: "go",
  rb: "ruby",
  rs: "rust",
  php: "php",
  swift: "swift",
  kt: "kotlin",
  kts: "kotlin",
  sql: "sql",
  sh: "bash",
  html: "html",
  css: "css",
  json: "json",
  yml: "yaml",
  yaml: "yaml",
};

function guessLanguage(filename: string): string {
  const ext = filename.split(".").pop()?.toLowerCase() ?? "";
  return EXTENSION_LANGUAGE_MAP[ext] ?? "";
}

export default function ChatPage() {
  const router = useRouter();
  const [sessionToken, setSessionToken] = useState<string | null>(null);
  const [sessionName, setSessionName] = useState<string>("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [code, setCode] = useState("");
  const [language, setLanguage] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [showCode, setShowCode] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [isDragging, setIsDragging] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const token = getSessionToken();
    if (!token) {
      router.replace("/login");
      return;
    }
    setSessionToken(token);
    setSessionName(getSessionName() || "New session");

    getMessages(token)
      .then(setMessages)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load history"))
      .finally(() => setLoadingHistory(false));
  }, [router]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!sessionToken || !input.trim() || isStreaming) return;

    setError(null);
    const userMessage: Message = { role: "user", content: input.trim() };
    const nextMessages = [...messages, userMessage];
    setMessages([...nextMessages, { role: "assistant", content: "" }]);
    setInput("");
    setIsStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      let assembled = "";
      for await (const chunk of streamChat(
        sessionToken,
        nextMessages,
        code.trim() || null,
        language.trim() || null,
        controller.signal
      )) {
        if (chunk.content) {
          assembled += chunk.content;
          setMessages((prev) => {
            const copy = [...prev];
            copy[copy.length - 1] = { role: "assistant", content: assembled };
            return copy;
          });
        }
        if (chunk.done) break;
      }
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        setError(err instanceof Error ? err.message : "Something went wrong while streaming");
      }
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  }

  function handleStop() {
    abortRef.current?.abort();
  }

  async function handleClear() {
    if (!sessionToken) return;
    try {
      await clearMessages(sessionToken);
      setMessages([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to clear chat");
    }
  }

  function handleLogout() {
    clearAuth();
    router.push("/login");
  }

  async function loadFile(file: File) {
    setError(null);
    if (file.size > MAX_UPLOAD_BYTES) {
      setError(
        `"${file.name}" is too large (${Math.round(file.size / 1000)}KB). Files must be under ${
          MAX_UPLOAD_BYTES / 1000
        }KB — paste just the relevant section instead.`
      );
      return;
    }
    try {
      const text = await file.text();
      setCode(text);
      setLanguage(guessLanguage(file.name));
      setFileName(file.name);
      setShowCode(true);
    } catch {
      setError(`Couldn't read "${file.name}" — make sure it's a plain text source file.`);
    }
  }

  function handleFileInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) void loadFile(file);
    e.target.value = ""; // allow re-selecting the same file later
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) void loadFile(file);
  }

  function clearAttachedFile() {
    setFileName(null);
    setCode("");
    setLanguage("");
  }

  return (
    <main className="flex h-screen flex-col bg-slate-950">
      <header className="flex items-center justify-between border-b border-slate-800 px-6 py-4">
        <div>
          <h1 className="text-sm font-semibold text-slate-100">Coding Helper</h1>
          <p className="text-xs text-slate-500">{sessionName}</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleClear}
            className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
          >
            Clear chat
          </button>
          <button
            onClick={handleLogout}
            className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
          >
            Log out
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        {loadingHistory ? (
          <p className="text-sm text-slate-500">Loading your conversation…</p>
        ) : messages.length === 0 ? (
          <p className="text-sm text-slate-500">
            Ask a question or paste some code below to get started. You&apos;ll get hints, not full
            solutions.
          </p>
        ) : (
          <div className="mx-auto flex max-w-2xl flex-col gap-4">
            {messages.map((m, i) => (
              <div
                key={i}
                className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm ${
                  m.role === "user"
                    ? "ml-auto bg-indigo-600 text-white"
                    : "mr-auto bg-slate-800 text-slate-100"
                }`}
              >
                {m.content || (isStreaming && i === messages.length - 1 ? "…" : "")}
              </div>
            ))}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {error && (
        <div className="mx-6 mb-2 rounded-lg border border-red-900 bg-red-950/50 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      <form onSubmit={handleSend} className="border-t border-slate-800 px-6 py-4">
        <div className="mx-auto max-w-2xl">
          <div className="mb-2 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => setShowCode((v) => !v)}
              className="text-xs text-indigo-400 hover:text-indigo-300"
            >
              {showCode ? "Hide code field" : "+ Attach code"}
            </button>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="text-xs text-indigo-400 hover:text-indigo-300"
            >
              ⇪ Upload a file
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".py,.js,.jsx,.ts,.tsx,.java,.c,.h,.cpp,.cc,.cxx,.hpp,.cs,.go,.rb,.rs,.php,.swift,.kt,.kts,.sql,.sh,.html,.css,.json,.yml,.yaml,.txt,.md"
              onChange={handleFileInputChange}
              className="hidden"
            />
            {fileName && (
              <span className="flex items-center gap-1 rounded-full border border-slate-700 bg-slate-800 px-2 py-0.5 text-xs text-slate-300">
                {fileName}
                <button
                  type="button"
                  onClick={clearAttachedFile}
                  className="text-slate-500 hover:text-slate-200"
                  aria-label="Remove attached file"
                >
                  ×
                </button>
              </span>
            )}
          </div>

          {showCode && (
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
              className={`mb-2 flex gap-2 rounded-lg transition ${
                isDragging ? "ring-2 ring-indigo-500" : ""
              }`}
            >
              <textarea
                value={code}
                onChange={(e) => {
                  setCode(e.target.value);
                  setFileName(null);
                }}
                placeholder="Paste the code you want reviewed, or drag a file here…"
                rows={6}
                className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-xs text-slate-100 outline-none focus:border-indigo-500"
              />
              <input
                type="text"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                placeholder="language (e.g. python)"
                className="h-8 w-36 self-start rounded-lg border border-slate-700 bg-slate-900 px-2 text-xs text-slate-100 outline-none focus:border-indigo-500"
              />
            </div>
          )}

          <div className="flex gap-2">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about your code or a concept…"
              className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500"
            />
            {isStreaming ? (
              <button
                type="button"
                onClick={handleStop}
                className="rounded-lg bg-slate-700 px-4 py-2 text-sm font-medium text-white hover:bg-slate-600"
              >
                Stop
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim()}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60"
              >
                Send
              </button>
            )}
          </div>
        </div>
      </form>
    </main>
  );
}

/**
 * Typed fetch helpers for the FastAPI backend.
 *
 * Matches the real contract in app/api/v1/{auth,chatbot}.py and
 * app/schemas/{auth,chat}.py — field names here must stay in sync with those.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const API_BASE = `${API_URL}/api/v1`;

export type Message = {
  role: "user" | "assistant" | "system";
  content: string;
};

export type Token = {
  access_token: string;
  token_type: string;
  expires_at: string;
};

export type RegisterResult = {
  id: number;
  email: string;
  username: string | null;
  token: Token;
};

export type SessionResult = {
  session_id: string;
  name: string;
  token: Token;
};

/**
 * Turns a failed Response into a short, user-safe message. Never surfaces
 * raw response bodies, stack traces, or backend exception text — those are
 * logged to the console for debugging but kept out of the UI.
 */
async function parseError(res: Response): Promise<string> {
  let data: unknown;
  try {
    data = await res.json();
  } catch {
    data = undefined;
  }

  // 5xx = server-side failure. The backend's current error handlers put
  // raw exception text (str(e)) straight into `detail` for these, which
  // can include things like internal API error messages or model/config
  // names — never safe to show a user. Always fall through to the generic
  // status-based message below for 5xx, no matter what the body contains.
  //
  // 4xx is different: those are expected, intentional, user-facing
  // messages from validation/auth (e.g. "Incorrect email or password"),
  // so a short plain-string `detail` there is trusted.
  if (res.status < 500) {
    const detail = (data as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === "string" && detail.length < 300 && !/Traceback|File "/.test(detail)) {
      return detail;
    }
    const errors = (data as { errors?: unknown } | undefined)?.errors;
    if (Array.isArray(errors)) {
      const joined = errors
        .map((e) => (typeof e?.message === "string" ? e.message : null))
        .filter((m): m is string => !!m)
        .join(", ");
      if (joined) return joined;
    }
  }

  if (data !== undefined) {
    // The backend returned something we don't recognize as safe to show
    // verbatim (e.g. a validation object or an unexpected shape) — log it
    // for debugging and fall through to a generic, status-coded message.
    console.error("Unrecognized error response shape:", data);
  }

  switch (res.status) {
    case 401:
      return "Your session has expired. Please log in again.";
    case 403:
      return "You don't have permission to do that.";
    case 404:
      return "That couldn't be found.";
    case 413:
      return "That's too large to upload.";
    case 429:
      return "Too many requests — please wait a moment and try again.";
    default:
      return res.status >= 500
        ? "Something went wrong on our end. Please try again in a moment."
        : "Something went wrong. Please try again.";
  }
}

export async function registerUser(
  email: string,
  password: string,
  username?: string
): Promise<RegisterResult> {
  const res = await fetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, username: username || undefined }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function loginUser(email: string, password: string): Promise<Token> {
  const form = new URLSearchParams();
  form.set("email", email);
  form.set("password", password);
  form.set("grant_type", "password");

  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function createSession(userToken: string): Promise<SessionResult> {
  const res = await fetch(`${API_BASE}/auth/session`, {
    method: "POST",
    headers: { Authorization: `Bearer ${userToken}` },
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getMessages(sessionToken: string): Promise<Message[]> {
  const res = await fetch(`${API_BASE}/chatbot/messages`, {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  if (!res.ok) throw new Error(await parseError(res));
  const data = await res.json();
  return data.messages ?? [];
}

export async function clearMessages(sessionToken: string): Promise<void> {
  const res = await fetch(`${API_BASE}/chatbot/messages`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  if (!res.ok) throw new Error(await parseError(res));
}

/**
 * Streams a chat response chunk by chunk.
 *
 * The backend's /chatbot/chat/stream sends Server-Sent Events as
 * `data: {"content": "...", "done": false}\n\n`. We can't use the native
 * EventSource here because it only supports GET with no custom headers, and
 * this endpoint needs POST + a Bearer token — so we read the raw stream
 * ourselves and split on the SSE "\n\n" event boundary.
 */
export async function* streamChat(
  sessionToken: string,
  messages: Message[],
  code: string | null,
  language: string | null,
  signal?: AbortSignal
): AsyncGenerator<{ content: string; done: boolean }> {
  const res = await fetch(`${API_BASE}/chatbot/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${sessionToken}`,
    },
    body: JSON.stringify({
      messages,
      code: code || undefined,
      language: language || undefined,
    }),
    signal,
  });

  if (!res.ok || !res.body) {
    throw new Error(await parseError(res));
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";

    for (const rawEvent of events) {
      const line = rawEvent.trim();
      if (!line.startsWith("data:")) continue;
      const jsonStr = line.slice("data:".length).trim();
      if (!jsonStr) continue;
      try {
        const parsed = JSON.parse(jsonStr);
        yield { content: parsed.content ?? "", done: !!parsed.done };
      } catch {
        // Ignore a malformed/partial chunk rather than killing the stream.
      }
    }
  }
}

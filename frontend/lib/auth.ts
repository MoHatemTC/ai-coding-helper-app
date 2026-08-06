/**
 * Local auth/session storage helpers.
 *
 * The backend uses a two-tier token system (see app/api/v1/auth.py):
 *   1. Register or log in -> get a USER token.
 *   2. POST /auth/session with the user token -> get a SESSION token.
 *   3. All /chatbot/* endpoints require the SESSION token, not the user token.
 *
 * This is a plain Next.js app (not a Claude artifact), so localStorage is the
 * normal, correct place to keep these — they just need to survive a refresh.
 */

const USER_TOKEN_KEY = "ach_user_token";
const SESSION_TOKEN_KEY = "ach_session_token";
const SESSION_ID_KEY = "ach_session_id";
const SESSION_NAME_KEY = "ach_session_name";

function isBrowser() {
  return typeof window !== "undefined";
}

export function setUserToken(token: string) {
  if (isBrowser()) localStorage.setItem(USER_TOKEN_KEY, token);
}

export function getUserToken(): string | null {
  return isBrowser() ? localStorage.getItem(USER_TOKEN_KEY) : null;
}

export function setSession(sessionId: string, token: string, name: string) {
  if (!isBrowser()) return;
  localStorage.setItem(SESSION_ID_KEY, sessionId);
  localStorage.setItem(SESSION_TOKEN_KEY, token);
  localStorage.setItem(SESSION_NAME_KEY, name);
}

export function getSessionToken(): string | null {
  return isBrowser() ? localStorage.getItem(SESSION_TOKEN_KEY) : null;
}

export function getSessionId(): string | null {
  return isBrowser() ? localStorage.getItem(SESSION_ID_KEY) : null;
}

export function getSessionName(): string | null {
  return isBrowser() ? localStorage.getItem(SESSION_NAME_KEY) : null;
}

export function clearAuth() {
  if (!isBrowser()) return;
  localStorage.removeItem(USER_TOKEN_KEY);
  localStorage.removeItem(SESSION_TOKEN_KEY);
  localStorage.removeItem(SESSION_ID_KEY);
  localStorage.removeItem(SESSION_NAME_KEY);
}

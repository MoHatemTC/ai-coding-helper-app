import { HttpClient, HttpContext } from '@angular/common/http';
import { Injectable, WritableSignal, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { environment } from '../../../environments/environment';
import { USE_USER_TOKEN } from '../http/token-context';
import type { SessionResponse } from '../models/api';
import { ACTIVE_SESSION_KEY } from './auth.service';

const SESSIONS_KEY = 'ai_sessions';

interface PersistedSessions {
  sessions: SessionResponse[];
}

@Injectable({ providedIn: 'root' })
export class SessionService {
  private readonly apiUrl = environment.apiUrl;
  private readonly sessions = signal<SessionResponse[]>(this.readPersisted());
  private readonly activeId = signal<string | null>(localStorage.getItem(ACTIVE_SESSION_KEY));
  readonly loading = signal(false);

  readonly activeSession: WritableSignal<SessionResponse | null> = signal(
    this.readPersisted().find((session) => session.session_id === this.activeId()) ?? null,
  );

  private readonly http = inject(HttpClient);

  get list(): SessionResponse[] {
    return this.sessions();
  }

  get activeToken(): string | null {
    const active = this.activeSession();
    if (!active) return null;
    const expiresAt = new Date(active.token.expires_at).getTime();
    return expiresAt > Date.now() ? active.token.access_token : null;
  }

  /**
   * Load the user's sessions and select one. Creates a fresh session when the
   * user has none yet. Call once after login/register and on app boot.
   */
  async initialize(): Promise<SessionResponse> {
    this.loading.set(true);
    try {
      const sessions = await this.fetchSessions();
      this.setSessions(sessions);

      let active = sessions.find((session) => session.session_id === this.activeId()) ?? sessions[0];
      if (!active) {
        active = await this.create();
      }
      this.select(active.session_id);
      return active;
    } finally {
      this.loading.set(false);
    }
  }

  async refresh(): Promise<SessionResponse[]> {
    const sessions = await this.fetchSessions();
    this.setSessions(sessions);
    const active = this.activeSession();
    if (active) {
      const refreshed = sessions.find((session) => session.session_id === active.session_id);
      if (refreshed) {
        this.activeSession.set(refreshed);
        this.activeId.set(refreshed.session_id);
        this.persist();
      }
    }
    return sessions;
  }

  select(sessionId: string): void {
    const found = this.sessions().find((session) => session.session_id === sessionId);
    if (!found) return;
    this.activeSession.set(found);
    this.activeId.set(found.session_id);
    localStorage.setItem(ACTIVE_SESSION_KEY, found.session_id);
  }

  async create(): Promise<SessionResponse> {
    const session = await firstValueFrom(
      this.http.post<SessionResponse>(`${this.apiUrl}/auth/session`, null, {
        context: new HttpContext().set(USE_USER_TOKEN, true),
      }),
    );
    this.setSessions([session, ...this.sessions()]);
    this.select(session.session_id);
    return session;
  }

  async rename(sessionId: string, name: string): Promise<SessionResponse> {
    const body = new FormData();
    body.append('name', name);
    const updated = await firstValueFrom(
      this.http.patch<SessionResponse>(`${this.apiUrl}/auth/session/${sessionId}/name`, body),
    );
    this.setSessions(this.sessions().map((session) => (session.session_id === sessionId ? updated : session)));
    if (this.activeSession()?.session_id === sessionId) {
      this.activeSession.set(updated);
      this.persist();
    }
    return updated;
  }

  async delete(sessionId: string): Promise<void> {
    await firstValueFrom(this.http.delete<void>(`${this.apiUrl}/auth/session/${sessionId}`));
    const remaining = this.sessions().filter((session) => session.session_id !== sessionId);
    this.setSessions(remaining);

    if (this.activeSession()?.session_id === sessionId) {
      const next = remaining[0] ?? null;
      if (next) {
        this.select(next.session_id);
      } else {
        this.activeSession.set(null);
        this.activeId.set(null);
        localStorage.removeItem(ACTIVE_SESSION_KEY);
      }
    }
  }

  private async fetchSessions(): Promise<SessionResponse[]> {
    return firstValueFrom(
      this.http.get<SessionResponse[]>(`${this.apiUrl}/auth/sessions`, {
        context: new HttpContext().set(USE_USER_TOKEN, true),
      }),
    );
  }

  private setSessions(sessions: SessionResponse[]): void {
    this.sessions.set(sessions);
    this.persist();
  }

  private persist(): void {
    const payload: PersistedSessions = { sessions: this.sessions() };
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(payload));
  }

  private readPersisted(): SessionResponse[] {
    try {
      const raw = localStorage.getItem(SESSIONS_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw) as PersistedSessions;
      return Array.isArray(parsed.sessions) ? parsed.sessions : [];
    } catch {
      return [];
    }
  }
}

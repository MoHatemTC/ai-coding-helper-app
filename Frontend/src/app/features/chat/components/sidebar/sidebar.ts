import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';

import { AuthService } from '../../../../core/services/auth.service';
import { SessionService } from '../../../../core/services/session.service';
import { ChatUiService } from '../../chat-ui.service';

const PINNED_SESSIONS_KEY = 'ai_pinned_sessions';

@Component({
  selector: 'app-sidebar',
  imports: [FormsModule, MatButtonModule, MatIconModule, MatMenuModule, MatProgressSpinnerModule, MatTooltipModule],
  templateUrl: './sidebar.html',
  styleUrl: './sidebar.scss',
})
export class Sidebar {
  private readonly sessions = inject(SessionService);
  private readonly auth = inject(AuthService);
  private readonly ui = inject(ChatUiService);

  readonly list = computed(() => this.sessions.list);
  readonly activeId = computed(() => this.sessions.activeSession()?.session_id ?? null);
  readonly creating = signal(false);
  readonly editingId = signal<string | null>(null);
  readonly editName = signal('');

  // Pinning is a frontend-only convenience — the backend has no concept of a
  // "pinned" session, so the set of pinned session ids just lives in
  // localStorage (same pattern SessionService already uses for caching the
  // session list and remembering the active session).
  private readonly pinnedIds = signal<Set<string>>(this.readPinned());

  readonly searchQuery = signal('');

  readonly filteredList = computed(() => {
    const q = this.searchQuery().trim().toLowerCase();
    if (!q) return this.list();
    return this.list().filter((session) => (session.name || 'New chat').toLowerCase().includes(q));
  });

  readonly pinnedSessions = computed(() =>
    this.filteredList().filter((session) => this.pinnedIds().has(session.session_id)),
  );

  readonly recentSessions = computed(() =>
    this.filteredList().filter((session) => !this.pinnedIds().has(session.session_id)),
  );

  readonly hasAnySessions = computed(() => this.list().length > 0);
  readonly searchHasNoMatches = computed(
    () => this.searchQuery().trim().length > 0 && this.filteredList().length === 0,
  );

  readonly userLabel = computed(() => {
    const name = this.auth.username();
    const email = this.auth.email();
    return name || email || 'Signed in';
  });

  readonly userInitial = computed(() => {
    const label = this.userLabel();
    return label ? label.trim().charAt(0).toUpperCase() : '?';
  });

  isPinned(sessionId: string): boolean {
    return this.pinnedIds().has(sessionId);
  }

  togglePin(sessionId: string): void {
    const next = new Set(this.pinnedIds());
    if (next.has(sessionId)) {
      next.delete(sessionId);
    } else {
      next.add(sessionId);
    }
    this.pinnedIds.set(next);
    this.persistPinned(next);
  }

  select(sessionId: string): void {
    this.sessions.select(sessionId);
    this.ui.closeSidebar();
  }

  async newChat(): Promise<void> {
    if (this.creating()) return;
    this.creating.set(true);
    try {
      await this.sessions.create();
      this.ui.closeSidebar();
    } finally {
      this.creating.set(false);
    }
  }

  startRename(sessionId: string, currentName: string): void {
    this.editingId.set(sessionId);
    this.editName.set(currentName);
  }

  async saveRename(sessionId: string): Promise<void> {
    const name = this.editName().trim();
    if (name && name !== this.list().find((s) => s.session_id === sessionId)?.name) {
      try {
        await this.sessions.rename(sessionId, name);
      } finally {
        this.editingId.set(null);
      }
    } else {
      this.editingId.set(null);
    }
  }

  cancelRename(): void {
    this.editingId.set(null);
  }

  async deleteSession(sessionId: string): Promise<void> {
    const session = this.list().find((s) => s.session_id === sessionId);
    const confirmed = window.confirm(`Delete "${session?.name || 'this chat'}"? This cannot be undone.`);
    if (!confirmed) return;
    try {
      await this.sessions.delete(sessionId);
      const next = new Set(this.pinnedIds());
      if (next.delete(sessionId)) {
        this.pinnedIds.set(next);
        this.persistPinned(next);
      }
    } catch {
      // error toast is shown by the interceptor
    }
  }

  logout(): void {
    this.auth.logout();
    window.location.assign('/auth');
  }

  private persistPinned(ids: Set<string>): void {
    localStorage.setItem(PINNED_SESSIONS_KEY, JSON.stringify(Array.from(ids)));
  }

  private readPinned(): Set<string> {
    try {
      const raw = localStorage.getItem(PINNED_SESSIONS_KEY);
      if (!raw) return new Set();
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? new Set(parsed) : new Set();
    } catch {
      return new Set();
    }
  }
}

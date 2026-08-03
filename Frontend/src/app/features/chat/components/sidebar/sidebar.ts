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

  readonly userLabel = computed(() => {
    const name = this.auth.username();
    const email = this.auth.email();
    return name || email || 'Signed in';
  });

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
    } catch {
      // error toast is shown by the interceptor
    }
  }

  logout(): void {
    this.auth.logout();
    window.location.assign('/auth');
  }
}

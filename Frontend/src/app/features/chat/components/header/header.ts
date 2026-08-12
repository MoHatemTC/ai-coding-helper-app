import { Component, computed, inject, input } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';

import { ChatService, ChatMode } from '../../../../core/services/chat.service';
import { SessionService } from '../../../../core/services/session.service';
import { ChatUiService } from '../../chat-ui.service';

@Component({
  selector: 'app-chat-header',
  imports: [MatButtonModule, MatButtonToggleModule, MatIconModule, MatTooltipModule],
  templateUrl: './header.html',
  styleUrl: './header.scss',
})
export class ChatHeader {
  readonly sessionId = input.required<string>();

  private readonly sessions = inject(SessionService);
  private readonly chat = inject(ChatService);
  private readonly ui = inject(ChatUiService);

  readonly title = computed(() => this.sessions.activeSession()?.name || 'New chat');
  readonly mode = this.chat.mode;

  openSidebar(): void {
    this.ui.openSidebar();
  }

  setMode(mode: ChatMode): void {
    this.chat.setMode(mode);
  }

  async clearChat(): Promise<void> {
    const confirmed = window.confirm('Clear the entire conversation history for this chat?');
    if (!confirmed) return;
    await this.chat.clearHistory();
  }
}

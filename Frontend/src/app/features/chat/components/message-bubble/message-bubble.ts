import { Component, computed, inject, input, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MarkdownComponent } from 'ngx-markdown';

import { ChatService, ViewMessage } from '../../../../core/services/chat.service';
import type { FileAttachment } from '../../../../core/models/api';

@Component({
  selector: 'app-message-bubble',
  imports: [MatButtonModule, MatIconModule, MatTooltipModule, MarkdownComponent],
  templateUrl: './message-bubble.html',
  styleUrl: './message-bubble.scss',
})
export class MessageBubble {
  readonly message = input.required<ViewMessage>();
  readonly copied = signal(false);

  private readonly chat = inject(ChatService);

  readonly isUser = computed(() => this.message().role === 'user');

  async copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(this.message().content);
      this.copied.set(true);
      window.setTimeout(() => this.copied.set(false), 1500);
    } catch {
      // clipboard unavailable — ignore
    }
  }

  async download(file: FileAttachment): Promise<void> {
    if (!file.stored_path) return;
    await this.chat.downloadFile(file);
  }
}

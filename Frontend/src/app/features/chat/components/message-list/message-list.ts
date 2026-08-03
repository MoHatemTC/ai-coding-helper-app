import { Component, ElementRef, effect, inject, viewChild } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';

import { ChatService } from '../../../../core/services/chat.service';
import { MessageBubble } from '../message-bubble/message-bubble';

@Component({
  selector: 'app-message-list',
  imports: [MatButtonModule, MatIconModule, MatProgressSpinnerModule, MessageBubble],
  templateUrl: './message-list.html',
  styleUrl: './message-list.scss',
})
export class MessageList {
  private readonly chat = inject(ChatService);
  private readonly scrollEl = viewChild<ElementRef<HTMLElement>>('scroll');

  readonly messages = this.chat.messages;
  readonly busy = this.chat.busy;
  readonly hasMore = this.chat.hasMore;
  readonly loadingHistory = this.chat.loadingHistory;

  private stickToBottom = true;
  private lastCount = 0;

  constructor() {
    effect(() => {
      const count = this.chat.messages().length;
      if (count !== this.lastCount) {
        this.lastCount = count;
        this.scrollToBottom();
      }
    });

    effect(() => {
      const messages = this.chat.messages();
      const last = messages[messages.length - 1];
      if (last?.streaming) {
        void last.content;
        if (this.stickToBottom) {
          this.scrollToBottom();
        }
      }
    });
  }

  onScroll(): void {
    const el = this.scrollEl();
    if (!el) return;
    const distance = el.nativeElement.scrollHeight - el.nativeElement.scrollTop - el.nativeElement.clientHeight;
    this.stickToBottom = distance < 48;
  }

  loadEarlier(): void {
    this.stickToBottom = false;
    void this.chat.loadEarlier();
  }

  private scrollToBottom(): void {
    requestAnimationFrame(() => {
      const el = this.scrollEl();
      if (el) {
        el.nativeElement.scrollTop = el.nativeElement.scrollHeight;
      }
    });
  }
}

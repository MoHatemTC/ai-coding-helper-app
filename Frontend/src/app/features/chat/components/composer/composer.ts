import { Component, ElementRef, computed, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';

import { ChatService } from '../../../../core/services/chat.service';
import type { AgentMode } from '../../../../core/models/api';

@Component({
  selector: 'app-composer',
  imports: [FormsModule, MatButtonModule, MatIconModule, MatTooltipModule],
  templateUrl: './composer.html',
  styleUrl: './composer.scss',
})
export class Composer {
  private readonly chat = inject(ChatService);
  private readonly textarea = viewChild<ElementRef<HTMLTextAreaElement>>('inputEl');

  readonly busy = this.chat.busy;
  readonly input = signal('');
  readonly files = signal<File[]>([]);
  readonly fileInput = viewChild<ElementRef<HTMLInputElement>>('fileInput');
  readonly agentMode = this.chat.agentMode;

  readonly canSend = computed(
    () => (this.input().trim().length > 0 || this.files().length > 0) && !this.busy(),
  );

  onModelChange(value: string): void {
    this.input.set(value);
    this.autoGrow();
  }

  onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void this.send();
    }
  }

  pickFiles(): void {
    this.fileInput()?.nativeElement.click();
  }

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const selected = Array.from(input.files ?? []);
    if (selected.length) {
      this.files.update((list) => [...list, ...selected]);
    }
    input.value = '';
  }

  removeFile(index: number): void {
    this.files.update((list) => list.filter((_, i) => i !== index));
  }

  setAgentMode(mode: AgentMode): void {
    this.chat.setAgentMode(mode);
  }

  async send(): Promise<void> {
    const text = this.input().trim();
    const files = this.files();
    if ((!text && files.length === 0) || this.busy()) return;

    this.input.set('');
    this.files.set([]);
    this.resetHeight();
    await this.chat.send(text, files);
  }

  async stop(): Promise<void> {
    await this.chat.stop();
  }

  private autoGrow(): void {
    const el = this.textarea();
    if (!el) return;
    el.nativeElement.style.height = 'auto';
    el.nativeElement.style.height = Math.min(el.nativeElement.scrollHeight, 200) + 'px';
  }

  private resetHeight(): void {
    const el = this.textarea();
    if (el) {
      el.nativeElement.style.height = 'auto';
    }
  }
}

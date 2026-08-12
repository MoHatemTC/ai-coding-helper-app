import { Component, computed, inject, input, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MarkdownComponent } from 'ngx-markdown';

import { ChatService, ViewMessage } from '../../../../core/services/chat.service';
import type { AgentActivity, FileAttachment } from '../../../../core/models/api';

const ACTIVITY_META: Record<string, { icon: string; label: string }> = {
  processing_prompt: { icon: 'psychology', label: 'Processing your prompt…' },
  processing_files: { icon: 'description', label: 'Processing your files…' },
  searching_code: { icon: 'search', label: 'Retrieve relevant code…' },
  thinking: { icon: 'auto_awesome', label: 'Thinking…' },
  using_tool: { icon: 'build', label: 'Using a tool…' },
  asking_user: { icon: 'help', label: 'Asking you a question…' },
};

const FALLBACK_ACTIVITY: { icon: string; label: string } = { icon: 'hub', label: 'Working…' };

const TOOL_NAME_PREFIXES = ['coding_helper'];

function formatToolName(name: string): string {
  let cleaned = name;
  for (const prefix of TOOL_NAME_PREFIXES) {
    cleaned = cleaned.replace(new RegExp(`^${prefix}_`), '');
  }
  return cleaned
    .split('_')
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

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
  readonly activities = computed(() => this.message().activities ?? []);
  readonly currentActivity = computed(() => {
    const acts = this.message().activities;
    return acts && acts.length ? acts[acts.length - 1] : null;
  });

  activityView(activity: AgentActivity): { icon: string; label: string } {
    const meta = ACTIVITY_META[activity.status];
    if (!meta) {
      return FALLBACK_ACTIVITY;
    }
    if (activity.status === 'using_tool' && activity.tool_name) {
      return { icon: meta.icon, label: `Using ${formatToolName(activity.tool_name)}…` };
    }
    return meta;
  }

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

import { HttpClient } from '@angular/common/http';
import { Injectable, effect, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { environment } from '../../../environments/environment';
import type {
  AgentActivity,
  AgentMode,
  ChatMessage,
  ChatResponse,
  FileAttachment,
  PaginatedChatResponse,
} from '../models/api';
import { SessionService } from './session.service';
import { StreamService } from './stream.service';

export interface ViewMessage extends ChatMessage {
  streaming?: boolean;
  error?: boolean;
  pending?: boolean;
  activities?: AgentActivity[];
}

export type ChatMode = 'normal' | 'stream';

const MODE_KEY = 'ai_chat_mode';
const AGENT_MODE_KEY = 'ai_agent_mode';

/**
 * The backend augments user messages with a trailing "Uploaded files:" summary
 * (for attachments) or a "Repo {owner}/{repo} files:" listing (for ingested
 * GitHub repos). Strip these before rendering; attachments are shown as chips
 * and repo files are not shown at all.
 */
export function stripUploadedFilesSuffix(content: string): string {
  const match = content.match(
    /\n\n(?:Uploaded files:|Repo [\w.-]+\/[\w.-]+ files:)\n(?: {2}- .*?(?:\n|$))+$/,
  );
  return match ? content.slice(0, match.index) : content;
}

@Injectable({ providedIn: 'root' })
export class ChatService {
  private readonly http = inject(HttpClient);
  private readonly session = inject(SessionService);
  private readonly stream = inject(StreamService);
  private readonly apiUrl = environment.apiUrl;

  private readonly modeSignal = signal<ChatMode>(
    localStorage.getItem(MODE_KEY) === 'stream' ? 'stream' : 'normal',
  );
  readonly mode = this.modeSignal.asReadonly();

  private readonly agentModeSignal = signal<AgentMode>(
    localStorage.getItem(AGENT_MODE_KEY) === 'fast' ? 'fast' : 'reasoning',
  );
  readonly agentMode = this.agentModeSignal.asReadonly();

  readonly messages = signal<ViewMessage[]>([]);
  readonly busy = signal(false);
  readonly hasMore = signal(false);
  readonly nextCursor = signal<string | null>(null);
  readonly loadingHistory = signal(false);

  private abortController: AbortController | null = null;
  private loadedSessionId: string | null = null;
  private stoppedByUser = false;

  constructor() {
    effect(() => {
      const sessionId = this.session.activeSession()?.session_id ?? null;
      if (sessionId && sessionId !== this.loadedSessionId) {
        this.loadedSessionId = sessionId;
        void this.loadMessages();
      }
    });
  }

  setMode(mode: ChatMode): void {
    this.modeSignal.set(mode);
    localStorage.setItem(MODE_KEY, mode);
  }

  setAgentMode(mode: AgentMode): void {
    this.agentModeSignal.set(mode);
    localStorage.setItem(AGENT_MODE_KEY, mode);
  }

  async loadMessages(): Promise<void> {
    const session = this.session.activeSession();
    if (!session) return;

    this.loadingHistory.set(true);
    try {
      const response = await firstValueFrom(
        this.http.get<PaginatedChatResponse>(
          `${this.apiUrl}/chatbot/messages`,
          { params: { limit: String(environment.messagePageSize) } },
        ),
      );
      this.messages.set(this.toChronological(response.messages));
      this.hasMore.set(response.has_more);
      this.nextCursor.set(response.next_cursor);
    } finally {
      this.loadingHistory.set(false);
    }
  }

  async loadEarlier(): Promise<void> {
    const session = this.session.activeSession();
    const cursor = this.nextCursor();
    if (!session || !cursor || this.loadingHistory()) return;

    this.loadingHistory.set(true);
    try {
      const response = await firstValueFrom(
        this.http.get<PaginatedChatResponse>(`${this.apiUrl}/chatbot/messages`, {
          params: { limit: String(environment.messagePageSize), after: cursor },
        }),
      );
      const older = this.toChronological(response.messages);
      this.messages.update((current) => [...older, ...current]);
      this.hasMore.set(response.has_more);
      this.nextCursor.set(response.next_cursor);
    } finally {
      this.loadingHistory.set(false);
    }
  }

  async send(text: string, files: File[]): Promise<void> {
    const session = this.session.activeSession();
    if (!session || this.busy()) return;

    const trimmed = text.trim();
    if (!trimmed && files.length === 0) return;

    this.stoppedByUser = false;

    const optimistic: ViewMessage = {
      role: 'user',
      content: trimmed || '(file upload)',
      files: files.map((file) => ({
        file_id: '',
        original_name: file.name,
        stored_path: '',
        language: '',
      })),
      pending: true,
    };
    this.messages.update((list) => {
      const withoutStaleStream = list.filter((message) => !message.streaming);
      return [...withoutStaleStream, optimistic];
    });
    this.busy.set(true);

    try {
      if (this.modeSignal() === 'normal') {
        await this.sendNormal(trimmed, files);
      } else {
        await this.sendStream(trimmed, files);
      }
    } finally {
      this.busy.set(false);
      this.abortController = null;
    }
  }

  async stop(): Promise<void> {
    this.stoppedByUser = true;
    this.abortController?.abort();
    this.abortController = null;
    this.messages.update((list) => list.filter((message) => !message.streaming));
    this.busy.set(false);
    try {
      await this.loadMessages();
    } catch {
      // ignore reload failures on stop
    }
  }

  async clearHistory(): Promise<void> {
    const session = this.session.activeSession();
    if (!session) return;
    await firstValueFrom(this.http.delete<void>(`${this.apiUrl}/chatbot/messages`));
    this.messages.set([]);
    this.hasMore.set(false);
    this.nextCursor.set(null);
  }

  async downloadFile(file: FileAttachment): Promise<void> {
    const blob = await firstValueFrom(
      this.http.get(`${this.apiUrl}/chatbot/file/`, {
        params: { url: file.stored_path },
        responseType: 'blob',
      }),
    );
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = file.original_name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
  }

  private async sendNormal(text: string, files: File[]): Promise<void> {
    const body = new FormData();
    body.append('message', text);
    body.append('mode', this.agentModeSignal());
    for (const file of files) {
      body.append('files', file, file.name);
    }

    let response: ChatResponse;
    try {
      response = await firstValueFrom(this.http.post<ChatResponse>(`${this.apiUrl}/chatbot/chat`, body));
    } catch (err) {
      this.markLastUserMessageError();
      throw err;
    }

    const assistant = response.messages.find((message) => message.role === 'assistant');
    if (assistant) {
      this.messages.update((list) => [...list, { ...assistant }]);
    }

    try {
      await this.loadMessages();
    } catch {
      // keep the optimistic + assistant messages if history reload fails
    }
  }

  private async sendStream(text: string, files: File[]): Promise<void> {
    const session = this.session.activeSession();
    if (!session) return;

    const streamingMessage: ViewMessage = { role: 'assistant', content: '', streaming: true };
    this.messages.update((list) => [...list, streamingMessage]);

    this.abortController = new AbortController();

    await this.stream.post(session.session_id, text, files, {
      signal: this.abortController.signal,
      mode: this.agentModeSignal(),
      onChunk: (content) => {
        this.messages.update((list) => {
          const last = list[list.length - 1];
          if (last?.streaming) {
            return [...list.slice(0, -1), { ...last, content: last.content + content }];
          }
          return list;
        });
      },
      onStatus: (activity) => {
        this.messages.update((list) => {
          const last = list[list.length - 1];
          if (!last?.streaming) return list;
          const activities = last.activities ?? [];
          const prev = activities[activities.length - 1];
          if (prev && prev.status === activity.status && prev.tool_name === activity.tool_name) {
            return list;
          }
          return [...list.slice(0, -1), { ...last, activities: [...activities, activity] }];
        });
      },
      onDone: async () => {
        this.messages.update((list) =>
          list.map((message) => (message.streaming ? { ...message, streaming: false } : message)),
        );
        try {
          await this.loadMessages();
        } catch {
          // keep the streamed content if history reload fails
        }
      },
      onError: () => {
        if (this.stoppedByUser) {
          this.messages.update((list) => list.filter((message) => !message.streaming));
          return;
        }
        this.messages.update((list) =>
          list.map((item) => (item.streaming ? { ...item, streaming: false, error: true } : item)),
        );
      },
    });
  }

  private markLastUserMessageError(): void {
    this.messages.update((list) => {
      const index = list.length - 1;
      if (index < 0) return list;
      return [...list.slice(0, index), { ...list[index], error: true }];
    });
  }

  private toChronological(messages: ChatMessage[]): ViewMessage[] {
    return messages
      .slice()
      .reverse()
      .map((message) => ({
        ...message,
        content: stripUploadedFilesSuffix(message.content ?? ''),
      }));
  }
}

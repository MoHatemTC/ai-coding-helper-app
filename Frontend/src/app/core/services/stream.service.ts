import { Injectable, inject } from '@angular/core';

import { environment } from '../../../environments/environment';
import type { AgentMode, StreamEvent } from '../models/api';
import { SessionService } from './session.service';

export interface StreamOptions {
  signal?: AbortSignal;
  mode?: AgentMode;
  onChunk: (content: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
}

@Injectable({ providedIn: 'root' })
export class StreamService {
  private readonly session = inject(SessionService);

  async post(
    sessionId: string,
    message: string,
    files: File[],
    options: StreamOptions,
  ): Promise<void> {
    const token = this.session.activeToken;
    const url = `${environment.apiUrl}/chatbot/chat/stream`;

    const body = new FormData();
    body.append('message', message);
    body.append('mode', options.mode ?? 'reasoning');
    for (const file of files) {
      body.append('files', file, file.name);
    }

    let response: Response;
    try {
      response = await fetch(url, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body,
        signal: options.signal,
      });
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        options.onError('Stream stopped');
      } else {
        options.onError(this.extractMessage(err));
      }
      return;
    }

    if (!response.ok || !response.body) {
      options.onError(await this.errorFromResponse(response));
      return;
    }

    try {
      await this.readStream(response, options);
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        options.onError('Stream stopped');
      } else {
        options.onError(this.extractMessage(err));
      }
    }
  }

  private async readStream(response: Response, options: StreamOptions): Promise<void> {
    if (!response.body) {
      options.onError('Response stream is unavailable');
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let separator: number;
      while ((separator = buffer.indexOf('\n\n')) !== -1) {
        const rawEvent = buffer.slice(0, separator);
        buffer = buffer.slice(separator + 2);
        for (const line of rawEvent.split('\n')) {
          if (!line.startsWith('data:')) continue;
          const payload = line.slice(5).trim();
          if (!payload) continue;

          let event: StreamEvent;
          try {
            event = JSON.parse(payload) as StreamEvent;
          } catch {
            continue;
          }

          if (event.done) {
            // The backend reports stream errors as a done event with content.
            if (event.content) {
              options.onError(event.content);
            } else {
              options.onDone();
            }
            return;
          }
          options.onChunk(event.content);
        }
      }
    }
  }

  private async errorFromResponse(response: Response): Promise<string> {
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (payload.detail) {
        return typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail);
      }
    } catch {
      // fall through to status text
    }
    return `Request failed with status ${response.status}`;
  }

  private extractMessage(err: unknown): string {
    return err instanceof Error && err.message ? err.message : 'Unexpected stream error';
  }
}

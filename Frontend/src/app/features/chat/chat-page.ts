import { Component, effect, inject } from '@angular/core';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSidenavModule } from '@angular/material/sidenav';

import { ChatService } from '../../core/services/chat.service';
import { SessionService } from '../../core/services/session.service';
import { ChatUiService } from './chat-ui.service';
import { Composer } from './components/composer/composer';
import { MessageList } from './components/message-list/message-list';
import { ChatHeader } from './components/header/header';
import { Sidebar } from './components/sidebar/sidebar';

@Component({
  selector: 'app-chat-page',
  imports: [MatSidenavModule, MatProgressSpinnerModule, Sidebar, ChatHeader, MessageList, Composer],
  templateUrl: './chat-page.html',
  styleUrl: './chat-page.scss',
})
export class ChatPage {
  private readonly sessions = inject(SessionService);
  private readonly chat = inject(ChatService);
  readonly ui = inject(ChatUiService);

  readonly activeSession = this.sessions.activeSession;
  readonly sidebarOpen = this.ui.sidebarOpen;

  private wasBusy = false;

  constructor() {
    // The backend auto-names sessions after the first message; refresh the
    // session list so the sidebar/header title stays in sync once a turn ends.
    effect(() => {
      const busy = this.chat.busy();
      if (this.wasBusy && !busy) {
        void this.sessions.refresh();
      }
      this.wasBusy = busy;
    });
  }
}

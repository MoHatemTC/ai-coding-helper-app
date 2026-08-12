import { Injectable, signal } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class ChatUiService {
  readonly sidebarOpen = signal(false);

  openSidebar(): void {
    this.sidebarOpen.set(true);
  }

  closeSidebar(): void {
    this.sidebarOpen.set(false);
  }
}

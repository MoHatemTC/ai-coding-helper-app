import { Injectable, inject } from '@angular/core';
import { MatSnackBar } from '@angular/material/snack-bar';

@Injectable({ providedIn: 'root' })
export class ToastService {
  private readonly snackBar = inject(MatSnackBar);

  success(message: string): void {
    this.snackBar.open(message, 'Close', { duration: 3000, panelClass: ['app-toast-success'] });
  }

  error(message: string): void {
    this.snackBar.open(message, 'Close', { duration: 5000, panelClass: ['app-toast-error'] });
  }

  info(message: string): void {
    this.snackBar.open(message, 'Close', { duration: 3000, panelClass: ['app-toast-info'] });
  }
}

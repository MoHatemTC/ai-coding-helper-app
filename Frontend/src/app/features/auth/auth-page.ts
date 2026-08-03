import { Component, computed, inject, signal } from '@angular/core';
import {
  FormControl,
  FormGroup,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { HttpErrorResponse } from '@angular/common/http';
import { Router } from '@angular/router';

import { AuthService } from '../../core/services/auth.service';
import { SessionService } from '../../core/services/session.service';

@Component({
  selector: 'app-auth-page',
  imports: [
    ReactiveFormsModule,
    MatButtonModule,
    MatButtonToggleModule,
    MatIconModule,
    MatInputModule,
    MatProgressSpinnerModule,
  ],
  templateUrl: './auth-page.html',
  styleUrl: './auth-page.scss',
})
export class AuthPage {
  private readonly auth = inject(AuthService);
  private readonly sessions = inject(SessionService);
  private readonly router = inject(Router);

  readonly mode = signal<'login' | 'register'>('login');
  readonly submitting = signal(false);
  readonly errorMessage = signal<string | null>(null);
  readonly showPassword = signal(false);
  readonly showConfirm = signal(false);

  readonly isLogin = computed(() => this.mode() === 'login');

  readonly loginForm = new FormGroup({
    email: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.email] }),
    password: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
  });

  readonly registerForm = new FormGroup({
    username: new FormControl('', { nonNullable: true }),
    email: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.email] }),
    password: new FormControl('', {
      nonNullable: true,
      validators: [Validators.required, Validators.minLength(8)],
    }),
    confirm: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
  });

  switchMode(mode: 'login' | 'register'): void {
    this.mode.set(mode);
    this.errorMessage.set(null);
    this.showPassword.set(false);
    this.showConfirm.set(false);
  }

  async submitLogin(): Promise<void> {
    if (this.loginForm.invalid || this.submitting()) return;
    this.errorMessage.set(null);
    this.submitting.set(true);
    try {
      const { email, password } = this.loginForm.getRawValue();
      await this.auth.login(email, password);
      await this.enterApp();
    } catch (err) {
      this.errorMessage.set(extractAuthError(err));
    } finally {
      this.submitting.set(false);
    }
  }

  async submitRegister(): Promise<void> {
    if (this.registerForm.invalid || this.submitting()) return;
    this.errorMessage.set(null);

    const { username, email, password, confirm } = this.registerForm.getRawValue();
    if (password !== confirm) {
      this.errorMessage.set('Passwords do not match');
      return;
    }

    this.submitting.set(true);
    try {
      await this.auth.register(email, password, username || null);
      await this.enterApp();
    } catch (err) {
      this.errorMessage.set(extractAuthError(err));
    } finally {
      this.submitting.set(false);
    }
  }

  private async enterApp(): Promise<void> {
    await this.sessions.initialize();
    await this.router.navigate(['/chat']);
  }
}

function extractAuthError(err: unknown): string {
  if (err instanceof HttpErrorResponse) {
    const detail = err.error?.detail as unknown;
    if (typeof detail === 'string' && detail) return detail;
    if (Array.isArray(detail)) {
      const parts = detail.map((item: { msg?: string }) => item?.msg).filter(Boolean);
      if (parts.length) return parts.join('; ');
    }
    if (err.status === 401) return 'Incorrect email or password';
    if (err.status === 422) return 'Please check your details and try again';
  }
  return 'Something went wrong. Please try again.';
}

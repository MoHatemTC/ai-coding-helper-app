import { HttpClient, HttpContext } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { environment } from '../../../environments/environment';
import { NO_AUTH } from '../http/token-context';
import type { LoginResponse, UserCreate, UserResponse } from '../models/api';

const TOKEN_KEY = 'ai_user_token';
const EXPIRY_KEY = 'ai_user_token_expires_at';
const EMAIL_KEY = 'ai_user_email';
const USERNAME_KEY = 'ai_user_name';
const ID_KEY = 'ai_user_id';
export const ACTIVE_SESSION_KEY = 'ai_active_session_id';

const SESSION_STORAGE_KEYS = [ACTIVE_SESSION_KEY, 'ai_sessions'];

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly apiUrl = environment.apiUrl;
  private readonly token = signal<string | null>(localStorage.getItem(TOKEN_KEY));
  private readonly expiry = signal<number | null>(Number(localStorage.getItem(EXPIRY_KEY)) || null);
  readonly email = signal<string | null>(localStorage.getItem(EMAIL_KEY));
  readonly username = signal<string | null>(localStorage.getItem(USERNAME_KEY));
  readonly userId = signal<number | null>(Number(localStorage.getItem(ID_KEY)) || null);

  readonly isAuthenticated = computed(() => {
    const value = this.token();
    const expiresAt = this.expiry();
    return !!value && !!expiresAt && expiresAt > Date.now();
  });

  private readonly http = inject(HttpClient);

  get accessToken(): string | null {
    return this.isAuthenticated() ? this.token() : null;
  }

  async login(email: string, password: string): Promise<LoginResponse> {
    const body = new FormData();
    body.append('email', email);
    body.append('password', password);
    body.append('grant_type', 'password');

    const res = await firstValueFrom(
      this.http.post<LoginResponse>(`${this.apiUrl}/auth/login`, body, {
        context: new HttpContext().set(NO_AUTH, true),
      }),
    );
    this.persistToken(res.access_token, res.expires_at, email);
    return res;
  }

  async register(email: string, password: string, username?: string | null): Promise<UserResponse> {
    const res = await firstValueFrom(
      this.http.post<UserResponse>(
        `${this.apiUrl}/auth/register`,
        { email, password, username } satisfies UserCreate,
        { context: new HttpContext().set(NO_AUTH, true) },
      ),
    );
    this.persistUser(res);
    return res;
  }

  logout(): void {
    this.token.set(null);
    this.expiry.set(null);
    this.email.set(null);
    this.username.set(null);
    this.userId.set(null);
    for (const key of [TOKEN_KEY, EXPIRY_KEY, EMAIL_KEY, USERNAME_KEY, ID_KEY, ...SESSION_STORAGE_KEYS]) {
      localStorage.removeItem(key);
    }
  }

  private persistToken(accessToken: string, expiresAt: string, email: string): void {
    this.token.set(accessToken);
    this.expiry.set(new Date(expiresAt).getTime());
    this.email.set(email);
    localStorage.setItem(TOKEN_KEY, accessToken);
    localStorage.setItem(EXPIRY_KEY, String(new Date(expiresAt).getTime()));
    localStorage.setItem(EMAIL_KEY, email);
  }

  private persistUser(res: UserResponse): void {
    this.persistToken(res.token.access_token, res.token.expires_at, res.email);
    this.username.set(res.username ?? null);
    localStorage.setItem(USERNAME_KEY, res.username ?? '');
    localStorage.setItem(ID_KEY, String(res.id));
    this.userId.set(res.id);
  }
}

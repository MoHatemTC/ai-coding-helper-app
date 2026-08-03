import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';

import { NO_AUTH } from '../http/token-context';
import { AuthService } from '../services/auth.service';
import { ToastService } from '../services/toast.service';

export const errorInterceptor: HttpInterceptorFn = (req, next) => {
  const router = inject(Router);
  const auth = inject(AuthService);
  const toast = inject(ToastService);
  const isAuthRequest = req.context.get(NO_AUTH);

  return next(req).pipe(
    catchError((raw: unknown) => {
      if (!(raw instanceof HttpErrorResponse)) {
        return throwError(() => raw);
      }
      const error = raw as HttpErrorResponse;

      if (error.status === 401 && !isAuthRequest) {
        auth.logout();
        toast.info('Session expired — please sign in again');
        void router.navigate(['/auth']);
        return throwError(() => error);
      }

      if (!isAuthRequest) {
        toast.error(extractDetail(error));
      }

      return throwError(() => error);
    }),
  );
};

function extractDetail(error: HttpErrorResponse): string {
  const detail = error.error?.detail as unknown;
  if (typeof detail === 'string' && detail) {
    return detail;
  }
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item: { msg?: string }) => item?.msg)
      .filter((msg): msg is string => !!msg);
    if (parts.length) {
      return parts.join('; ');
    }
  }

  switch (error.status) {
    case 400:
      return 'The request was rejected';
    case 401:
      return 'Incorrect email or password';
    case 403:
      return 'You do not have permission to do that';
    case 404:
      return 'The requested resource was not found';
    case 422:
      return 'Please check your input and try again';
    case 429:
      return 'Too many requests — please slow down';
    case 500:
      return 'Something went wrong on the server';
    default:
      return 'Something went wrong';
  }
}

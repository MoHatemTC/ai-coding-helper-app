import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';

import { NO_AUTH, USE_USER_TOKEN } from '../http/token-context';
import { AuthService } from '../services/auth.service';
import { SessionService } from '../services/session.service';

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  if (req.context.get(NO_AUTH)) {
    return next(req);
  }

  const useUserToken = req.context.get(USE_USER_TOKEN);
  const auth = inject(AuthService);
  const sessions = inject(SessionService);
  const token = useUserToken ? auth.accessToken : sessions.activeToken;

  if (!token) {
    return next(req);
  }

  return next(req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }));
};

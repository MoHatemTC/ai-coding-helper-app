import { HttpContextToken } from '@angular/common/http';

/** Attach the user JWT instead of the active session JWT (session management endpoints). */
export const USE_USER_TOKEN = new HttpContextToken<boolean>(() => false);

/** Never attach an Authorization header (login / register). */
export const NO_AUTH = new HttpContextToken<boolean>(() => false);

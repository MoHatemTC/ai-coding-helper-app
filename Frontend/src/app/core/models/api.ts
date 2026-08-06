export interface Token {
  access_token: string;
  token_type: string;
  expires_at: string;
}

export interface BaseResponse {
  request_id?: string;
}

/** POST /auth/register */
export interface UserCreate {
  email: string;
  password: string;
  username?: string | null;
}

/** POST /auth/register response */
export interface UserResponse extends BaseResponse {
  id: number;
  email: string;
  username: string | null;
  token: Token;
}

/** POST /auth/login response */
export interface LoginResponse extends BaseResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
}

export interface SessionResponse extends BaseResponse {
  session_id: string;
  name: string;
  token: Token;
}

export interface FileAttachment {
  file_id: string;
  original_name: string;
  stored_path: string;
  language: string;
}

export type MessageRole = 'user' | 'assistant' | 'system';

export type AgentMode = 'reasoning' | 'fast';

export interface ChatMessage {
  role: MessageRole;
  content: string;
  files?: FileAttachment[] | null;
}

export interface ChatResponse extends BaseResponse {
  messages: ChatMessage[];
}

export interface PaginatedChatResponse extends BaseResponse {
  messages: ChatMessage[];
  has_more: boolean;
  next_cursor: string | null;
}

export interface StreamEvent extends BaseResponse {
  content: string;
  done: boolean;
}

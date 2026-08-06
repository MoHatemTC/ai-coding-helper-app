# Upload Endpoint Contract — Proposal for Haitham

**Status: proposal, not yet agreed.** No file-upload backend endpoint exists in the codebase today (confirmed by searching `app/api` and `app/schemas` — no `UploadFile` or `multipart` usage anywhere). This document exists so there's something concrete to discuss instead of starting from a blank page.

## Current state (interim, already working)

Right now the frontend reads an attached file entirely client-side (`FileReader.text()`), guesses its language from the extension, and sends the raw text inline as the existing `code` / `language` fields on `POST /api/v1/chatbot/chat` and `/chat/stream` — the same fields already used for pasted code. `ChatRequest.code` is capped at `max_length=20000` in `app/schemas/chat.py`, so the frontend enforces a matching 20,000-byte client-side limit before it will even read the file.

This works today for small text/source files, but it means: no file is ever actually stored server-side, nothing goes through real multipart upload handling, and anything larger than the 20KB text-field cap is rejected outright — including any binary file (images, PDFs, etc.).

## Proposed dedicated endpoint

```
POST /api/v1/chatbot/uploads
Authorization: Bearer <session token>
Content-Type: multipart/form-data; field name "file"
```

**Response 200:**
```json
{
  "upload_id": "string",
  "filename": "string",
  "content_type": "string",
  "size_bytes": 12345,
  "language": "python"
}
```

**Error responses:**
- `413` — file exceeds the size limit
- `415` — unsupported file type
- `401` — missing/invalid session token

The frontend would then pass `upload_id` alongside (or instead of) the existing `code` field on the chat request, and the backend resolves it to file content server-side.

## Open questions for Haitham

Whether uploads should be persisted (a table/row per upload) or handled ephemerally in-memory for the duration of one request. Whether the size limit stays at 20KB to match the existing `code` field, or increases now that it's a dedicated multipart endpoint. Which file types are actually in scope — the frontend already restricts the file picker to the same source-code extensions listed in `EXTENSION_LANGUAGE_MAP` in `frontend/app/chat/page.tsx`, but this should be confirmed rather than assumed. And whether `upload_id` fully replaces the inline `code` field on `ChatRequest`, or the two coexist during a transition period.

## Recommendation given the deadline

Given the Aug 8 deadline, the pragmatic path is: keep the current inline client-side approach as the working, demoable upload flow for this submission, and treat the dedicated endpoint above as the agreed next step once Haitham weighs in — rather than blocking the whole sprint on a new backend endpoint landing in time. This is called out explicitly as an open item in the PRD self-review.

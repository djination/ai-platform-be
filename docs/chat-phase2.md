# Phase 2 — Learner chat (tutor vs support)

## Related learner endpoints

- `GET /api/content-engine/module/` — latest published module (includes `id` / `difficulty` when material exists, else both `null`).
- `GET /api/content-engine/modules/<id>/` — one published module by primary key; `404` if missing or not published. Response includes `difficulty` (`beginner` \| `intermediate` \| `advanced`).

## Endpoint

- `POST /api/content-engine/chat/` — JWT required (`Authorization: Bearer …`).
- Throttle scope: `chat` (default `60/hour` per user via `ScopedRateThrottle`).
- Daily cap: `CHAT_DAILY_MESSAGE_LIMIT` messages per user per UTC day (cache-backed).

## Request body

| Field | Required | Notes |
| --- | --- | --- |
| `message` | Yes | Plain text; blocked patterns return `route: "blocked"` without counting toward daily cap. |
| `session_key` | No | Omit to start a new session; reuse server-returned key to continue. |
| `mode` | No | Tutor only: `general`, `correction`, `hint`, `exercise`. |
| `level` | No | Tutor hint for difficulty (e.g. `beginner`). |
| `module_context` | No | Optional excerpt from current lesson for grounding. The learner UI may include a short lesson excerpt plus a text summary of quiz questions/options from the published module. |

## Response

Returns `reply`, `session_key`, `route` (`tutor` \| `support` \| `blocked`), `intent`, `intent_confidence`, `ambiguous`, `needs_human_handoff`, and `mode` (tutor routes only).

## Personas

- **Tutor:** English-learning focus; refuses harmful/medical/legal/financial advice; concise tone. See `TUTOR_BOUNDARIES` and mode prompts in `content_engine/chat_service.py`.
- **Support:** FAQ-grounded platform help; escalation when KB confidence is low or topic is sensitive. See `CS_BOUNDARIES` and `CS_KNOWLEDGE_BASE` in the same module.

## LLM provider

If `OPENROUTER_API_KEY` is unset or empty, the backend uses deterministic **baseline** replies (no external calls). With a valid key, requests go to `OPENROUTER_BASE_URL` using `OPENROUTER_MODEL`.

## Persistence

- `ChatSession` / `ChatMessage` store conversation turns per user.
- `ChatRoutingAudit` logs classification and routing for review.

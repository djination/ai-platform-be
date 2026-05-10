"""
Phase 2 chat: tutor vs customer support routing, guardrails, optional OpenRouter.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# E1.1 — Persona & boundaries (also documented in backend/docs/chat-phase2.md)
TUTOR_BOUNDARIES = (
    "You are an English learning tutor. Stay focused on language learning. "
    "Do not provide medical, legal, or financial advice. "
    "If asked for harmful content, refuse briefly and redirect to safe learning. "
    "Keep answers concise and encouraging."
)

CS_BOUNDARIES = (
    "You are customer support for this educational platform. "
    "Answer using the provided FAQ snippets when possible. "
    "Do not invent billing details; if unsure, say you will escalate to a human. "
    "No medical, legal, or investment advice."
)

INPUT_BLOCKED_PATTERNS = [
    r"\b(kill\s+yourself|suicide|bomb\s+how|how\s+to\s+make\s+a\s+bomb)\b",
]

OUTPUT_REFUSAL_PHRASES = (
    "i cannot provide legal advice",
    "i cannot provide medical",
    "contact a qualified professional",
)

SUPPORT_KEYWORDS = (
    "refund",
    "billing",
    "payment",
    "invoice",
    "subscription",
    "account",
    "password reset",
    "login problem",
    "tagihan",
    "pembayaran",
    "berlangganan",
    "akun",
)

AMBIGUOUS_KEYWORDS = ("help", "bantuan", "support", "customer service")


def guard_input(user_message: str) -> tuple[bool, str]:
    text = (user_message or "").lower()
    for pattern in INPUT_BLOCKED_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return False, "Permintaan ini tidak dapat diproses. Silakan hubungi layanan krisis setempat jika Anda membutuhkan bantu darurat."
    return True, ""


def guard_output(assistant_text: str) -> tuple[str, bool]:
    """Returns (text, was_refused_or_sanitized)."""
    lower = (assistant_text or "").lower()
    for phrase in OUTPUT_REFUSAL_PHRASES:
        if phrase in lower:
            return assistant_text, False
    # Soft block: if model outputs prescription-like medical advice
    if re.search(r"\b(take \d+ mg|prescription|diagnose)\b", lower):
        return (
            "Saya tidak dapat memberikan saran medis. Untuk kesehatan, konsultasikan profesional medis.",
            True,
        )
    return assistant_text, False


def normalize_chat_message_for_cache(message: str) -> str:
    """Lowercase + collapse whitespace so minor spelling/casing differences hit the same cache."""
    return " ".join((message or "").strip().lower().split())


def compute_chat_reply_cache_lookup_hash(
    *,
    route: str,
    mode: str,
    level: str,
    module_context: str,
    message: str,
    ambiguous: bool,
    history_empty: bool,
) -> str | None:
    """Returns SHA-256 hex digest, or None when caching must not apply."""
    if not getattr(settings, "CHAT_REPLY_CACHE_ENABLED", True):
        return None
    if not history_empty:
        return None
    norm = normalize_chat_message_for_cache(message)
    if not norm:
        return None
    if route == "support":
        mode_k, level_k, ctx_k = "", "", ""
    else:
        mode_k = (mode or "general").strip().lower()
        level_k = (level or "beginner").strip().lower()
        ctx_k = (module_context or "").strip()
    blob = json.dumps(
        {
            "ambiguous": bool(ambiguous),
            "ctx": ctx_k,
            "level": level_k,
            "message": norm,
            "mode": mode_k,
            "route": route,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def classify_intent(user_message: str) -> dict[str, Any]:
    text = (user_message or "").lower().strip()
    support_hits = sum(1 for k in SUPPORT_KEYWORDS if k in text)
    ambiguous_hits = sum(1 for k in AMBIGUOUS_KEYWORDS if k in text)

    if support_hits >= 1 and ambiguous_hits == 0:
        return {"intent": "support", "confidence": min(0.5 + 0.15 * support_hits, 0.95), "ambiguous": False}
    if support_hits >= 1 and ambiguous_hits >= 1:
        return {"intent": "support", "confidence": 0.55, "ambiguous": True}
    if ambiguous_hits >= 1 and support_hits == 0:
        return {"intent": "tutor", "confidence": 0.45, "ambiguous": True}
    return {"intent": "tutor", "confidence": 0.75, "ambiguous": False}


# F1.2 — Minimal KB (title -> body)
CS_KNOWLEDGE_BASE: list[dict[str, str]] = [
    {
        "id": "billing-1",
        "title": "Refund policy",
        "body": "Refunds follow the policy shown at checkout. For refund requests, email support with your account email and invoice ID.",
        "keywords": "refund reimbursement money back",
    },
    {
        "id": "account-1",
        "title": "Reset password",
        "body": "Use Forgot Password on the login page. If email does not arrive, check spam or contact support with your registered email.",
        "keywords": "password reset login forgot",
    },
    {
        "id": "contact-1",
        "title": "Human support",
        "body": "Escalation email: support@example.com (replace with production contact).",
        "keywords": "human agent contact email",
    },
]


def retrieve_kb_snippets(user_message: str, limit: int = 3) -> tuple[list[dict[str, Any]], float]:
    tokens = set(re.findall(r"\w+", (user_message or "").lower()))
    scored: list[tuple[float, dict[str, str]]] = []
    for row in CS_KNOWLEDGE_BASE:
        kw = set(re.findall(r"\w+", row.get("keywords", "").lower()))
        overlap = len(tokens & kw)
        if overlap:
            scored.append((overlap / max(len(tokens), 1), row))
    scored.sort(key=lambda x: -x[0])
    top = [dict(row) for _, row in scored[:limit]]
    confidence = scored[0][0] if scored else 0.0
    return top, confidence


def needs_human_handoff(user_message: str, kb_confidence: float) -> bool:
    text = (user_message or "").lower()
    sensitive = any(x in text for x in ("lawyer", "court", "sue", "police report", "hack", "stolen card"))
    if sensitive:
        return True
    if kb_confidence < 0.08 and any(k in text for k in ("refund", "charge", "card", "invoice")):
        return True
    return False


def build_tutor_system(mode: str, level: str, module_context: str) -> str:
    mode = (mode or "general").strip().lower()
    level = (level or "beginner").strip().lower()
    ctx = (module_context or "").strip()
    ctx_line = f"\nCurrent lesson context (optional):\n{ctx[:1200]}" if ctx else ""

    mode_instructions = {
        "general": "Answer as a tutor; prefer examples and short explanations.",
        "correction": "Focus on grammar/spelling correction with brief explanations. Show improved version.",
        "hint": "Give Socratic hints only; do not reveal full answers immediately.",
        "exercise": f"Generate one short English exercise appropriate for {level} level (question + optional choices).",
    }
    instruction = mode_instructions.get(mode, mode_instructions["general"])
    return (
        f"{TUTOR_BOUNDARIES}\n{instruction}\nLearner level: {level}.{ctx_line}"
    )


def build_cs_system(kb_snippets: list[dict[str, Any]]) -> str:
    kb_block = json.dumps(kb_snippets, ensure_ascii=False, indent=2) if kb_snippets else "[]"
    return f"{CS_BOUNDARIES}\nFAQ snippets (use when relevant):\n{kb_block}"


def _baseline_tutor_reply(user_message: str, mode: str) -> str:
    mode = (mode or "general").lower()
    if mode == "correction":
        return (
            "Quick tip: check subject-verb agreement and tense consistency. "
            "Paste a shorter sentence if you want a line-by-line correction."
        )
    if mode == "hint":
        return "Think about the main verb: what action happens, and when? Try rewriting one clause at a time."
    if mode == "exercise":
        return (
            "Exercise (beginner): Complete the sentence — \"I ___ to school every day.\" "
            "(Options: go / goes / going). Reply with your choice and why."
        )
    return (
        "Let’s practice English. Try asking a specific question (grammar, vocabulary, or a sentence to check)."
    )


def _baseline_support_reply(snippets: list[dict[str, Any]], handoff: bool) -> str:
    parts = []
    for s in snippets[:2]:
        parts.append(f"• {s.get('title', '')}: {s.get('body', '')}")
    body = "\n".join(parts) if parts else "I don’t have a matching FAQ entry yet."
    if handoff:
        body += "\n\nI’ll connect you with a human agent — please email support with your account details."
    return body


def call_llm(messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
    """Returns assistant text and usage metadata."""
    api_key = getattr(settings, "OPENROUTER_API_KEY", None) or ""
    model = getattr(settings, "OPENROUTER_MODEL", "openai/gpt-4o-mini")
    base_url = getattr(settings, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions")

    if not api_key or api_key == "change-me-in-production":
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        sys0 = next((m["content"] for m in messages if m["role"] == "system"), "")
        if "customer support" in sys0.lower():
            snippets, conf = retrieve_kb_snippets(last_user)
            handoff = needs_human_handoff(last_user, conf)
            return _baseline_support_reply(snippets, handoff), {"provider": "baseline", "tokens": 0}
        mode = "general"
        if "correction" in sys0.lower():
            mode = "correction"
        elif "socratic" in sys0.lower() or "hint" in sys0.lower():
            mode = "hint"
        elif "exercise" in sys0.lower():
            mode = "exercise"
        return _baseline_tutor_reply(last_user, mode), {"provider": "baseline", "tokens": 0}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ("system", "user", "assistant")],
        "max_tokens": 800,
        "temperature": 0.6,
    }
    try:
        resp = requests.post(base_url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        msg = (choice.get("message") or {})
        content = (msg.get("content") or "").strip()
        usage = data.get("usage") or {}
        return content, {"provider": "openrouter", "usage": usage}
    except Exception as exc:
        logger.exception("OpenRouter call failed: %s", exc)
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return _baseline_tutor_reply(last_user, "general"), {"provider": "fallback_error", "error": str(exc)}


def trim_history(messages: list[dict[str, str]], max_turns: int = 8) -> list[dict[str, str]]:
    """Keep last N user/assistant pairs worth (simple: last max_turns*2 non-system)."""
    system = [m for m in messages if m["role"] == "system"]
    rest = [m for m in messages if m["role"] != "system"]
    rest = rest[-(max_turns * 2) :]
    return system + rest


def new_session_key() -> str:
    return uuid.uuid4().hex


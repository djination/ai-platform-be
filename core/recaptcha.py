"""Verify Google reCAPTCHA v2/v3 tokens (siteverify)."""

from __future__ import annotations

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

SITEVERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"


def recaptcha_is_configured() -> bool:
    return bool((getattr(settings, "RECAPTCHA_SECRET_KEY", None) or "").strip())


def verify_recaptcha_token(token: str, remote_ip: str | None = None) -> tuple[bool, str]:
    """
    Returns (ok, error_message). error_message empty when ok.
    v3: requires success and score >= RECAPTCHA_MIN_SCORE when score is present.
    v2: requires success only.
    """
    secret = (getattr(settings, "RECAPTCHA_SECRET_KEY", None) or "").strip()
    if not secret:
        return True, ""

    t = (token or "").strip()
    if not t:
        return False, "Verifikasi keamanan wajib. Muat ulang halaman dan coba lagi."

    data = {"secret": secret, "response": t}
    if remote_ip:
        data["remoteip"] = remote_ip

    try:
        resp = requests.post(SITEVERIFY_URL, data=data, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        logger.warning("reCAPTCHA siteverify request failed: %s", exc)
        return False, "Layanan verifikasi sementara tidak tersedia. Coba lagi nanti."

    if not payload.get("success"):
        codes = payload.get("error-codes") or []
        logger.info("reCAPTCHA failed error-codes=%s", codes)
        return False, "Verifikasi keamanan gagal. Coba lagi."

    score = payload.get("score")
    if score is not None:
        min_score = float(getattr(settings, "RECAPTCHA_MIN_SCORE", 0.5))
        try:
            if float(score) < min_score:
                return False, "Verifikasi keamanan tidak lolos. Coba lagi."
        except (TypeError, ValueError):
            return False, "Verifikasi keamanan tidak valid."

    return True, ""

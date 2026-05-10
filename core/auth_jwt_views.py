"""JWT obtain pair with optional reCAPTCHA (login abuse mitigation)."""

from __future__ import annotations

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.views import TokenObtainPairView

from .recaptcha import recaptcha_is_configured, verify_recaptcha_token


def _client_ip(request) -> str | None:
    xfwd = request.META.get("HTTP_X_FORWARDED_FOR")
    return (xfwd.split(",")[0].strip() if xfwd else None) or request.META.get("REMOTE_ADDR")


def _data_without_recaptcha(request):
    """Return (serializer_data, recaptcha_token_str)."""
    data = request.data
    token = ""

    if isinstance(data, dict):
        d = {k: v for k, v in data.items() if k != "recaptcha_token"}
        token = str(data.get("recaptcha_token") or "").strip()
        return d, token

    mutable = data.copy()
    if hasattr(mutable, "getlist"):
        vals = mutable.getlist("recaptcha_token")
        token = str(vals[0] if vals else "").strip()
    mutable.pop("recaptcha_token", None)
    return mutable, token


class LearnerTokenObtainPairView(TokenObtainPairView):
    """
    Same as TokenObtainPairView but strips `recaptcha_token` from payload.
    If RECAPTCHA_VERIFY_LOGIN is true and RECAPTCHA_SECRET_KEY is set, token is verified first.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "learner_login"

    def post(self, request, *args, **kwargs):
        payload, recaptcha_token = _data_without_recaptcha(request)

        if getattr(settings, "RECAPTCHA_VERIFY_LOGIN", False) and recaptcha_is_configured():
            ok, err = verify_recaptcha_token(recaptcha_token, remote_ip=_client_ip(request))
            if not ok:
                return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(data=payload)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0]) from e

        return Response(serializer.validated_data, status=status.HTTP_200_OK)

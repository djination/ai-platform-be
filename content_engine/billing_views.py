import logging
from datetime import datetime, time, timedelta, timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from .billing_catalog import find_plan, get_catalog_plans, plan_public_dict, valid_paid_plan_codes
from .entitlements import (
    effective_chat_daily_limit,
    effective_content_daily_limit,
    effective_plan_label,
    get_entitlement,
    subscription_is_active,
)
from .models import LearnerEntitlement

logger = logging.getLogger(__name__)


class BillingPlansView(APIView):
    """Katalog paket untuk UI (dari Admin: Billing catalog plan). JWT opsional: menandai paket efektif."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]

    def get(self, request):
        plans = get_catalog_plans()
        effective = None
        if getattr(request.user, "is_authenticated", False):
            effective = effective_plan_label(request.user)
        items = [plan_public_dict(p, effective_code=effective) for p in plans]
        return Response(
            {
                "plans": items,
                "demo_payment_enabled": bool(getattr(settings, "BILLING_DEMO_PAYMENT_ENABLED", False)),
            }
        )


class RequestPlanUpgradeView(APIView):
    """
    Mencatat pilihan upgrade; pembayaran dilakukan nanti via payment gateway.
    Setelah gateway mengonfirmasi, set plan + payment_status=active + pro_access_until (view terpisah / webhook).
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        plan_code = str(request.data.get("plan_code") or "").strip().lower()

        if plan_code == LearnerEntitlement.Plan.FREE or not plan_code:
            return Response(
                {"error": "Pilih paket berbayar (go, plus, pro)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if plan_code not in valid_paid_plan_codes():
            return Response(
                {"error": "Kode paket tidak valid atau tidak aktif di katalog."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ent = get_entitlement(request.user)
        if subscription_is_active(ent):
            return Response(
                {
                    "error": "Langganan aktif masih berlaku. Kelola di halaman akun atau tunggu masa berlaku habis.",
                    "current_plan": ent.plan,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        ent.pending_plan_code = plan_code
        ent.payment_status = LearnerEntitlement.PaymentStatus.PENDING
        ent.save(update_fields=["pending_plan_code", "payment_status", "updated_at"])

        return Response(
            {
                "detail": "Permintaan upgrade tercatat. Lanjutkan pembayaran melalui payment gateway saat sudah terintegrasi.",
                "plan_code": plan_code,
                "payment": {
                    "status": "pending",
                    "next": "payment_gateway",
                },
            },
            status=status.HTTP_200_OK,
        )


class DemoCompletePaymentView(APIView):
    """
    Mengaktifkan langganan seolah pembayaran sukses (hanya jika BILLING_DEMO_PAYMENT_ENABLED).
    Pakai plan_code di body atau pending_plan_code di entitlement.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "billing_demo"

    def post(self, request):
        if not getattr(settings, "BILLING_DEMO_PAYMENT_ENABLED", False):
            return Response(
                {"error": "Demo pembayaran dinonaktifkan di server."},
                status=status.HTTP_403_FORBIDDEN,
            )

        ent = get_entitlement(request.user)
        body_code = str(request.data.get("plan_code") or "").strip().lower()
        code = body_code or (ent.pending_plan_code or "").strip().lower()

        if not code:
            return Response(
                {"error": "Tentukan plan_code atau lakukan permintaan upgrade terlebih dahulu."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if code not in valid_paid_plan_codes():
            return Response(
                {"error": "Kode paket tidak valid."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if subscription_is_active(ent):
            return Response(
                {
                    "error": "Langganan sudah aktif.",
                    "plan": ent.plan,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        paid_values = {
            LearnerEntitlement.Plan.GO.value,
            LearnerEntitlement.Plan.PLUS.value,
            LearnerEntitlement.Plan.PRO.value,
        }
        if code not in paid_values:
            return Response({"error": "Kode paket tidak dikenal."}, status=status.HTTP_400_BAD_REQUEST)

        days = int(getattr(settings, "BILLING_DEMO_SUBSCRIPTION_DAYS", 30))
        ent.plan = code
        ent.payment_status = LearnerEntitlement.PaymentStatus.ACTIVE
        ent.pending_plan_code = ""
        ent.pro_access_until = timezone.now() + timedelta(days=days)
        ent.save(
            update_fields=[
                "plan",
                "payment_status",
                "pending_plan_code",
                "pro_access_until",
                "updated_at",
            ]
        )

        logger.info("Demo payment completed for user_id=%s plan=%s", request.user.pk, code)

        plan_row = find_plan(code)
        return Response(
            {
                "detail": "Pembayaran demo berhasil. Langganan diaktifkan untuk jangka waktu terbatas.",
                "plan": code,
                "pro_access_until": ent.pro_access_until.isoformat(),
                "demo": True,
                "plan_title": (plan_row or {}).get("title"),
                "price_display": (plan_row or {}).get("price_display"),
            },
            status=status.HTTP_200_OK,
        )


class MeEntitlementView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        ent = get_entitlement(user)
        active = subscription_is_active(ent)
        plan = effective_plan_label(user)
        limit = effective_chat_daily_limit(user)
        return Response(
            {
                "plan": plan,
                "stored_plan": ent.plan,
                "subscribed_plan": ent.plan if active else None,
                "pro_access_until": ent.pro_access_until.isoformat() if ent.pro_access_until else None,
                "payment_status": ent.payment_status,
                "pending_plan_code": ent.pending_plan_code or None,
                "chat_daily_message_limit": limit,
                "demo_payment_enabled": bool(getattr(settings, "BILLING_DEMO_PAYMENT_ENABLED", False)),
            }
        )


def _daily_usage(cache_prefix: str, user_id: int) -> int:
    day = timezone.now().date().isoformat()
    return int(cache.get(f"{cache_prefix}:{user_id}:{day}", 0) or 0)


def _remaining(limit: int | None, used: int) -> int | None:
    if limit is None:
        return None
    return max(limit - used, 0)


class MeLimitsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        plan = effective_plan_label(user)
        chat_limit = effective_chat_daily_limit(user)
        content_limit = effective_content_daily_limit(user)
        chat_used = _daily_usage("chat-daily", user.pk)
        content_used = _daily_usage("content-daily", user.pk)
        now = timezone.now()
        reset_at = datetime.combine(
            now.date() + timedelta(days=1),
            time.min,
            tzinfo=dt_timezone.utc,
        )

        return Response(
            {
                "plan": plan,
                "window": "daily",
                "timezone": "UTC",
                "reset_at": reset_at.isoformat(),
                "chat": {
                    "limit": chat_limit,
                    "used": chat_used,
                    "remaining": _remaining(chat_limit, chat_used),
                },
                "content": {
                    "limit": content_limit,
                    "used": content_used,
                    "remaining": _remaining(content_limit, content_used),
                },
            }
        )

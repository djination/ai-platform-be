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
    configured_plan_daily_limits,
    effective_chat_daily_limit,
    effective_content_daily_limit,
    effective_plan_label,
    get_entitlement,
    subscription_is_active,
)
from .models import LearnerEntitlement

logger = logging.getLogger(__name__)


def _downgrade_target_plan_codes(current_plan: str) -> list[str]:
    """Kode paket berbayar lebih rendah yang boleh sebagai target downgrade."""
    p = (current_plan or "").strip().lower()
    if p == LearnerEntitlement.Plan.PRO:
        return [LearnerEntitlement.Plan.PLUS.value, LearnerEntitlement.Plan.GO.value]
    if p == LearnerEntitlement.Plan.PLUS:
        return [LearnerEntitlement.Plan.GO.value]
    return []


class BillingPlansView(APIView):
    """Katalog paket untuk UI (dari Admin: Billing catalog plan). JWT opsional: menandai paket efektif."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]

    def get(self, request):
        plans = get_catalog_plans()
        effective = None
        if getattr(request.user, "is_authenticated", False):
            effective = effective_plan_label(request.user)
        items = []
        for p in plans:
            row = plan_public_dict(p, effective_code=effective)
            row["daily_limits"] = configured_plan_daily_limits(row.get("code"))
            items.append(row)
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
        ent.cancel_at_period_end = False
        ent.pro_access_until = timezone.now() + timedelta(days=days)
        ent.save(
            update_fields=[
                "plan",
                "payment_status",
                "pending_plan_code",
                "cancel_at_period_end",
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


class SubscriptionManageView(APIView):
    """
    Pembatalan langganan (default: berhenti di akhir pro_access_until; opsi when=immediate)
    atau downgrade ke tier berbayar lebih rendah.
    Downgrade: pro_access_until tidak diubah; flag cancel di akhir periode dihapus.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "billing_manage"

    def post(self, request):
        intent = str(request.data.get("intent") or "").strip().lower()
        plan_code = str(request.data.get("plan_code") or "").strip().lower()
        ent = get_entitlement(request.user)

        if intent == "revoke_cancel":
            if not ent.cancel_at_period_end:
                return Response(
                    {"error": "Tidak ada jadwal berhenti di akhir periode untuk diurungkan."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            ent.cancel_at_period_end = False
            ent.save(update_fields=["cancel_at_period_end", "updated_at"])
            logger.info("Scheduled subscription cancel revoked user_id=%s", request.user.pk)
            return Response(
                {
                    "detail": "Jadwal berhenti di akhir periode dibatalkan. Langganan tetap seperti biasa.",
                    "cancel_at_period_end": False,
                },
                status=status.HTTP_200_OK,
            )

        if intent == "cancel":
            if not subscription_is_active(ent) or ent.plan == LearnerEntitlement.Plan.FREE:
                return Response(
                    {"error": "Tidak ada langganan berbayar aktif yang bisa dibatalkan."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            when = str(request.data.get("when") or "period_end").strip().lower()
            if when == "immediate":
                ent.plan = LearnerEntitlement.Plan.FREE
                ent.payment_status = LearnerEntitlement.PaymentStatus.NONE
                ent.pro_access_until = None
                ent.pending_plan_code = ""
                ent.cancel_at_period_end = False
                ent.save(
                    update_fields=[
                        "plan",
                        "payment_status",
                        "pro_access_until",
                        "pending_plan_code",
                        "cancel_at_period_end",
                        "updated_at",
                    ]
                )
                logger.info("Subscription cancelled immediately user_id=%s", request.user.pk)
                return Response(
                    {
                        "detail": "Langganan dihentikan segera. Anda kembali ke paket Free.",
                        "plan": LearnerEntitlement.Plan.FREE.value,
                        "payment_status": LearnerEntitlement.PaymentStatus.NONE.value,
                        "cancel_at_period_end": False,
                    },
                    status=status.HTTP_200_OK,
                )
            if when not in ("period_end", "end_of_period", ""):
                return Response(
                    {"error": "Nilai when tidak dikenal. Gunakan period_end atau immediate."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if ent.cancel_at_period_end:
                return Response(
                    {"error": "Pembatalan di akhir periode sudah dijadwalkan."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            ent.cancel_at_period_end = True
            ent.pending_plan_code = ""
            ent.save(update_fields=["cancel_at_period_end", "pending_plan_code", "updated_at"])
            logger.info("Subscription cancel at period end scheduled user_id=%s", request.user.pk)
            until_iso = ent.pro_access_until.isoformat() if ent.pro_access_until else None
            return Response(
                {
                    "detail": "Langganan akan berhenti di akhir masa berlaku. Sampai saat itu paket Anda tetap aktif.",
                    "cancel_at_period_end": True,
                    "plan": ent.plan,
                    "pro_access_until": until_iso,
                    "payment_status": ent.payment_status,
                },
                status=status.HTTP_200_OK,
            )

        if intent == "downgrade":
            if not subscription_is_active(ent):
                return Response(
                    {"error": "Langganan tidak aktif."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if ent.plan == LearnerEntitlement.Plan.FREE:
                return Response(
                    {"error": "Paket Anda sudah Free."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if plan_code in ("", LearnerEntitlement.Plan.FREE.value):
                return Response(
                    {
                        "error": "Untuk kembali ke Free gunakan pembatalan langganan (intent cancel).",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            allowed = _downgrade_target_plan_codes(ent.plan)
            if plan_code not in allowed:
                return Response(
                    {
                        "error": "Downgrade ke paket ini tidak diizinkan.",
                        "allowed": allowed,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if plan_code not in valid_paid_plan_codes():
                return Response(
                    {"error": "Kode paket tidak aktif di katalog."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            old = ent.plan
            ent.plan = plan_code
            ent.pending_plan_code = ""
            ent.cancel_at_period_end = False
            ent.payment_status = LearnerEntitlement.PaymentStatus.ACTIVE
            ent.save(
                update_fields=[
                    "plan",
                    "pending_plan_code",
                    "cancel_at_period_end",
                    "payment_status",
                    "updated_at",
                ]
            )
            logger.info(
                "Subscription downgrade user_id=%s %s -> %s",
                request.user.pk,
                old,
                plan_code,
            )
            until = ent.pro_access_until.isoformat() if ent.pro_access_until else None
            return Response(
                {
                    "detail": "Paket diturunkan. Masa berlaku langganan tidak diubah.",
                    "plan": plan_code,
                    "pro_access_until": until,
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "error": "Field intent wajib: cancel, revoke_cancel, atau downgrade (dengan plan_code untuk downgrade).",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )


class MeEntitlementView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        ent = get_entitlement(user)
        active = subscription_is_active(ent)
        plan = effective_plan_label(user)
        limit = effective_chat_daily_limit(user)
        pending_raw = (ent.pending_plan_code or "").strip()
        pending_out = (
            None
            if ent.payment_status == LearnerEntitlement.PaymentStatus.ACTIVE
            else (pending_raw or None)
        )
        downgrade_options: list[dict[str, str]] = []
        if active and ent.plan in (
            LearnerEntitlement.Plan.GO,
            LearnerEntitlement.Plan.PLUS,
            LearnerEntitlement.Plan.PRO,
        ):
            for code in _downgrade_target_plan_codes(ent.plan):
                row = find_plan(code)
                downgrade_options.append(
                    {"code": code, "title": (row or {}).get("title") or code}
                )
        can_manage = bool(
            active
            and ent.plan
            in (
                LearnerEntitlement.Plan.GO,
                LearnerEntitlement.Plan.PLUS,
                LearnerEntitlement.Plan.PRO,
            )
        )
        return Response(
            {
                "plan": plan,
                "stored_plan": ent.plan,
                "subscribed_plan": ent.plan if active else None,
                "pro_access_until": ent.pro_access_until.isoformat() if ent.pro_access_until else None,
                "payment_status": ent.payment_status,
                "pending_plan_code": pending_out,
                "chat_daily_message_limit": limit,
                "demo_payment_enabled": bool(getattr(settings, "BILLING_DEMO_PAYMENT_ENABLED", False)),
                "can_manage_subscription": can_manage,
                "downgrade_plan_options": downgrade_options,
                "cancel_at_period_end": bool(ent.cancel_at_period_end),
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

from django.conf import settings
from django.utils import timezone

from .models import LearnerEntitlement


def _normalize_limit(raw: int) -> int | None:
    return None if int(raw) <= 0 else int(raw)


def configured_plan_daily_limits(plan_code: str) -> dict[str, int | None]:
    code = str(plan_code or "").strip().lower()
    if code == LearnerEntitlement.Plan.GO:
        chat = getattr(settings, "CHAT_GO_DAILY_MESSAGE_LIMIT", 400)
        content = getattr(settings, "CONTENT_GO_DAILY_LIMIT", 15)
    elif code == LearnerEntitlement.Plan.PLUS:
        chat = getattr(settings, "CHAT_PLUS_DAILY_MESSAGE_LIMIT", 2000)
        content = getattr(settings, "CONTENT_PLUS_DAILY_LIMIT", 50)
    elif code == LearnerEntitlement.Plan.PRO:
        chat = getattr(settings, "CHAT_PRO_DAILY_MESSAGE_LIMIT", 0)
        content = getattr(settings, "CONTENT_PRO_DAILY_LIMIT", 0)
    else:
        chat = getattr(settings, "CHAT_DAILY_MESSAGE_LIMIT", 200)
        content = getattr(settings, "CONTENT_DAILY_LIMIT", 5)
    return {"chat": _normalize_limit(chat), "content": _normalize_limit(content)}


def maybe_finalize_expired_paid_period(ent: LearnerEntitlement) -> bool:
    """
    Jika langganan berbayar sudah lewat pro_access_until, samakan baris DB ke Free.
    Berlaku untuk habis masa natural maupun setelah user memilih cancel di akhir periode.
    Return True jika baris diperbarui.
    """
    now = timezone.now()
    if ent.payment_status != LearnerEntitlement.PaymentStatus.ACTIVE:
        return False
    if ent.plan == LearnerEntitlement.Plan.FREE:
        return False
    until = ent.pro_access_until
    if until is None or until > now:
        return False
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
    return True


def get_entitlement(user) -> LearnerEntitlement:
    ent, _ = LearnerEntitlement.objects.get_or_create(user=user)
    maybe_finalize_expired_paid_period(ent)
    return ent


def subscription_is_active(ent: LearnerEntitlement) -> bool:
    if ent.payment_status != LearnerEntitlement.PaymentStatus.ACTIVE:
        return False
    if ent.plan == LearnerEntitlement.Plan.FREE:
        return False
    until = ent.pro_access_until
    if until is None:
        return False
    return until > timezone.now()


def user_has_paid_subscription(user) -> bool:
    """True jika langganan berbayar masih berlaku (Go / Plus / Pro)."""
    return subscription_is_active(get_entitlement(user))


def effective_chat_daily_limit(user) -> int | None:
    """
    Batas pesan chat per hari. None = tanpa batas harian (biasanya Pro).
    Free: CHAT_DAILY_MESSAGE_LIMIT. Tier berbayar: CHAT_*_DAILY_MESSAGE_LIMIT (0 => unlimited).
    """
    ent = get_entitlement(user)
    if not subscription_is_active(ent):
        return int(getattr(settings, "CHAT_DAILY_MESSAGE_LIMIT", 200))

    tier = ent.plan
    if tier == LearnerEntitlement.Plan.GO:
        cap = int(getattr(settings, "CHAT_GO_DAILY_MESSAGE_LIMIT", 400))
    elif tier == LearnerEntitlement.Plan.PLUS:
        cap = int(getattr(settings, "CHAT_PLUS_DAILY_MESSAGE_LIMIT", 2000))
    elif tier == LearnerEntitlement.Plan.PRO:
        cap = int(getattr(settings, "CHAT_PRO_DAILY_MESSAGE_LIMIT", 0))
    else:
        cap = int(getattr(settings, "CHAT_DAILY_MESSAGE_LIMIT", 200))

    if cap <= 0:
        return None
    return cap


def effective_content_daily_limit(user) -> int | None:
    """
    Batas akses konten/modul per hari. None = tanpa batas harian.
    Free: CONTENT_DAILY_LIMIT. Tier berbayar: CONTENT_*_DAILY_LIMIT.
    """
    ent = get_entitlement(user)
    if not subscription_is_active(ent):
        return int(getattr(settings, "CONTENT_DAILY_LIMIT", 5))

    tier = ent.plan
    if tier == LearnerEntitlement.Plan.GO:
        cap = int(getattr(settings, "CONTENT_GO_DAILY_LIMIT", 15))
    elif tier == LearnerEntitlement.Plan.PLUS:
        cap = int(getattr(settings, "CONTENT_PLUS_DAILY_LIMIT", 50))
    elif tier == LearnerEntitlement.Plan.PRO:
        cap = int(getattr(settings, "CONTENT_PRO_DAILY_LIMIT", 0))
    else:
        cap = int(getattr(settings, "CONTENT_DAILY_LIMIT", 5))

    if cap <= 0:
        return None
    return cap


def effective_plan_label(user) -> str:
    """Kode paket untuk kuota & UI (free / go / plus / pro)."""
    ent = get_entitlement(user)
    if subscription_is_active(ent):
        return str(ent.plan)
    return LearnerEntitlement.Plan.FREE.value

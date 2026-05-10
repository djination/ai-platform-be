from django.conf import settings
from django.utils import timezone

from .models import LearnerEntitlement


def get_entitlement(user) -> LearnerEntitlement:
    ent, _ = LearnerEntitlement.objects.get_or_create(user=user)
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

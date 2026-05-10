from django.conf import settings
from django.db import models


class IngestAPIKey(models.Model):
    name = models.CharField(max_length=100, unique=True)
    key = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Ingest API key"
        verbose_name_plural = "Ingest API keys"

    def __str__(self):
        return f"{self.name} ({'active' if self.is_active else 'inactive'})"


class RawContent(models.Model):
    title = models.CharField(max_length=255)
    source_url = models.URLField()
    raw_text = models.TextField()
    category = models.CharField(max_length=100)
    language_code = models.CharField(max_length=10, default="en")
    locale = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Optional BCP 47 locale display hint (e.g. en-US, id-ID).",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class ProcessedModule(models.Model):
    class Difficulty(models.TextChoices):
        BEGINNER = "beginner", "Beginner"
        INTERMEDIATE = "intermediate", "Intermediate"
        ADVANCED = "advanced", "Advanced"

    class ReviewStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        REVIEWED = "reviewed", "Reviewed"
        REJECTED = "rejected", "Rejected"

    raw_content = models.ForeignKey(
        RawContent,
        on_delete=models.CASCADE,
        related_name="processed_modules",
    )
    module_json = models.JSONField()
    difficulty = models.CharField(
        max_length=20,
        choices=Difficulty.choices,
        default=Difficulty.BEGINNER,
    )
    review_status = models.CharField(
        max_length=20,
        choices=ReviewStatus.choices,
        default=ReviewStatus.DRAFT,
    )
    review_notes = models.TextField(blank=True, default="")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    is_published = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.raw_content.title} ({self.difficulty})"


class EnrichmentCache(models.Model):
    prompt_hash = models.CharField(max_length=64, unique=True)
    prompt_type = models.CharField(max_length=50)
    model_name = models.CharField(max_length=100, default="openrouter-default")
    response_json = models.JSONField(default=dict, blank=True)
    token_usage = models.PositiveIntegerField(default=0)
    estimated_cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.prompt_type} ({self.model_name})"


class EnrichmentJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    raw_content = models.ForeignKey(
        RawContent,
        on_delete=models.CASCADE,
        related_name="enrichment_jobs",
    )
    prompt_type = models.CharField(max_length=50)
    prompt_body = models.TextField()
    prompt_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    response_json = models.JSONField(default=dict, blank=True)
    token_usage = models.PositiveIntegerField(default=0)
    estimated_cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["prompt_hash"]),
        ]

    def __str__(self):
        return f"{self.raw_content_id} - {self.prompt_type} ({self.status})"


class ChatSession(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_sessions",
    )
    session_key = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "session_key"], name="uniq_chat_session_per_user"),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.session_key[:8]}"


class ChatMessage(models.Model):
    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        SYSTEM = "system", "System"

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=Role.choices)
    content = models.TextField()
    intent_route = models.CharField(max_length=32, blank=True, default="")
    mode = models.CharField(max_length=32, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class ChatRoutingAudit(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_routing_audits",
    )
    session_key = models.CharField(max_length=64, blank=True, default="")
    message_preview = models.CharField(max_length=200)
    classified_intent = models.CharField(max_length=32)
    confidence = models.FloatField(default=0)
    route_chosen = models.CharField(max_length=32)
    ambiguous = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class LearnerEntitlement(models.Model):
    """Paket & status pembayaran peserta. Plan aktif + masa berlaku diisi setelah gateway memverifikasi bayar."""

    class Plan(models.TextChoices):
        FREE = "free", "Free"
        GO = "go", "Go"
        PLUS = "plus", "Plus"
        PRO = "pro", "Pro"

    class PaymentStatus(models.TextChoices):
        NONE = "none", "None"
        PENDING = "pending", "Pending payment"
        ACTIVE = "active", "Active"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="learner_entitlement",
    )
    plan = models.CharField(
        max_length=16,
        choices=Plan.choices,
        default=Plan.FREE,
        db_index=True,
    )
    payment_status = models.CharField(
        max_length=16,
        choices=PaymentStatus.choices,
        default=PaymentStatus.NONE,
        db_index=True,
    )
    pending_plan_code = models.CharField(
        max_length=16,
        blank=True,
        default="",
        help_text="Paket yang dipilih user; menunggu pembayaran gateway.",
    )
    pro_access_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text="UTC sampai langganan berlaku (diisi setelah pembayaran terverifikasi).",
    )
    stripe_customer_id = models.CharField(max_length=255, blank=True, default="")
    stripe_subscription_id = models.CharField(max_length=255, blank=True, default="")
    stripe_subscription_status = models.CharField(max_length=32, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "learner entitlement"
        verbose_name_plural = "learner entitlements"

    def __str__(self):
        return f"{self.user_id}:{self.plan}"


class BillingCatalogPlan(models.Model):
    """Katalog paket (Free / Go / Plus / Pro) untuk UI learner; kelola di Django Admin."""

    code = models.CharField(max_length=16, unique=True, db_index=True)
    title = models.CharField(max_length=64)
    price_prefix = models.CharField(max_length=32, blank=True, default="")
    price_idr_monthly = models.PositiveIntegerField(default=0)
    price_display_override = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Kosong = tampilkan nominal otomatis dari harga bulanan (format IDR).",
    )
    period_label = models.CharField(max_length=32, default="/ bulan")
    vat_note = models.CharField(max_length=120, blank=True, default="")
    slogan = models.TextField(blank=True, default="")
    features = models.JSONField(
        default=list,
        blank=True,
        help_text='Daftar fitur JSON, mis. [{"icon":"spark","text":"..."}]',
    )
    footer_note = models.TextField(blank=True, default="")
    popular = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "paket billing (katalog)"
        verbose_name_plural = "paket billing (katalog)"

    def get_price_display(self) -> str:
        o = (self.price_display_override or "").strip()
        if o:
            return o
        if self.price_idr_monthly == 0:
            return "Rp 0"
        formatted = f"{self.price_idr_monthly:,}".replace(",", ".")
        return f"Rp {formatted}"

    def to_catalog_dict(self) -> dict:
        return {
            "code": self.code,
            "title": self.title,
            "price_prefix": self.price_prefix,
            "price_idr_monthly": self.price_idr_monthly,
            "price_display": self.get_price_display(),
            "period_label": self.period_label,
            "vat_note": self.vat_note,
            "slogan": self.slogan,
            "features": list(self.features or []),
            "footer_note": self.footer_note,
            "popular": self.popular,
            "cta_kind": "current_free" if self.code == "free" else "upgrade",
        }

    def __str__(self):
        return f"{self.code} — {self.title}"


class ChatReplyCache(models.Model):
    """First-turn (no history) exact-match cache for chat replies; reduces LLM token use."""

    lookup_hash = models.CharField(max_length=64, unique=True, db_index=True)
    route = models.CharField(max_length=32)
    reply = models.TextField()
    hit_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "chat reply caches"

    def __str__(self):
        return f"{self.lookup_hash[:12]}…"

from django.contrib import admin

from .models import (
    BillingCatalogPlan,
    EnrichmentCache,
    EnrichmentJob,
    IngestAPIKey,
    LearnerEntitlement,
    ProcessedModule,
    RawContent,
)


@admin.register(IngestAPIKey)
class IngestAPIKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at", "last_used_at")
    list_filter = ("is_active",)
    search_fields = ("name", "key")


@admin.register(RawContent)
class RawContentAdmin(admin.ModelAdmin):
    list_display = ("title", "source_url", "category", "created_at")
    list_filter = ("category",)
    search_fields = ("title", "source_url")


@admin.register(ProcessedModule)
class ProcessedModuleAdmin(admin.ModelAdmin):
    list_display = ("raw_content", "difficulty", "review_status", "is_published")
    list_filter = ("difficulty", "review_status", "is_published")


@admin.register(EnrichmentJob)
class EnrichmentJobAdmin(admin.ModelAdmin):
    list_display = ("raw_content", "prompt_type", "status", "token_usage", "estimated_cost_usd")
    list_filter = ("prompt_type", "status")


@admin.register(BillingCatalogPlan)
class BillingCatalogPlanAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "title",
        "sort_order",
        "price_idr_monthly",
        "get_price_display",
        "popular",
        "is_active",
    )
    list_filter = ("is_active", "popular")
    list_editable = ("sort_order", "popular", "is_active")
    search_fields = ("code", "title")
    ordering = ("sort_order", "id")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "code",
                    "title",
                    "sort_order",
                    "is_active",
                    "popular",
                )
            },
        ),
        (
            "Harga & teks",
            {
                "fields": (
                    "price_prefix",
                    "price_idr_monthly",
                    "price_display_override",
                    "period_label",
                    "vat_note",
                    "slogan",
                    "footer_note",
                )
            },
        ),
        (
            "Fitur (JSON)",
            {
                "fields": ("features",),
                "description": 'Contoh: [{"icon":"spark","text":"Model inti tutor"}]. Ikon: spark, message, book, memory, search, stack, zap.',
            },
        ),
    )

    @admin.display(description="Tampilan harga")
    def get_price_display(self, obj: BillingCatalogPlan):
        return obj.get_price_display()


@admin.register(LearnerEntitlement)
class LearnerEntitlementAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "plan",
        "payment_status",
        "pending_plan_code",
        "pro_access_until",
        "updated_at",
    )
    list_filter = ("plan", "payment_status")
    search_fields = ("user__username", "pending_plan_code")


@admin.register(EnrichmentCache)
class EnrichmentCacheAdmin(admin.ModelAdmin):
    list_display = ("prompt_type", "model_name", "token_usage", "estimated_cost_usd", "created_at")
    list_filter = ("prompt_type", "model_name")

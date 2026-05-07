from django.contrib import admin

from .models import IngestAPIKey, ProcessedModule, RawContent


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
    list_display = ("raw_content", "difficulty", "is_published")
    list_filter = ("difficulty", "is_published")

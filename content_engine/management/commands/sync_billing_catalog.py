from __future__ import annotations

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import connection
from django.db import transaction

from content_engine.billing_catalog import default_catalog_plans
from content_engine.models import BillingCatalogPlan


class Command(BaseCommand):
    help = "Sync free/go/plus/pro catalog rows with default package configuration."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without writing to database.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        table_names = connection.introspection.table_names()
        if BillingCatalogPlan._meta.db_table not in table_names:
            raise CommandError(
                "Table BillingCatalogPlan belum ada. Jalankan `py manage.py migrate` dulu."
            )

        defaults = {plan["code"]: plan for plan in default_catalog_plans()}

        updated = 0
        created = 0

        for code, data in defaults.items():
            payload = {
                "title": data["title"],
                "price_prefix": data["price_prefix"],
                "price_idr_monthly": data["price_idr_monthly"],
                "price_display_override": data.get("price_display_override", ""),
                "period_label": data["period_label"],
                "vat_note": data["vat_note"],
                "slogan": data["slogan"],
                "features": data["features"],
                "footer_note": data["footer_note"],
                "popular": data["popular"],
                "is_active": data.get("is_active", True),
                "sort_order": data.get("sort_order", 0),
            }
            obj, was_created = BillingCatalogPlan.objects.get_or_create(
                code=code,
                defaults=payload,
            )
            if was_created:
                created += 1
                self.stdout.write(f"[create] {code}")
                continue

            changed_fields: list[str] = []
            for field, value in payload.items():
                if getattr(obj, field) != value:
                    setattr(obj, field, value)
                    changed_fields.append(field)

            if changed_fields:
                updated += 1
                self.stdout.write(f"[update] {code}: {', '.join(changed_fields)}")
                if not dry_run:
                    obj.save(update_fields=changed_fields)
            else:
                self.stdout.write(f"[ok] {code}: no change")

        if dry_run:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING("Dry-run mode: no data written."))

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. created={created}, updated={updated}, scanned={len(defaults)}"
            )
        )

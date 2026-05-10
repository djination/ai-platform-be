"""
Load sample Indonesian pilot modules using the same persistence steps as POST /api/content-engine/ingest/.

Does not require HTTP or ingest API key (runs inside Django).
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from content_engine.models import ProcessedModule, RawContent
from content_engine.pipeline import apply_post_ingest_metadata
from content_engine.serializers import RawContentIngestSerializer


def _default_fixture_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "fixtures" / "pilot_id_ingest_payloads.json"


class Command(BaseCommand):
    help = "Seed pilot Indonesian (id) modules from JSON ingest payloads (same logic as HTTP ingest)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fixture",
            default="",
            help="Path to JSON array of ingest payloads (default: content_engine/fixtures/pilot_id_ingest_payloads.json)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print titles only; do not write to the database.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Create even when source_url already exists.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Maximum number of items to process (0 = all).",
        )
        parser.add_argument(
            "--publish",
            action="store_true",
            help="After each insert, set ProcessedModule.is_published=True for learner visibility.",
        )
        parser.add_argument(
            "--skip-enrichment",
            action="store_true",
            help="Do not queue enrichment jobs (faster local seed).",
        )

    def handle(self, *args, **options):
        fixture_arg = (options["fixture"] or "").strip()
        path = Path(fixture_arg) if fixture_arg else _default_fixture_path()
        if not path.is_file():
            raise CommandError(f"Fixture not found: {path}")

        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON in {path}: {exc}") from exc

        if not isinstance(items, list):
            raise CommandError("Fixture root must be a JSON array")

        limit = int(options["limit"] or 0)
        if limit > 0:
            items = items[:limit]

        dry_run = options["dry_run"]
        force = options["force"]
        publish = options["publish"]
        skip_enrichment = options["skip_enrichment"]

        created = 0
        skipped = 0
        errors = 0

        for payload in items:
            if not isinstance(payload, dict):
                self.stderr.write(self.style.WARNING("Skip non-object entry in fixture"))
                errors += 1
                continue

            source_url = str(payload.get("source_url") or "").strip()
            title = str(payload.get("title") or "").strip()
            if not source_url or not title:
                self.stderr.write(self.style.WARNING("Skip entry missing source_url or title"))
                errors += 1
                continue

            if dry_run:
                self.stdout.write(f"[dry-run] would load: {title} ({source_url})")
                continue

            if not force and RawContent.objects.filter(source_url=source_url).exists():
                self.stdout.write(self.style.NOTICE(f"Skip existing {source_url}"))
                skipped += 1
                continue

            serializer = RawContentIngestSerializer(data=payload)
            if not serializer.is_valid():
                self.stderr.write(self.style.ERROR(f"Invalid payload {source_url}: {serializer.errors}"))
                errors += 1
                continue

            try:
                with transaction.atomic():
                    raw_content = serializer.save()
                    apply_post_ingest_metadata(raw_content, queue_jobs=not skip_enrichment)
                    if publish:
                        updated = ProcessedModule.objects.filter(raw_content=raw_content).update(
                            is_published=True
                        )
                        if not updated:
                            self.stderr.write(
                                self.style.WARNING(f"No ProcessedModule for raw id={raw_content.id}")
                            )
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"Failed {source_url}: {exc}"))
                errors += 1
                continue

            created += 1
            self.stdout.write(self.style.SUCCESS(f"Created raw + module: {title}"))

        if dry_run:
            self.stdout.write(self.style.NOTICE(f"Dry run complete ({len(items)} items)."))
            return

        self.stdout.write(
            self.style.NOTICE(f"Done. created={created} skipped={skipped} invalid/failed={errors}")
        )

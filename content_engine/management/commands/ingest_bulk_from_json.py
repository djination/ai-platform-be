"""
Bulk-ingest minimal payloads (e.g. OpenClaw export) using the same logic as POST /ingest/.

Expected JSON: an array of objects with at least:
  title, source_url, raw_text, category

Optional fields match RawContentIngestSerializer (language_code, locale, metadata, processed_module).

Does not perform web search or scraping — only persists rows from the file.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from content_engine.models import RawContent
from content_engine.pipeline import apply_post_ingest_metadata
from content_engine.serializers import RawContentIngestSerializer


class Command(BaseCommand):
    help = "Ingest many items from a JSON array file (OpenClaw-style minimal payloads supported)."

    def add_arguments(self, parser):
        parser.add_argument(
            "json_path",
            type=str,
            help="Path to JSON file containing an array of ingest payloads",
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Create even when source_url already exists in the database.",
        )
        parser.add_argument("--limit", type=int, default=0, help="Max rows (0 = all).")
        parser.add_argument(
            "--skip-enrichment",
            action="store_true",
            help="Skip enqueueing enrichment jobs after each row.",
        )

    def handle(self, *args, **options):
        path = Path(options["json_path"])
        if not path.is_file():
            raise CommandError(f"File not found: {path}")

        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON: {exc}") from exc

        if not isinstance(items, list):
            raise CommandError("Root JSON value must be an array")

        limit = int(options["limit"] or 0)
        if limit > 0:
            items = items[:limit]

        dry_run = options["dry_run"]
        force = options["force"]
        skip_enrichment = options["skip_enrichment"]

        ok = 0
        skipped = 0
        failed = 0

        for row in items:
            if not isinstance(row, dict):
                failed += 1
                self.stderr.write(self.style.WARNING("Skip non-object row"))
                continue

            source_url = str(row.get("source_url") or "").strip()
            title = str(row.get("title") or "").strip()
            if not source_url or not title:
                failed += 1
                self.stderr.write(self.style.ERROR(f"Skip row missing title or source_url: {title or source_url}"))
                continue

            if dry_run:
                self.stdout.write(f"[dry-run] {title[:80]}… → {source_url}")
                continue

            if not force and RawContent.objects.filter(source_url=source_url).exists():
                skipped += 1
                self.stdout.write(self.style.NOTICE(f"Skip existing URL: {source_url}"))
                continue

            serializer = RawContentIngestSerializer(data=row)
            if not serializer.is_valid():
                failed += 1
                self.stderr.write(
                    self.style.ERROR(f"Invalid payload for {source_url}: {serializer.errors}")
                )
                continue

            try:
                with transaction.atomic():
                    raw_content = serializer.save()
                    apply_post_ingest_metadata(raw_content, queue_jobs=not skip_enrichment)
            except Exception as exc:
                failed += 1
                self.stderr.write(self.style.ERROR(f"Failed {source_url}: {exc}"))
                continue

            ok += 1
            self.stdout.write(self.style.SUCCESS(f"201-equivalent saved: {title[:72]}"))

        if dry_run:
            self.stdout.write(self.style.NOTICE(f"Dry run: {len(items)} row(s)."))
            return

        self.stdout.write(self.style.NOTICE(f"Done. saved={ok} skipped={skipped} failed={failed}"))

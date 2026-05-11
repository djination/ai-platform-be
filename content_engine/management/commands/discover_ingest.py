"""Cron-friendly wrapper around content_engine.discovery.run_discover_and_ingest."""

from django.core.management.base import BaseCommand, CommandError

from content_engine.discovery import run_discover_and_ingest


class Command(BaseCommand):
    help = "Search the web, extract article text, and ingest RawContent rows (same as admin discover)."

    def add_arguments(self, parser):
        parser.add_argument("--query", required=True, help="Search query string")
        parser.add_argument("--category", required=True, help="RawContent.category value")
        parser.add_argument("--max-results", type=int, default=10)
        parser.add_argument("--language-code", default="en")
        parser.add_argument("--locale", default="")
        parser.add_argument(
            "--search-backend",
            choices=("duckduckgo", "serpapi", "google"),
            default=None,
            help="Override DISCOVERY_SEARCH_BACKEND (default: from settings)",
        )
        parser.add_argument(
            "--skip-enrichment",
            action="store_true",
            help="Do not enqueue enrichment jobs after each ingest",
        )
        parser.add_argument(
            "--suggested-difficulty",
            default="beginner",
            choices=("beginner", "intermediate", "advanced"),
            help="Stored in RawContent.metadata for filtering and draft defaults (same as admin UI).",
        )

    def handle(self, *args, **options):
        query = options["query"].strip()
        category = options["category"].strip()
        max_results = int(options["max_results"])
        language_code = options["language_code"].strip()
        locale = options["locale"].strip()
        backend = options["search_backend"]
        skip_enrichment = options["skip_enrichment"]
        suggested_difficulty = options["suggested_difficulty"]

        report = run_discover_and_ingest(
            query=query,
            max_results=max_results,
            category=category,
            language_code=language_code,
            locale=locale,
            suggested_difficulty=suggested_difficulty,
            search_backend=backend,
            queue_jobs=not skip_enrichment,
        )

        if report.get("error"):
            raise CommandError(report["error"])

        self.stdout.write(f"Candidates: {report.get('candidates_found')}")
        self.stdout.write(self.style.SUCCESS(f"Created: {len(report.get('created') or [])}"))
        self.stdout.write(f"Skipped: {len(report.get('skipped') or [])}")
        self.stdout.write(f"Failed: {len(report.get('failed') or [])}")
        for row in report.get("created") or []:
            self.stdout.write(f"  + {row.get('title')} :: {row.get('source_url')}")
        for row in report.get("failed") or []:
            self.stderr.write(self.style.WARNING(f"  ! {row.get('source_url')} :: {row.get('error')}"))

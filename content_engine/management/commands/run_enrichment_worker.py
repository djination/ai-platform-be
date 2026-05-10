from django.core.management.base import BaseCommand
from django.utils import timezone

from content_engine.models import EnrichmentJob
from content_engine.pipeline import execute_enrichment_job


class Command(BaseCommand):
    help = "Process pending enrichment jobs."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **options):
        limit = options["limit"]
        jobs = EnrichmentJob.objects.filter(status=EnrichmentJob.Status.PENDING).order_by("id")[:limit]
        processed = 0
        for job in jobs:
            job.status = EnrichmentJob.Status.RUNNING
            job.started_at = timezone.now()
            job.save(update_fields=["status", "started_at"])
            try:
                execute_enrichment_job(job)
                job.finished_at = timezone.now()
                job.error_message = ""
                job.save(
                    update_fields=[
                        "status",
                        "response_json",
                        "token_usage",
                        "estimated_cost_usd",
                        "finished_at",
                        "error_message",
                    ]
                )
                processed += 1
            except Exception as exc:
                job.status = EnrichmentJob.Status.FAILED
                job.error_message = str(exc)
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "error_message", "finished_at"])
        self.stdout.write(self.style.SUCCESS(f"Processed {processed} enrichment job(s)."))

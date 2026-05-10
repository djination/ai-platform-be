import secrets

from django.core.management.base import BaseCommand, CommandError

from content_engine.models import IngestAPIKey


class Command(BaseCommand):
    help = "Rotate ingest API key by creating a new active key."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--deactivate", default="", help="Comma-separated names to deactivate")

    def handle(self, *args, **options):
        name = options["name"].strip()
        if not name:
            raise CommandError("name is required")

        key_value = secrets.token_urlsafe(32)
        new_key = IngestAPIKey.objects.create(name=name, key=key_value, is_active=True)

        deactivate_names = [x.strip() for x in options["deactivate"].split(",") if x.strip()]
        if deactivate_names:
            IngestAPIKey.objects.filter(name__in=deactivate_names).update(is_active=False)

        self.stdout.write(self.style.SUCCESS(f"Created new ingest key '{new_key.name}'"))
        self.stdout.write("Store this key securely:")
        self.stdout.write(key_value)

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
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class ProcessedModule(models.Model):
    class Difficulty(models.TextChoices):
        BEGINNER = "beginner", "Beginner"
        INTERMEDIATE = "intermediate", "Intermediate"
        ADVANCED = "advanced", "Advanced"

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
    is_published = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.raw_content.title} ({self.difficulty})"

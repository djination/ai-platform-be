from rest_framework import serializers

from .models import ProcessedModule, RawContent


class ProcessedModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcessedModule
        fields = ("module_json", "difficulty", "is_published")


class RawContentIngestSerializer(serializers.ModelSerializer):
    processed_module = ProcessedModuleSerializer(required=False)
    language_code = serializers.CharField(required=False, allow_blank=False, default="en")
    locale = serializers.CharField(required=False, allow_blank=True, default="")
    metadata = serializers.JSONField(required=False, default=dict)

    class Meta:
        model = RawContent
        fields = (
            "source_url",
            "title",
            "raw_text",
            "category",
            "language_code",
            "locale",
            "metadata",
            "processed_module",
        )

    def validate_language_code(self, value):
        normalized = value.strip().lower()
        if len(normalized) not in (2, 5):
            raise serializers.ValidationError("language_code must be ISO code (e.g. en or en-us).")
        return normalized

    def validate_locale(self, value):
        if not value or not str(value).strip():
            return ""
        return str(value).strip()[:32]

    def create(self, validated_data):
        processed_module_data = validated_data.pop("processed_module", None)
        raw_content = RawContent.objects.create(**validated_data)

        if processed_module_data:
            ProcessedModule.objects.create(
                raw_content=raw_content,
                **processed_module_data,
            )

        return raw_content

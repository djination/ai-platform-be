from rest_framework import serializers

from .models import ProcessedModule, RawContent


class ProcessedModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcessedModule
        fields = ("module_json", "difficulty", "is_published")


class RawContentIngestSerializer(serializers.ModelSerializer):
    processed_module = ProcessedModuleSerializer(required=False)

    class Meta:
        model = RawContent
        fields = (
            "source_url",
            "title",
            "raw_text",
            "category",
            "status",
            "processed_module",
        )

    def create(self, validated_data):
        processed_module_data = validated_data.pop("processed_module", None)
        raw_content = RawContent.objects.create(**validated_data)

        if processed_module_data:
            ProcessedModule.objects.create(
                raw_content=raw_content,
                **processed_module_data,
            )

        return raw_content

from rest_framework import status
from rest_framework.test import APITestCase

from .models import IngestAPIKey, ProcessedModule, RawContent


class RawContentIngestViewSetTests(APITestCase):
    url = "/api/content-engine/ingest/"

    def setUp(self):
        self.valid_payload = {
            "source_url": "https://example.com/content-1",
            "title": "Test Content",
            "raw_text": "Sample raw text",
            "category": "grammar",
            "status": "pending",
            "processed_module": {
                "module_json": {
                    "lessons": ["L1"],
                    "quizzes": ["Q1"],
                    "examples": ["E1"],
                },
                "difficulty": "beginner",
                "is_published": False,
            },
        }

    def test_get_status_message_is_available_without_api_key(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ok")

    def test_rejects_request_without_api_key(self):
        response = self.client.post(self.url, self.valid_payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_accepts_request_with_active_db_api_key(self):
        IngestAPIKey.objects.create(name="openclaw-main", key="valid-key", is_active=True)
        response = self.client.post(
            self.url,
            self.valid_payload,
            format="json",
            HTTP_X_API_KEY="valid-key",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(RawContent.objects.count(), 1)
        self.assertEqual(ProcessedModule.objects.count(), 1)

    def test_rejects_request_with_inactive_db_api_key(self):
        IngestAPIKey.objects.create(
            name="openclaw-old",
            key="inactive-key",
            is_active=False,
        )
        response = self.client.post(
            self.url,
            self.valid_payload,
            format="json",
            HTTP_X_API_KEY="inactive-key",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_rejects_invalid_payload(self):
        IngestAPIKey.objects.create(name="openclaw-main", key="valid-key", is_active=True)
        invalid_payload = self.valid_payload.copy()
        invalid_payload["source_url"] = "not-a-valid-url"

        response = self.client.post(
            self.url,
            invalid_payload,
            format="json",
            HTTP_X_API_KEY="valid-key",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

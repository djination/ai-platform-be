from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from . import chat_service
from .discovery import run_discover_and_ingest, search_candidate_urls

from .entitlements import effective_chat_daily_limit, effective_content_daily_limit, effective_plan_label
from .models import (
    ChatMessage,
    ChatReplyCache,
    ChatRoutingAudit,
    ChatSession,
    EnrichmentJob,
    IngestAPIKey,
    LearnerEntitlement,
    ProcessedModule,
    RawContent,
)


class RawContentIngestViewSetTests(APITestCase):
    url = "/api/content-engine/ingest/"

    def setUp(self):
        self.valid_payload = {
            "source_url": "https://example.com/content-1",
            "title": "Test Content",
            "raw_text": "Sample raw text",
            "category": "grammar",
            "language_code": "en",
            "metadata": {"source": "test-suite"},
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
        self.assertEqual(response.json()["status"], "ok")

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
        self.assertEqual(RawContent.objects.first().language_code, "en")
        self.assertGreaterEqual(EnrichmentJob.objects.count(), 1)

    def test_ingest_enriches_metadata_and_queues_jobs(self):
        IngestAPIKey.objects.create(name="openclaw-main", key="valid-key", is_active=True)
        response = self.client.post(
            self.url,
            self.valid_payload,
            format="json",
            HTTP_X_API_KEY="valid-key",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        raw = RawContent.objects.first()
        self.assertIn("content_hash", raw.metadata)
        self.assertIn("quality", raw.metadata)
        self.assertIn("safety", raw.metadata)
        self.assertEqual(EnrichmentJob.objects.filter(raw_content=raw).count(), 3)

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

    def test_rejects_invalid_json_payload(self):
        IngestAPIKey.objects.create(name="openclaw-main", key="valid-key", is_active=True)
        response = self.client.post(
            self.url,
            data="{invalid-json}",
            content_type="application/json",
            HTTP_X_API_KEY="valid-key",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_ingest_without_processed_module_creates_only_raw_content(self):
        IngestAPIKey.objects.create(name="openclaw-main", key="valid-key", is_active=True)
        payload = {
            "source_url": "https://example.com/raw-only",
            "title": "Raw Only",
            "raw_text": "Body text",
            "category": "grammar",
            "language_code": "en",
        }
        response = self.client.post(
            self.url,
            payload,
            format="json",
            HTTP_X_API_KEY="valid-key",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(RawContent.objects.count(), 1)
        self.assertEqual(ProcessedModule.objects.count(), 0)

    def test_ingest_admin_style_lesson_module_json(self):
        IngestAPIKey.objects.create(name="openclaw-main", key="valid-key", is_active=True)
        payload = {
            "source_url": "https://example.com/admin-lesson",
            "title": "Lesson Title",
            "raw_text": "Lesson body paragraph.",
            "category": "grammar",
            "language_code": "en",
            "processed_module": {
                "module_json": {
                    "title": "Lesson Title",
                    "lessonContent": "Lesson body paragraph.",
                    "quiz": [],
                },
                "difficulty": "intermediate",
                "is_published": False,
            },
        }
        response = self.client.post(
            self.url,
            payload,
            format="json",
            HTTP_X_API_KEY="valid-key",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        pm = ProcessedModule.objects.first()
        self.assertEqual(pm.difficulty, "intermediate")
        self.assertFalse(pm.is_published)
        self.assertEqual(pm.review_status, ProcessedModule.ReviewStatus.DRAFT)
        self.assertEqual(pm.module_json.get("lessonContent"), "Lesson body paragraph.")

    @override_settings(
        REST_FRAMEWORK={
            "DEFAULT_AUTHENTICATION_CLASSES": (
                "rest_framework_simplejwt.authentication.JWTAuthentication",
            ),
            "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle"],
            "DEFAULT_THROTTLE_RATES": {"openclaw_ingest": "2/minute"},
        }
    )
    def test_throttles_when_api_key_exceeds_rate_limit(self):
        IngestAPIKey.objects.create(name="openclaw-main", key="rate-key", is_active=True)
        for _ in range(2):
            response = self.client.post(
                self.url,
                self.valid_payload,
                format="json",
                HTTP_X_API_KEY="rate-key",
            )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        throttled_response = self.client.post(
            self.url,
            self.valid_payload,
            format="json",
            HTTP_X_API_KEY="rate-key",
        )
        self.assertEqual(throttled_response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class AdminAuthorizationTests(APITestCase):
    raw_content_url = "/api/content-engine/admin/raw-content/"
    processed_modules_url = "/api/content-engine/admin/processed-modules/"
    discover_url = "/api/content-engine/admin/discover-ingest/"

    def setUp(self):
        self.user_model = get_user_model()
        self.content_manager_group = Group.objects.create(name="content_manager")
        self.admin_user = self.user_model.objects.create_user(
            username="admin",
            password="secret123",
            is_staff=True,
        )
        self.regular_user = self.user_model.objects.create_user(
            username="user",
            password="secret123",
            is_staff=False,
        )
        self.content_manager_user = self.user_model.objects.create_user(
            username="content-manager",
            password="secret123",
            is_staff=False,
        )
        self.content_manager_user.groups.add(self.content_manager_group)

    def test_admin_endpoint_rejects_regular_authenticated_user(self):
        self.client.force_authenticate(user=self.regular_user)
        response = self.client.get(self.raw_content_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_endpoint_allows_staff_user(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.raw_content_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_admin_endpoint_allows_content_manager_group_user(self):
        self.client.force_authenticate(user=self.content_manager_user)
        response = self.client.get(self.raw_content_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_admin_endpoint_rejects_anonymous_user(self):
        response = self.client.get(self.raw_content_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_processed_modules_endpoint_rejects_regular_user(self):
        self.client.force_authenticate(user=self.regular_user)
        response = self.client.get(self.processed_modules_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch("content_engine.views.run_discover_and_ingest")
    def test_discover_ingest_rejects_regular_user(self, mock_run):
        self.client.force_authenticate(user=self.regular_user)
        response = self.client.post(
            self.discover_url,
            {"query": "hello world topic", "category": "grammar", "max_results": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        mock_run.assert_not_called()

    @patch("content_engine.views.run_discover_and_ingest")
    def test_discover_ingest_allows_staff(self, mock_run):
        mock_run.return_value = {
            "query": "hello world topic",
            "created": [],
            "skipped": [],
            "failed": [],
            "candidates_found": 0,
            "search_backend": "duckduckgo",
            "requested_max": 1,
        }
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(
            self.discover_url,
            {"query": "hello world topic", "category": "grammar", "max_results": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_run.assert_called_once()

    def test_admin_processed_modules_list_filters_by_language_and_publish_status(self):
        raw_en = RawContent.objects.create(
            title="List En",
            source_url="https://example.com/list-en",
            raw_text="x",
            category="grammar",
            language_code="en",
            metadata={},
        )
        raw_id = RawContent.objects.create(
            title="List Id",
            source_url="https://example.com/list-id",
            raw_text="y",
            category="grammar",
            language_code="id",
            metadata={},
        )
        ProcessedModule.objects.create(
            raw_content=raw_en,
            module_json={"title": "MEn"},
            difficulty="beginner",
            is_published=True,
        )
        ProcessedModule.objects.create(
            raw_content=raw_id,
            module_json={"title": "MId"},
            difficulty="beginner",
            is_published=False,
        )
        self.client.force_authenticate(user=self.admin_user)
        full = self.client.get(self.processed_modules_url)
        self.assertEqual(len(full.json()), 2)
        id_rows = self.client.get(f"{self.processed_modules_url}?language=id")
        self.assertEqual(len(id_rows.json()), 1)
        self.assertEqual(id_rows.json()[0]["language_code"], "id")
        pub_rows = self.client.get(f"{self.processed_modules_url}?is_published=true")
        self.assertEqual(len(pub_rows.json()), 1)
        self.assertTrue(pub_rows.json()[0]["is_published"])

    def test_admin_processed_module_detail_supports_review_action_and_notes(self):
        raw = RawContent.objects.create(
            title="Admin Review Source",
            source_url="https://example.com/review-source",
            raw_text="text",
            category="grammar",
            language_code="en",
            metadata={},
        )
        module = ProcessedModule.objects.create(
            raw_content=raw,
            module_json={"title": "Review Module"},
            difficulty="beginner",
            is_published=False,
        )
        detail_url = f"/api/content-engine/admin/processed-modules/{module.id}/"
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.patch(
            detail_url,
            {
                "is_published": True,
                "review_action": "approve",
                "review_notes": "Layak publish",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        module.refresh_from_db()
        self.assertEqual(module.review_status, ProcessedModule.ReviewStatus.REVIEWED)
        self.assertTrue(module.is_published)
        self.assertEqual(module.review_notes, "Layak publish")


class AdminRawContentPromoteTests(APITestCase):
    list_url = "/api/content-engine/admin/raw-content/"

    def setUp(self):
        user_model = get_user_model()
        self.admin_user = user_model.objects.create_user(
            username="raw-promote-admin",
            password="secret123",
            is_staff=True,
        )
        self.raw = RawContent.objects.create(
            title="Promote Me",
            source_url="https://example.com/promote",
            raw_text="Lesson body",
            category="grammar",
            language_code="en",
            metadata={},
        )

    def test_list_includes_processed_module_count(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.json()
        self.assertEqual(len(rows), 1)
        self.assertIn("processed_module_count", rows[0])
        self.assertEqual(rows[0]["processed_module_count"], 0)

    def test_list_filters_by_language_code(self):
        RawContent.objects.create(
            title="Bahasa Indonesia",
            source_url="https://example.com/id-row",
            raw_text="isi",
            category="grammar",
            language_code="id",
            metadata={},
        )
        self.client.force_authenticate(user=self.admin_user)
        all_rows = self.client.get(self.list_url)
        self.assertEqual(len(all_rows.json()), 2)
        id_rows = self.client.get(f"{self.list_url}?language_code=id")
        self.assertEqual(len(id_rows.json()), 1)
        self.assertEqual(id_rows.json()[0]["language_code"], "id")

    def test_list_filters_by_category_learning_path(self):
        RawContent.objects.create(
            title="LP6 row",
            source_url="https://example.com/lp6",
            raw_text="body",
            category="lp6-cross-cultural-communication",
            language_code="en",
            metadata={},
        )
        self.client.force_authenticate(user=self.admin_user)
        lp6 = self.client.get(f"{self.list_url}?category=lp6-cross-cultural-communication")
        self.assertEqual(len(lp6.json()), 1)
        self.assertEqual(lp6.json()[0]["category"], "lp6-cross-cultural-communication")
        alias = self.client.get(f"{self.list_url}?learning_path=lp6-cross-cultural-communication")
        self.assertEqual(len(alias.json()), 1)

    def test_get_raw_detail_includes_raw_text(self):
        url = f"/api/content-engine/admin/raw-content/{self.raw.id}/"
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["raw_text"], "Lesson body")

    def test_patch_raw_content(self):
        url = f"/api/content-engine/admin/raw-content/{self.raw.id}/"
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.patch(
            url,
            {"title": "Updated", "raw_text": "New body"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.raw.refresh_from_db()
        self.assertEqual(self.raw.title, "Updated")
        self.assertEqual(self.raw.raw_text, "New body")

    def test_post_draft_module_from_raw(self):
        url = f"/api/content-engine/admin/raw-content/{self.raw.id}/draft-module/"
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(url, {"difficulty": "advanced"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        pm = ProcessedModule.objects.get(raw_content=self.raw)
        self.assertEqual(pm.difficulty, "advanced")
        self.assertFalse(pm.is_published)
        self.assertEqual(pm.review_status, ProcessedModule.ReviewStatus.DRAFT)

    def test_post_draft_module_conflict_when_module_exists(self):
        ProcessedModule.objects.create(
            raw_content=self.raw,
            module_json={"title": "Existing", "lessonContent": "x", "quiz": []},
            difficulty="beginner",
            is_published=False,
        )
        url = f"/api/content-engine/admin/raw-content/{self.raw.id}/draft-module/"
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)


class ModuleViewPublicationTests(APITestCase):
    module_url = "/api/content-engine/module/"
    published_modules_url = "/api/content-engine/modules/published/"
    admin_module_detail_template = "/api/content-engine/admin/processed-modules/{module_id}/"

    def setUp(self):
        cache.clear()

    def _create_raw_content(self, title, language_code="en"):
        return RawContent.objects.create(
            title=title,
            source_url=f"https://example.com/{title.lower().replace(' ', '-')}",
            raw_text=f"{title} raw text",
            category="grammar",
            language_code=language_code,
            metadata={"seed": title},
        )

    def test_module_view_returns_default_when_no_published_module(self):
        response = self.client.get(self.module_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertIn("Belum ada materi publish", payload["lessonContent"])
        self.assertIsNone(payload.get("id"))
        self.assertIsNone(payload.get("difficulty"))
        self.assertIsNone(payload.get("language_code"))
        self.assertIsNone(payload.get("locale"))

    def test_module_view_uses_latest_published_processed_module(self):
        older_raw = self._create_raw_content("Old Published")
        latest_raw = self._create_raw_content("Latest Published")

        ProcessedModule.objects.create(
            raw_content=older_raw,
            module_json={
                "title": "Old Module",
                "lessonContent": "Old lesson",
                "quiz": [{"question": "Q old", "options": ["A"], "correctOptionIndex": 0}],
            },
            difficulty="beginner",
            is_published=True,
        )
        ProcessedModule.objects.create(
            raw_content=latest_raw,
            module_json={
                "title": "Latest Module",
                "lessonContent": "Latest lesson",
                "quiz": [{"question": "Q latest", "options": ["A"], "correctOptionIndex": 0}],
            },
            difficulty="beginner",
            is_published=True,
        )
        ProcessedModule.objects.create(
            raw_content=latest_raw,
            module_json={"title": "Draft Module", "lessonContent": "Draft", "quiz": []},
            difficulty="beginner",
            is_published=False,
        )

        response = self.client.get(self.module_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(payload["title"], "Latest Module")
        self.assertEqual(payload["lessonContent"], "Latest lesson")
        self.assertEqual(payload["quiz"][0]["question"], "Q latest")
        self.assertEqual(payload.get("language_code"), "en")
        latest = ProcessedModule.objects.filter(is_published=True).order_by("-id").first()
        self.assertEqual(payload["id"], latest.id)
        self.assertEqual(payload["difficulty"], latest.difficulty)

    def test_published_module_detail_returns_published_module(self):
        raw = self._create_raw_content("Detail Module")
        pm = ProcessedModule.objects.create(
            raw_content=raw,
            module_json={
                "title": "Detail Title",
                "lessonContent": "Detail lesson body",
                "quiz": [{"question": "Q1", "options": ["a"], "correctOptionIndex": 0}],
            },
            difficulty="beginner",
            is_published=True,
        )
        url = f"/api/content-engine/modules/{pm.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["id"], pm.id)
        self.assertEqual(data["difficulty"], pm.difficulty)
        self.assertEqual(data["title"], "Detail Title")
        self.assertEqual(data["lessonContent"], "Detail lesson body")
        self.assertEqual(data["quiz"][0]["question"], "Q1")
        self.assertEqual(data.get("language_code"), "en")

    def test_published_module_detail_404_when_not_published(self):
        raw = self._create_raw_content("Draft Detail")
        pm = ProcessedModule.objects.create(
            raw_content=raw,
            module_json={"title": "Draft"},
            difficulty="beginner",
            is_published=False,
        )
        url = f"/api/content-engine/modules/{pm.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_published_modules_endpoint_lists_only_published_modules(self):
        raw_published = self._create_raw_content("Published One")
        raw_unpublished = self._create_raw_content("Draft One")
        ProcessedModule.objects.create(
            raw_content=raw_published,
            module_json={"title": "Published One"},
            difficulty="beginner",
            is_published=True,
        )
        ProcessedModule.objects.create(
            raw_content=raw_unpublished,
            module_json={"title": "Draft One"},
            difficulty="beginner",
            is_published=False,
        )

        response = self.client.get(self.published_modules_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        items = response.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Published One")
        self.assertEqual(items[0].get("language_code"), "en")

    def test_module_view_filters_by_language_returns_matching_latest(self):
        raw_en = self._create_raw_content("English Latest", language_code="en")
        raw_id = self._create_raw_content("Indo Latest", language_code="id")
        ProcessedModule.objects.create(
            raw_content=raw_en,
            module_json={
                "title": "English Lesson",
                "lessonContent": "En body",
                "quiz": [],
            },
            difficulty="beginner",
            is_published=True,
        )
        ProcessedModule.objects.create(
            raw_content=raw_id,
            module_json={
                "title": "Indo Lesson",
                "lessonContent": "Id body",
                "quiz": [],
            },
            difficulty="beginner",
            is_published=True,
        )

        all_langs = self.client.get(self.module_url)
        self.assertEqual(all_langs.status_code, status.HTTP_200_OK)
        self.assertEqual(all_langs.json()["title"], "Indo Lesson")

        en_only = self.client.get(f"{self.module_url}?language=en")
        self.assertEqual(en_only.status_code, status.HTTP_200_OK)
        self.assertEqual(en_only.json()["title"], "English Lesson")
        self.assertEqual(en_only.json()["language_code"], "en")

        id_via_lang_param = self.client.get(f"{self.module_url}?lang=id")
        self.assertEqual(id_via_lang_param.status_code, status.HTTP_200_OK)
        self.assertEqual(id_via_lang_param.json()["title"], "Indo Lesson")

    def test_module_view_language_filter_no_match_returns_empty_payload(self):
        raw_id = self._create_raw_content("Only Indonesian", language_code="id")
        ProcessedModule.objects.create(
            raw_content=raw_id,
            module_json={"title": "Hanya ID", "lessonContent": "...", "quiz": []},
            difficulty="beginner",
            is_published=True,
        )
        response = self.client.get(f"{self.module_url}?language=en")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertIn("Belum ada materi publish", body["lessonContent"])
        self.assertIsNone(body.get("language_code"))

    def test_published_modules_view_respects_language_filter(self):
        raw_en = self._create_raw_content("Pub En", language_code="en")
        raw_id = self._create_raw_content("Pub Id", language_code="id")
        ProcessedModule.objects.create(
            raw_content=raw_en,
            module_json={"title": "E1"},
            difficulty="beginner",
            is_published=True,
        )
        ProcessedModule.objects.create(
            raw_content=raw_id,
            module_json={"title": "I1"},
            difficulty="beginner",
            is_published=True,
        )

        all_items = self.client.get(self.published_modules_url)
        self.assertEqual(len(all_items.json()["items"]), 2)

        en_items = self.client.get(f"{self.published_modules_url}?language=en")
        titles = [row["title"] for row in en_items.json()["items"]]
        self.assertEqual(titles, ["E1"])

    def test_published_module_detail_404_when_language_filter_mismatch(self):
        raw = self._create_raw_content("Mismatch", language_code="en")
        pm = ProcessedModule.objects.create(
            raw_content=raw,
            module_json={"title": "En only", "lessonContent": "x", "quiz": []},
            difficulty="beginner",
            is_published=True,
        )
        url = f"/api/content-engine/modules/{pm.id}/?language=id"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_language_filter_en_matches_en_us_raw_tag(self):
        raw = self._create_raw_content("Tag US", language_code="en-us")
        ProcessedModule.objects.create(
            raw_content=raw,
            module_json={"title": "US Tag Lesson", "lessonContent": "x", "quiz": []},
            difficulty="beginner",
            is_published=True,
        )
        response = self.client.get(f"{self.module_url}?language=en")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["title"], "US Tag Lesson")
        self.assertEqual(response.json()["language_code"], "en-us")

    def test_publish_unpublish_changes_are_reflected_in_learner_endpoints(self):
        user_model = get_user_model()
        admin_user = user_model.objects.create_user(
            username="publish-admin",
            password="secret123",
            is_staff=True,
        )
        raw = self._create_raw_content("Toggle Module")
        module = ProcessedModule.objects.create(
            raw_content=raw,
            module_json={"title": "Toggle Module", "lessonContent": "Visible when published"},
            difficulty="beginner",
            is_published=False,
        )

        before_publish = self.client.get(self.module_url)
        self.assertIn("Belum ada materi publish", before_publish.json()["lessonContent"])

        self.client.force_authenticate(user=admin_user)
        detail_url = self.admin_module_detail_template.format(module_id=module.id)
        publish_response = self.client.patch(detail_url, {"is_published": True}, format="json")
        self.assertEqual(publish_response.status_code, status.HTTP_200_OK)
        self.client.force_authenticate(user=None)

        after_publish = self.client.get(self.module_url)
        self.assertEqual(after_publish.status_code, status.HTTP_200_OK)
        self.assertEqual(after_publish.json()["title"], "Toggle Module")

        listed = self.client.get(self.published_modules_url)
        self.assertEqual(len(listed.json()["items"]), 1)

        self.client.force_authenticate(user=admin_user)
        unpublish_response = self.client.patch(detail_url, {"is_published": False}, format="json")
        self.assertEqual(unpublish_response.status_code, status.HTTP_200_OK)
        self.client.force_authenticate(user=None)

        after_unpublish = self.client.get(self.module_url)
        self.assertIn("Belum ada materi publish", after_unpublish.json()["lessonContent"])
        listed_after_unpublish = self.client.get(self.published_modules_url)
        self.assertEqual(len(listed_after_unpublish.json()["items"]), 0)

    @override_settings(CONTENT_DAILY_LIMIT=1)
    def test_free_content_daily_limit_applies_for_authenticated_user(self):
        learner = get_user_model().objects.create_user(username="content-free", password="secret123")
        raw = self._create_raw_content("Daily Limit Free")
        ProcessedModule.objects.create(
            raw_content=raw,
            module_json={"title": "Limited", "lessonContent": "x", "quiz": []},
            difficulty="beginner",
            is_published=True,
        )

        self.client.login(username="content-free", password="secret123")
        first = self.client.get(self.module_url)
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        second = self.client.get(self.module_url)
        self.assertEqual(second.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(second.json().get("code"), "content_daily_limit")
        self.assertTrue(second.json().get("upgrade_available"))

    @override_settings(CONTENT_DAILY_LIMIT=1, CONTENT_PLUS_DAILY_LIMIT=3)
    def test_plus_plan_uses_higher_content_daily_limit(self):
        learner = get_user_model().objects.create_user(username="content-plus", password="secret123")
        ent = LearnerEntitlement.objects.create(
            user=learner,
            plan=LearnerEntitlement.Plan.PLUS,
            payment_status=LearnerEntitlement.PaymentStatus.ACTIVE,
            pro_access_until=timezone.now() + timedelta(days=30),
        )
        self.assertEqual(effective_content_daily_limit(learner), 3)
        self.assertEqual(ent.plan, LearnerEntitlement.Plan.PLUS)

        raw = self._create_raw_content("Daily Limit Plus")
        ProcessedModule.objects.create(
            raw_content=raw,
            module_json={"title": "Plus Module", "lessonContent": "x", "quiz": []},
            difficulty="beginner",
            is_published=True,
        )

        self.client.login(username="content-plus", password="secret123")
        self.assertEqual(self.client.get(self.module_url).status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get(self.module_url).status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get(self.module_url).status_code, status.HTTP_200_OK)
        blocked = self.client.get(self.module_url)
        self.assertEqual(blocked.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertNotIn("upgrade_available", blocked.json())


class ChatEndpointTests(APITestCase):
    url = "/api/content-engine/chat/"

    def setUp(self):
        cache.clear()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="chat-learner", password="secret123")

    def test_requires_authentication(self):
        response = self.client.post(self.url, {"message": "Hello"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_rejects_empty_message(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.url, {"message": ""}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_tutor_route_for_learning_question(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.url, {"message": "Explain past simple tense"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["route"], "tutor")
        self.assertTrue(data.get("reply"))
        self.assertTrue(data.get("session_key"))

    def test_support_route_for_billing_question(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            self.url,
            {"message": "I want a refund on my subscription"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["route"], "support")

    def test_blocked_input_returns_blocked_route(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.url, {"message": "how to make a bomb"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["route"], "blocked")
        self.assertEqual(ChatMessage.objects.count(), 0)

    def test_persists_session_and_messages(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.url, {"message": "Hello tutor"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        session_key = response.json()["session_key"]
        self.assertEqual(ChatSession.objects.count(), 1)
        self.assertEqual(ChatMessage.objects.count(), 2)
        self.assertEqual(ChatRoutingAudit.objects.filter(user=self.user).count(), 1)

        follow_up = self.client.post(
            self.url,
            {"message": "Follow-up question", "session_key": session_key},
            format="json",
        )
        self.assertEqual(follow_up.status_code, status.HTTP_200_OK)
        self.assertEqual(follow_up.json()["session_key"], session_key)
        self.assertEqual(ChatSession.objects.count(), 1)
        self.assertEqual(ChatMessage.objects.count(), 4)

    @patch("content_engine.views.call_llm")
    def test_reuses_cached_reply_for_same_first_turn_message(self, mock_llm):
        mock_llm.return_value = ("Assistant reply unique xyz123", {"provider": "openrouter", "tokens": 10})
        self.client.force_authenticate(user=self.user)
        r1 = self.client.post(self.url, {"message": "Explain past simple tense"})
        r2 = self.client.post(self.url, {"message": "explain   past SIMPLE tense"})
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_llm.call_count, 1)
        self.assertEqual(r1.json()["reply"], r2.json()["reply"])
        row = ChatReplyCache.objects.get()
        self.assertEqual(row.hit_count, 1)
        self.assertEqual(row.route, "tutor")

    @patch("content_engine.views.call_llm")
    def test_does_not_use_cache_when_session_has_history(self, mock_llm):
        mock_llm.return_value = ("Reply", {"provider": "openrouter", "tokens": 1})
        self.client.force_authenticate(user=self.user)
        first = self.client.post(self.url, {"message": "Hello there"})
        sk = first.json()["session_key"]
        self.client.post(self.url, {"message": "Follow-up", "session_key": sk})
        mock_llm.reset_mock()
        self.client.post(self.url, {"message": "Hello there", "session_key": sk})
        self.assertEqual(mock_llm.call_count, 1)

    @override_settings(CHAT_REPLY_CACHE_ENABLED=False)
    @patch("content_engine.views.call_llm")
    def test_cache_disabled_always_calls_llm(self, mock_llm):
        mock_llm.return_value = ("R", {})
        self.client.force_authenticate(user=self.user)
        self.client.post(self.url, {"message": "One"})
        self.client.post(self.url, {"message": "One"})
        self.assertEqual(mock_llm.call_count, 2)
        self.assertEqual(ChatReplyCache.objects.count(), 0)

    @override_settings(CHAT_DAILY_MESSAGE_LIMIT=1)
    def test_daily_limit_returns_429(self):
        self.client.force_authenticate(user=self.user)
        first = self.client.post(self.url, {"message": "First"}, format="json")
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        second = self.client.post(self.url, {"message": "Second"}, format="json")
        self.assertEqual(second.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        body = second.json()
        self.assertEqual(body.get("code"), "chat_daily_limit")
        self.assertTrue(body.get("upgrade_available"))

    @override_settings(
        CHAT_DAILY_MESSAGE_LIMIT=1,
        CHAT_PRO_DAILY_MESSAGE_LIMIT=3,
        CHAT_THROTTLE_RATE="200/hour",
    )
    def test_pro_user_uses_higher_daily_cap(self):
        LearnerEntitlement.objects.create(
            user=self.user,
            plan=LearnerEntitlement.Plan.PRO,
            payment_status=LearnerEntitlement.PaymentStatus.ACTIVE,
            pro_access_until=timezone.now() + timedelta(days=1),
        )
        self.client.force_authenticate(user=self.user)
        for i in range(3):
            r = self.client.post(self.url, {"message": f"Msg{i}"}, format="json")
            self.assertEqual(r.status_code, status.HTTP_200_OK, msg=r.content)
        fourth = self.client.post(self.url, {"message": "Over"}, format="json")
        self.assertEqual(fourth.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertNotIn("upgrade_available", fourth.json())

    @override_settings(
        CHAT_DAILY_MESSAGE_LIMIT=1,
        CHAT_PRO_DAILY_MESSAGE_LIMIT=0,
        CHAT_THROTTLE_RATE="200/hour",
    )
    def test_pro_unlimited_daily_cap(self):
        LearnerEntitlement.objects.create(
            user=self.user,
            plan=LearnerEntitlement.Plan.PRO,
            payment_status=LearnerEntitlement.PaymentStatus.ACTIVE,
            pro_access_until=timezone.now() + timedelta(days=1),
        )
        self.client.force_authenticate(user=self.user)
        for i in range(5):
            r = self.client.post(self.url, {"message": f"U{i}"}, format="json")
            self.assertEqual(r.status_code, status.HTTP_200_OK, msg=r.content)


class EntitlementHelperTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="ent-user", password="x")

    @override_settings(CHAT_DAILY_MESSAGE_LIMIT=10, CHAT_PRO_DAILY_MESSAGE_LIMIT=99)
    def test_pro_gets_pro_cap(self):
        LearnerEntitlement.objects.create(
            user=self.user,
            plan=LearnerEntitlement.Plan.PRO,
            payment_status=LearnerEntitlement.PaymentStatus.ACTIVE,
            pro_access_until=timezone.now() + timedelta(hours=1),
        )
        self.assertEqual(effective_chat_daily_limit(self.user), 99)
        self.assertEqual(effective_plan_label(self.user), "pro")

    @override_settings(CHAT_DAILY_MESSAGE_LIMIT=10, CHAT_GO_DAILY_MESSAGE_LIMIT=55)
    def test_go_gets_go_cap(self):
        LearnerEntitlement.objects.create(
            user=self.user,
            plan=LearnerEntitlement.Plan.GO,
            payment_status=LearnerEntitlement.PaymentStatus.ACTIVE,
            pro_access_until=timezone.now() + timedelta(hours=1),
        )
        self.assertEqual(effective_chat_daily_limit(self.user), 55)
        self.assertEqual(effective_plan_label(self.user), "go")

    @override_settings(CHAT_DAILY_MESSAGE_LIMIT=10, CHAT_PRO_DAILY_MESSAGE_LIMIT=99)
    def test_expired_pro_falls_back_to_free_cap(self):
        LearnerEntitlement.objects.create(
            user=self.user,
            plan=LearnerEntitlement.Plan.PRO,
            payment_status=LearnerEntitlement.PaymentStatus.ACTIVE,
            pro_access_until=timezone.now() - timedelta(hours=1),
        )
        self.assertEqual(effective_chat_daily_limit(self.user), 10)
        self.assertEqual(effective_plan_label(self.user), "free")


class MeEntitlementAPITests(APITestCase):
    url = "/api/content-engine/me/entitlement/"

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="me-ent", password="secret123")

    def test_requires_authentication(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_free_plan_payload(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        data = r.json()
        self.assertEqual(data["plan"], "free")
        self.assertIsNone(data["pro_access_until"])
        self.assertEqual(data["payment_status"], "none")
        self.assertIn("demo_payment_enabled", data)


class MeLimitsAPITests(APITestCase):
    url = "/api/content-engine/me/limits/"
    chat_url = "/api/content-engine/chat/"
    module_url = "/api/content-engine/module/"

    def setUp(self):
        cache.clear()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="me-limits", password="secret123")
        raw = RawContent.objects.create(
            title="Limits Module",
            source_url="https://example.com/limits",
            raw_text="Limits module text",
            category="grammar",
            language_code="en",
            metadata={},
        )
        ProcessedModule.objects.create(
            raw_content=raw,
            module_json={"title": "Limits", "lessonContent": "x", "quiz": []},
            difficulty="beginner",
            is_published=True,
        )

    def test_requires_authentication(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)

    @override_settings(CHAT_DAILY_MESSAGE_LIMIT=2, CONTENT_DAILY_LIMIT=3)
    def test_returns_used_remaining_for_free_user(self):
        self.client.force_authenticate(user=self.user)
        self.client.post(self.chat_url, {"message": "hello"}, format="json")
        day = timezone.now().date().isoformat()
        cache.set(f"content-daily:{self.user.pk}:{day}", 1, timeout=86400)

        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        data = r.json()
        self.assertEqual(data["plan"], "free")
        self.assertEqual(data["chat"]["limit"], 2)
        self.assertEqual(data["chat"]["used"], 1)
        self.assertEqual(data["chat"]["remaining"], 1)
        self.assertEqual(data["content"]["limit"], 3)
        self.assertEqual(data["content"]["used"], 1)
        self.assertEqual(data["content"]["remaining"], 2)
        self.assertEqual(data["window"], "daily")
        self.assertEqual(data["timezone"], "UTC")
        self.assertIn("reset_at", data)

class DemoCompletePaymentTests(APITestCase):
    url = "/api/content-engine/billing/demo/complete-payment/"

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="demo-pay", password="secret123")

    @override_settings(BILLING_DEMO_PAYMENT_ENABLED=False)
    def test_disabled_returns_403(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.post(self.url, {"plan_code": "go"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(BILLING_DEMO_PAYMENT_ENABLED=True, BILLING_DEMO_SUBSCRIPTION_DAYS=7)
    def test_activates_from_pending_plan(self):
        ent = LearnerEntitlement.objects.create(
            user=self.user,
            plan=LearnerEntitlement.Plan.FREE,
            payment_status=LearnerEntitlement.PaymentStatus.PENDING,
            pending_plan_code="plus",
        )
        self.client.force_authenticate(user=self.user)
        r = self.client.post(self.url, {}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        ent.refresh_from_db()
        self.assertEqual(ent.plan, LearnerEntitlement.Plan.PLUS)
        self.assertEqual(ent.payment_status, LearnerEntitlement.PaymentStatus.ACTIVE)
        self.assertEqual(ent.pending_plan_code, "")
        self.assertIsNotNone(ent.pro_access_until)

    @override_settings(BILLING_DEMO_PAYMENT_ENABLED=True)
    def test_activates_from_body_plan_code(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.post(self.url, {"plan_code": "go"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        ent = LearnerEntitlement.objects.get(user=self.user)
        self.assertEqual(ent.plan, LearnerEntitlement.Plan.GO)


class BillingPlansAPITests(APITestCase):
    url = "/api/content-engine/billing/plans/"

    def test_lists_plans_anonymously(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        data = r.json()
        codes = [p["code"] for p in data["plans"]]
        self.assertEqual(codes, ["free", "go", "plus", "pro"])
        self.assertIn("demo_payment_enabled", data)


class RequestUpgradeAPITests(APITestCase):
    url = "/api/content-engine/billing/request-upgrade/"

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="upgrade-user", password="secret123")

    def test_requires_authentication(self):
        r = self.client.post(self.url, {"plan_code": "go"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_sets_pending_plan(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.post(self.url, {"plan_code": "plus"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        ent = LearnerEntitlement.objects.get(user=self.user)
        self.assertEqual(ent.pending_plan_code, "plus")
        self.assertEqual(ent.payment_status, LearnerEntitlement.PaymentStatus.PENDING)

    def test_rejects_when_already_subscribed(self):
        LearnerEntitlement.objects.create(
            user=self.user,
            plan=LearnerEntitlement.Plan.PLUS,
            payment_status=LearnerEntitlement.PaymentStatus.ACTIVE,
            pro_access_until=timezone.now() + timedelta(days=1),
        )
        self.client.force_authenticate(user=self.user)
        r = self.client.post(self.url, {"plan_code": "pro"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)


class ChatReplyCacheUnitTests(SimpleTestCase):
    def test_lookup_hash_none_when_history_not_empty(self):
        h = chat_service.compute_chat_reply_cache_lookup_hash(
            route="tutor",
            mode="general",
            level="beginner",
            module_context="",
            message="Hi",
            ambiguous=False,
            history_empty=False,
        )
        self.assertIsNone(h)

    @override_settings(CHAT_REPLY_CACHE_ENABLED=True)
    def test_lookup_hash_normalizes_message(self):
        a = chat_service.compute_chat_reply_cache_lookup_hash(
            route="tutor",
            mode="general",
            level="beginner",
            module_context="",
            message="  Hello ",
            ambiguous=False,
            history_empty=True,
        )
        b = chat_service.compute_chat_reply_cache_lookup_hash(
            route="tutor",
            mode="general",
            level="beginner",
            module_context="",
            message="hello",
            ambiguous=False,
            history_empty=True,
        )
        self.assertEqual(a, b)

    @override_settings(CHAT_REPLY_CACHE_ENABLED=False)
    def test_lookup_hash_disabled_returns_none(self):
        h = chat_service.compute_chat_reply_cache_lookup_hash(
            route="tutor",
            mode="general",
            level="beginner",
            module_context="",
            message="Hi",
            ambiguous=False,
            history_empty=True,
        )
        self.assertIsNone(h)


class DiscoveryGoogleCSESearchTests(SimpleTestCase):
    @patch("content_engine.discovery.requests.get")
    def test_google_cse_returns_normalized_urls(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "items": [
                {"link": "https://example.com/g1", "title": "Article One"},
                {"link": "https://example.com/g2", "title": "Article Two"},
            ]
        }
        with override_settings(GOOGLE_CSE_API_KEY="test-key", GOOGLE_CSE_CX="test-cx"):
            out = search_candidate_urls("business english", 5, "google")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["url"], "https://example.com/g1")
        self.assertEqual(out[0]["snippet_title"], "Article One")
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        self.assertIn("customsearch/v1", args[0])


class DiscoveryPipelineTests(TestCase):
    @patch("content_engine.discovery.fetch_article_text")
    @patch("content_engine.discovery.search_candidate_urls")
    def test_run_discover_and_ingest_creates_raw(self, mock_search, mock_fetch):
        mock_search.return_value = [
            {"url": "https://example.com/disc-test-1", "snippet_title": "Snippet title"},
        ]
        mock_fetch.return_value = ("Extracted title", "word " * 80, None)
        report = run_discover_and_ingest(
            query="english negotiation phrases",
            max_results=3,
            category="lp4-test",
            queue_jobs=False,
        )
        self.assertNotIn("error", report)
        self.assertEqual(len(report["created"]), 1)
        self.assertEqual(
            RawContent.objects.filter(source_url="https://example.com/disc-test-1").count(),
            1,
        )

    @patch("content_engine.discovery.search_candidate_urls")
    def test_run_discover_short_query_skips_search(self, mock_search):
        report = run_discover_and_ingest(query="ab", max_results=5, category="x", queue_jobs=False)
        self.assertIn("error", report)
        mock_search.assert_not_called()


class SeedPilotIdContentCommandTests(TestCase):
    def test_dry_run_completes(self):
        out = StringIO()
        call_command("seed_pilot_id_content", "--dry-run", stdout=out)
        output = out.getvalue().lower()
        self.assertIn("dry run", output)


class IngestBulkFromJsonCommandTests(TestCase):
    def test_dry_run_reads_minimal_payloads(self):
        import json
        import tempfile
        from pathlib import Path

        payload = [
            {
                "title": "T",
                "source_url": "https://example.com/bulk-ingest-test",
                "raw_text": "Hello world.",
                "category": "test-category",
            }
        ]
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            json.dump(payload, tmp)
            tmp_path = tmp.name
        try:
            out = StringIO()
            call_command("ingest_bulk_from_json", tmp_path, "--dry-run", stdout=out)
            self.assertIn("dry run", out.getvalue().lower())
        finally:
            Path(tmp_path).unlink(missing_ok=True)


class ChatServiceUnitTests(SimpleTestCase):
    def test_classify_intent_support_for_refund(self):
        result = chat_service.classify_intent("How do I get a refund?")
        self.assertEqual(result["intent"], "support")

    def test_guard_input_blocks_harmful_pattern(self):
        ok, refusal = chat_service.guard_input("how to make a bomb")
        self.assertFalse(ok)
        self.assertTrue(refusal)

    def test_needs_human_handoff_sensitive_topics(self):
        self.assertTrue(chat_service.needs_human_handoff("I want to sue the company", 0.5))


@override_settings(RECAPTCHA_SECRET_KEY="")
class AuthLearnerRegisterTests(APITestCase):
    url = "/api/auth/register/"

    def test_register_creates_non_staff_user(self):
        response = self.client.post(
            self.url,
            {
                "username": "learner_new_1",
                "password": "strong-pass-99",
                "password_confirm": "strong-pass-99",
                "email": "learner@example.com",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body["username"], "learner_new_1")
        user = get_user_model().objects.get(username="learner_new_1")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.check_password("strong-pass-99"))

    def test_register_rejects_mismatched_passwords(self):
        response = self.client.post(
            self.url,
            {
                "username": "learner_new_2",
                "password": "strong-pass-99",
                "password_confirm": "other-pass-00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_rejects_duplicate_username(self):
        get_user_model().objects.create_user(username="dup_user", password="Existing-pass-88!")
        response = self.client.post(
            self.url,
            {
                "username": "dup_user",
                "password": "strong-pass-99",
                "password_confirm": "strong-pass-99",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(RECAPTCHA_SECRET_KEY="test-recaptcha-secret", RECAPTCHA_MIN_SCORE=0.5)
class AuthLearnerRegisterRecaptchaTests(APITestCase):
    url = "/api/auth/register/"

    def test_register_rejects_empty_recaptcha_token(self):
        response = self.client.post(
            self.url,
            {
                "username": "cap_user_0",
                "password": "strong-pass-99",
                "password_confirm": "strong-pass-99",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("core.auth_urls.verify_recaptcha_token")
    def test_register_requires_recaptcha_when_configured(self, mock_verify):
        mock_verify.return_value = (False, "bad token")
        response = self.client.post(
            self.url,
            {
                "username": "cap_user_1",
                "password": "strong-pass-99",
                "password_confirm": "strong-pass-99",
                "recaptcha_token": "tok",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.json())

    @patch("core.auth_urls.verify_recaptcha_token")
    def test_register_succeeds_with_valid_recaptcha(self, mock_verify):
        mock_verify.return_value = (True, "")
        response = self.client.post(
            self.url,
            {
                "username": "cap_user_2",
                "password": "strong-pass-99",
                "password_confirm": "strong-pass-99",
                "recaptcha_token": "tok",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_verify.assert_called_once()


class AuthLoginTokenTests(APITestCase):
    url = "/api/auth/token/"

    @override_settings(RECAPTCHA_SECRET_KEY="", RECAPTCHA_VERIFY_LOGIN=False)
    def test_obtain_pair_without_recaptcha_config(self):
        get_user_model().objects.create_user(username="tok_u1", password="Good-pass-99!")
        response = self.client.post(
            self.url,
            {"username": "tok_u1", "password": "Good-pass-99!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.json())

    @override_settings(RECAPTCHA_SECRET_KEY="sec", RECAPTCHA_VERIFY_LOGIN=True, RECAPTCHA_MIN_SCORE=0.5)
    @patch("core.auth_jwt_views.verify_recaptcha_token")
    def test_obtain_pair_rejects_without_token_when_login_verify_on(self, mock_verify):
        get_user_model().objects.create_user(username="tok_u2", password="Good-pass-99!")
        mock_verify.return_value = (False, "no")
        response = self.client.post(
            self.url,
            {"username": "tok_u2", "password": "Good-pass-99!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(RECAPTCHA_SECRET_KEY="sec", RECAPTCHA_VERIFY_LOGIN=True)
    @patch("core.auth_jwt_views.verify_recaptcha_token")
    def test_obtain_pair_accepts_token_when_login_verify_on(self, mock_verify):
        get_user_model().objects.create_user(username="tok_u3", password="Good-pass-99!")
        mock_verify.return_value = (True, "")
        response = self.client.post(
            self.url,
            {
                "username": "tok_u3",
                "password": "Good-pass-99!",
                "recaptcha_token": "x",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.json())


class RecaptchaUnitTests(SimpleTestCase):
    @patch("core.recaptcha.requests.post")
    def test_verify_accepts_v2_success(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"success": True}
        with override_settings(RECAPTCHA_SECRET_KEY="sec"):
            from core.recaptcha import verify_recaptcha_token

            ok, err = verify_recaptcha_token("t", remote_ip="127.0.0.1")
        self.assertTrue(ok)
        self.assertEqual(err, "")

    @patch("core.recaptcha.requests.post")
    def test_verify_rejects_low_v3_score(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"success": True, "score": 0.1}
        with override_settings(RECAPTCHA_SECRET_KEY="sec", RECAPTCHA_MIN_SCORE=0.5):
            from core.recaptcha import verify_recaptcha_token

            ok, err = verify_recaptcha_token("t", remote_ip=None)
        self.assertFalse(ok)
        self.assertTrue(err)

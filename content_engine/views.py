import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import ProcessedModule, RawContent


@csrf_exempt
def ingest_content(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    payload = json.loads(request.body)
    RawContent.objects.create(
        title=payload.get("title", ""),
        source_url=payload.get("source_url", ""),
        raw_text=payload.get("raw_text", ""),
        category=payload.get("category", ""),
    )

    return JsonResponse({"message": "Success"}, status=201)


def module_view(request):
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    latest_content = RawContent.objects.order_by("-created_at").first()

    if not latest_content:
        return JsonResponse(
            {
                "title": "English Module",
                "lessonContent": "Belum ada materi. Silakan ingest konten dulu.",
                "quiz": [],
            },
            status=200,
        )

    return JsonResponse(
        {
            "title": latest_content.title,
            "lessonContent": latest_content.raw_text,
            "quiz": [
                {
                    "question": "Apa kategori dari materi ini?",
                    "options": [
                        latest_content.category,
                        "Grammar Random",
                        "Uncategorized",
                        "Daily Story",
                    ],
                    "correctOptionIndex": 0,
                    "explanation": f"Kategori konten ini adalah '{latest_content.category}'.",
                }
            ],
        },
        status=200,
    )


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def admin_raw_content_list(request):
    raw_contents = RawContent.objects.order_by("-created_at")
    data = [
        {
            "id": item.id,
            "title": item.title,
            "source_url": item.source_url,
            "category": item.category,
            "created_at": item.created_at.isoformat(),
        }
        for item in raw_contents
    ]
    return Response(data, status=200)


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def admin_processed_modules_list(request):
    modules = ProcessedModule.objects.select_related("raw_content").order_by("-id")
    data = [
        {
            "id": item.id,
            "raw_content_id": item.raw_content_id,
            "raw_content_title": item.raw_content.title,
            "difficulty": item.difficulty,
            "is_published": item.is_published,
        }
        for item in modules
    ]
    return Response(data, status=200)


@csrf_exempt
@api_view(["PATCH"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def admin_processed_module_detail(request, module_id):
    try:
        payload = request.data
    except Exception:
        return Response({"error": "Invalid JSON payload"}, status=400)

    if "is_published" not in payload:
        return Response({"error": "Field 'is_published' is required"}, status=400)

    module = get_object_or_404(ProcessedModule, pk=module_id)
    module.is_published = bool(payload["is_published"])
    module.save(update_fields=["is_published"])

    return Response(
        {
            "id": module.id,
            "is_published": module.is_published,
            "message": "Publish status updated",
        },
        status=200,
    )

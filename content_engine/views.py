from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, F, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
import logging
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.exceptions import ParseError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.authentication import JWTAuthentication

from .discovery import run_discover_and_ingest
from .entitlements import user_has_paid_subscription
from .chat_service import (
    build_cs_system,
    build_tutor_system,
    call_llm,
    classify_intent,
    compute_chat_reply_cache_lookup_hash,
    guard_input,
    guard_output,
    needs_human_handoff,
    new_session_key,
    retrieve_kb_snippets,
    trim_history,
)
from .models import (
    ChatMessage,
    ChatReplyCache,
    ChatRoutingAudit,
    ChatSession,
    IngestAPIKey,
    ProcessedModule,
    RawContent,
)
from .pipeline import apply_post_ingest_metadata
from .permissions import IsAdminOrContentManager
from .serializers import RawContentIngestSerializer

logger = logging.getLogger(__name__)


def _language_q_tags(language_param: str, field_prefix: str) -> Q | None:
    """field_prefix '' => RawContent; 'raw_content__' => ProcessedModule join."""
    code = str(language_param).strip().lower().replace("_", "-")
    if not code:
        return None
    base = code.split("-")[0]
    lc = f"{field_prefix}language_code"
    return (
        Q(**{f"{lc}__iexact": code})
        | Q(**{f"{lc}__istartswith": f"{base}-"})
        | Q(**{f"{lc}__iexact": base})
    )


def _language_q_for_processed(language_param: str) -> Q | None:
    """Match RawContent.language_code via ProcessedModule.raw_content."""
    return _language_q_tags(language_param, "raw_content__")


def _language_q_for_raw(language_param: str) -> Q | None:
    """Match RawContent.language_code."""
    return _language_q_tags(language_param, "")


def _apply_language_to_published_modules(qs, request):
    lang = request.GET.get("language") or request.GET.get("lang")
    if not lang:
        return qs
    q = _language_q_for_processed(lang)
    return qs.filter(q) if q else qs


def _raw_matches_language_param(raw: RawContent, language_param: str) -> bool:
    if not language_param or not str(language_param).strip():
        return True
    code = str(language_param).strip().lower().replace("_", "-")
    base = code.split("-")[0]
    lc = (raw.language_code or "").strip().lower().replace("_", "-")
    if lc == code or lc == base:
        return True
    return lc.startswith(f"{base}-")


_CHAT_MAX_MESSAGE_LEN = 8000
_CHAT_MAX_MODULE_CONTEXT_LEN = 4000


def _chat_daily_cache_key(user_id):
    day = timezone.now().date().isoformat()
    return f"chat-daily:{user_id}:{day}"


def _content_daily_cache_key(user_id):
    day = timezone.now().date().isoformat()
    return f"content-daily:{user_id}:{day}"


def _check_and_increment_chat_daily(user) -> bool:
    from .entitlements import effective_chat_daily_limit

    limit = effective_chat_daily_limit(user)
    if limit is None:
        return True
    key = _chat_daily_cache_key(user.pk)
    current = cache.get(key, 0)
    if current >= limit:
        return False
    cache.set(key, current + 1, timeout=86400)
    return True


def _check_and_increment_content_daily(user) -> bool:
    from .entitlements import effective_content_daily_limit

    if not getattr(user, "is_authenticated", False):
        return True

    limit = effective_content_daily_limit(user)
    if limit is None:
        return True
    key = _content_daily_cache_key(user.pk)
    current = cache.get(key, 0)
    if current >= limit:
        return False
    cache.set(key, current + 1, timeout=86400)
    return True


def _content_limit_response(user):
    payload = {
        "error": "Daily content limit reached. Try again tomorrow.",
        "code": "content_daily_limit",
    }
    if getattr(user, "is_authenticated", False) and not user_has_paid_subscription(user):
        payload["upgrade_available"] = True
    return JsonResponse(payload, status=429)


def _ingest_rate_config():
    rate = settings.REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {}).get(
        "openclaw_ingest", "30/minute"
    )
    try:
        count_str, period = rate.split("/")
        limit = int(count_str)
    except (ValueError, TypeError):
        return 30, 60

    period = period.strip().lower()
    if period.startswith("sec"):
        return limit, 1
    if period.startswith("hour"):
        return limit, 3600
    if period.startswith("day"):
        return limit, 86400
    return limit, 60


def _is_ingest_throttled(api_key):
    limit, window_seconds = _ingest_rate_config()
    bucket = int(timezone.now().timestamp() // window_seconds)
    cache_key = f"ingest-rate:{api_key}:{bucket}"
    current = cache.get(cache_key, 0)
    if current >= limit:
        logger.warning("Ingest throttled for key=%s limit=%s window=%ss", api_key[:8], limit, window_seconds)
        return True
    cache.set(cache_key, current + 1, timeout=window_seconds)
    return False


@api_view(["GET", "POST"])
def ingest_content(request):
    if request.method == "GET":
        return Response({"status": "ok"}, status=status.HTTP_200_OK)

    api_key = request.headers.get("X-API-Key", "").strip()
    if not api_key:
        return Response({"error": "Missing API key"}, status=status.HTTP_403_FORBIDDEN)

    is_valid_db_key = IngestAPIKey.objects.filter(key=api_key, is_active=True).first()
    fallback_key = settings.OPENCLAW_API_KEY
    if not is_valid_db_key and api_key != fallback_key:
        return Response({"error": "Invalid API key"}, status=status.HTTP_403_FORBIDDEN)

    if is_valid_db_key:
        is_valid_db_key.last_used_at = timezone.now()
        is_valid_db_key.save(update_fields=["last_used_at"])

    if _is_ingest_throttled(api_key):
        return Response(
            {"error": "Request was throttled. Expected available in 60 seconds."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    try:
        payload = request.data
    except ParseError:
        return Response({"error": "Invalid JSON payload"}, status=status.HTTP_400_BAD_REQUEST)

    serializer = RawContentIngestSerializer(data=payload)
    if not serializer.is_valid():
        return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    raw_content = serializer.save()
    apply_post_ingest_metadata(raw_content, queue_jobs=True)
    return Response({"message": "Success"}, status=status.HTTP_201_CREATED)


def _learner_module_payload(processed_module: ProcessedModule) -> dict:
    raw_content = processed_module.raw_content
    module_json = processed_module.module_json or {}
    quiz = module_json.get("quiz")
    if not isinstance(quiz, list):
        quiz = []
    return {
        "id": processed_module.id,
        "difficulty": processed_module.difficulty,
        "language_code": raw_content.language_code,
        "locale": raw_content.locale or "",
        "title": module_json.get("title") or raw_content.title,
        "lessonContent": module_json.get("lessonContent") or raw_content.raw_text,
        "quiz": quiz,
    }


def module_view(request):
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    if not _check_and_increment_content_daily(request.user):
        return _content_limit_response(request.user)

    published_qs = ProcessedModule.objects.select_related("raw_content").filter(is_published=True)
    published_qs = _apply_language_to_published_modules(published_qs, request)
    latest_published_module = published_qs.order_by("-id").first()

    if not latest_published_module:
        return JsonResponse(
            {
                "id": None,
                "difficulty": None,
                "title": "English Module",
                "lessonContent": "Belum ada materi publish. Silakan ingest dan publish modul dulu.",
                "quiz": [],
                "language_code": None,
                "locale": None,
            },
            status=200,
        )

    return JsonResponse(_learner_module_payload(latest_published_module), status=200)


def published_module_detail_view(request, module_id):
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    if not _check_and_increment_content_daily(request.user):
        return _content_limit_response(request.user)

    processed_module = get_object_or_404(
        ProcessedModule.objects.select_related("raw_content"),
        pk=module_id,
        is_published=True,
    )
    lang = request.GET.get("language") or request.GET.get("lang")
    if lang and not _raw_matches_language_param(processed_module.raw_content, lang):
        return JsonResponse(
            {"error": "Module not available for this language filter."},
            status=404,
        )
    return JsonResponse(_learner_module_payload(processed_module), status=200)


def published_modules_view(request):
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    if not _check_and_increment_content_daily(request.user):
        return _content_limit_response(request.user)

    modules = ProcessedModule.objects.select_related("raw_content").filter(is_published=True)
    modules = _apply_language_to_published_modules(modules, request).order_by("-id")

    payload = []
    for module in modules:
        module_json = module.module_json or {}
        payload.append(
            {
                "id": module.id,
                "raw_content_id": module.raw_content_id,
                "title": module_json.get("title") or module.raw_content.title,
                "difficulty": module.difficulty,
                "language_code": module.raw_content.language_code,
                "locale": module.raw_content.locale or "",
            }
        )

    return JsonResponse({"items": payload}, status=200)


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminOrContentManager])
def admin_raw_content_list(request):
    raw_contents = RawContent.objects.annotate(processed_module_count=Count("processed_modules"))
    lang = request.GET.get("language_code") or request.GET.get("language")
    if lang:
        rq = _language_q_for_raw(lang)
        if rq:
            raw_contents = raw_contents.filter(rq)
    category_filter = (request.GET.get("category") or request.GET.get("learning_path") or "").strip()
    if category_filter:
        raw_contents = raw_contents.filter(category=category_filter)
    raw_contents = raw_contents.order_by("-created_at")
    data = [
        {
            "id": item.id,
            "title": item.title,
            "source_url": item.source_url,
            "category": item.category,
            "language_code": item.language_code,
            "locale": item.locale or "",
            "created_at": item.created_at.isoformat(),
            "processed_module_count": item.processed_module_count,
        }
        for item in raw_contents
    ]
    return Response(data, status=200)


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminOrContentManager])
@throttle_classes([ScopedRateThrottle])
def admin_discover_ingest(request):
    """Run search + article extraction + ingest (admin only)."""
    try:
        body = request.data
    except ParseError:
        return Response({"error": "Invalid JSON payload"}, status=status.HTTP_400_BAD_REQUEST)

    query = str(body.get("query") or "").strip()
    category = str(body.get("category") or "").strip()
    try:
        max_results = int(body.get("max_results", 10))
    except (TypeError, ValueError):
        max_results = 10

    language_code = str(body.get("language_code") or "en").strip()
    locale = str(body.get("locale") or "").strip()
    sb = str(body.get("search_backend") or "").strip().lower()
    search_backend = sb if sb in ("duckduckgo", "serpapi", "google", "google_cse") else None
    skip_enrichment = bool(body.get("skip_enrichment"))

    report = run_discover_and_ingest(
        query=query,
        max_results=max_results,
        category=category,
        language_code=language_code,
        locale=locale,
        search_backend=search_backend,
        queue_jobs=not skip_enrichment,
    )

    if report.get("error"):
        return Response(report, status=status.HTTP_400_BAD_REQUEST)
    return Response(report, status=status.HTTP_200_OK)


admin_discover_ingest.throttle_scope = "discover_ingest"


@api_view(["GET", "PATCH"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminOrContentManager])
def admin_raw_content_detail(request, raw_content_id):
    raw = get_object_or_404(RawContent, pk=raw_content_id)

    if request.method == "GET":
        return Response(
            {
                "id": raw.id,
                "title": raw.title,
                "source_url": raw.source_url,
                "category": raw.category,
                "raw_text": raw.raw_text,
                "language_code": raw.language_code,
                "locale": raw.locale or "",
                "metadata": raw.metadata,
                "created_at": raw.created_at.isoformat(),
                "processed_module_count": raw.processed_modules.count(),
            },
            status=status.HTTP_200_OK,
        )

    try:
        payload = request.data
    except Exception:
        return Response({"error": "Invalid JSON payload"}, status=400)

    update_fields = []
    if "title" in payload:
        raw.title = str(payload["title"]).strip()[:255]
        update_fields.append("title")
    if "raw_text" in payload:
        raw.raw_text = str(payload["raw_text"])
        update_fields.append("raw_text")
    if "category" in payload:
        raw.category = str(payload["category"]).strip()[:100]
        update_fields.append("category")
    if "source_url" in payload:
        raw.source_url = str(payload["source_url"]).strip()
        update_fields.append("source_url")
    if "language_code" in payload:
        lc = str(payload["language_code"]).strip().lower().replace("_", "-")[:10]
        if not lc:
            return Response({"error": "language_code cannot be empty"}, status=status.HTTP_400_BAD_REQUEST)
        raw.language_code = lc
        update_fields.append("language_code")
    if "locale" in payload:
        raw.locale = str(payload["locale"]).strip()[:32]
        update_fields.append("locale")

    if not update_fields:
        return Response(
            {
                "error": (
                    "Provide at least one of: title, raw_text, category, source_url, "
                    "language_code, locale"
                ),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    raw.save(update_fields=update_fields)
    return Response(
        {
            "id": raw.id,
            "title": raw.title,
            "source_url": raw.source_url,
            "category": raw.category,
            "language_code": raw.language_code,
            "locale": raw.locale or "",
            "message": "Raw content updated",
        },
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminOrContentManager])
def admin_raw_content_create_draft_module(request, raw_content_id):
    raw = get_object_or_404(RawContent, pk=raw_content_id)
    if raw.processed_modules.exists():
        return Response(
            {
                "error": "This raw content already has processed module(s).",
                "processed_module_ids": list(
                    raw.processed_modules.order_by("id").values_list("id", flat=True)
                ),
            },
            status=status.HTTP_409_CONFLICT,
        )

    try:
        payload = request.data
    except Exception:
        payload = {}

    difficulty = str(payload.get("difficulty", ProcessedModule.Difficulty.BEGINNER)).strip().lower()
    valid_diff = {c.value for c in ProcessedModule.Difficulty}
    if difficulty not in valid_diff:
        difficulty = ProcessedModule.Difficulty.BEGINNER

    module_json = {
        "title": raw.title,
        "lessonContent": raw.raw_text,
        "quiz": [],
    }
    overrides = payload.get("module_json")
    if isinstance(overrides, dict):
        if overrides.get("title"):
            module_json["title"] = str(overrides["title"]).strip()
        if "lessonContent" in overrides:
            module_json["lessonContent"] = str(overrides["lessonContent"])

    pm = ProcessedModule.objects.create(
        raw_content=raw,
        module_json=module_json,
        difficulty=difficulty,
        is_published=False,
        review_status=ProcessedModule.ReviewStatus.DRAFT,
    )

    apply_post_ingest_metadata(raw, queue_jobs=True)

    return Response(
        {
            "id": pm.id,
            "raw_content_id": raw.id,
            "difficulty": pm.difficulty,
            "review_status": pm.review_status,
            "is_published": pm.is_published,
            "message": "Draft processed module created",
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminOrContentManager])
def admin_processed_modules_list(request):
    modules = ProcessedModule.objects.select_related("raw_content").order_by("-id")
    lang = request.GET.get("language_code") or request.GET.get("language")
    if lang:
        lq = _language_q_for_processed(lang)
        if lq:
            modules = modules.filter(lq)
    pub = request.GET.get("is_published")
    if pub is not None and str(pub).strip() != "":
        key = str(pub).strip().lower()
        if key in ("true", "1", "yes"):
            modules = modules.filter(is_published=True)
        elif key in ("false", "0", "no"):
            modules = modules.filter(is_published=False)
    data = [
        {
            "id": item.id,
            "raw_content_id": item.raw_content_id,
            "raw_content_title": item.raw_content.title,
            "language_code": item.raw_content.language_code,
            "locale": item.raw_content.locale or "",
            "difficulty": item.difficulty,
            "review_status": item.review_status,
            "review_notes": item.review_notes,
            "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
            "is_published": item.is_published,
        }
        for item in modules
    ]
    return Response(data, status=200)


@api_view(["PATCH"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAdminOrContentManager])
def admin_processed_module_detail(request, module_id):
    try:
        payload = request.data
    except Exception:
        return Response({"error": "Invalid JSON payload"}, status=400)

    if "is_published" not in payload:
        return Response({"error": "Field 'is_published' is required"}, status=400)

    module = get_object_or_404(ProcessedModule, pk=module_id)
    update_fields = []

    if "review_action" in payload:
        action = str(payload["review_action"]).strip().lower()
        if action == "approve":
            module.review_status = ProcessedModule.ReviewStatus.REVIEWED
            module.reviewed_at = timezone.now()
        elif action == "reject":
            module.review_status = ProcessedModule.ReviewStatus.REJECTED
            module.is_published = False
            module.reviewed_at = timezone.now()
            update_fields.append("is_published")
        elif action == "reset":
            module.review_status = ProcessedModule.ReviewStatus.DRAFT
            module.reviewed_at = None
            module.is_published = False
            update_fields.extend(["reviewed_at", "is_published"])
        else:
            return Response(
                {"error": "Invalid review_action. Use approve, reject, or reset."},
                status=400,
            )
        update_fields.extend(["review_status", "reviewed_at"])

    if "review_notes" in payload:
        module.review_notes = str(payload["review_notes"]).strip()
        update_fields.append("review_notes")

    module.is_published = bool(payload["is_published"])
    update_fields.append("is_published")

    unique_fields = list(dict.fromkeys(update_fields))
    module.save(update_fields=unique_fields)

    return Response(
        {
            "id": module.id,
            "review_status": module.review_status,
            "review_notes": module.review_notes,
            "is_published": module.is_published,
            "message": "Publish status updated",
        },
        status=200,
    )


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
@throttle_classes([ScopedRateThrottle])
def chat_send(request):
    """Authenticated learner chat: routes tutor vs support, persists session/messages."""
    try:
        body = request.data
    except ParseError:
        return Response({"error": "Invalid JSON payload"}, status=status.HTTP_400_BAD_REQUEST)

    message = str(body.get("message") or "").strip()
    if not message:
        return Response({"error": "Field 'message' is required"}, status=status.HTTP_400_BAD_REQUEST)
    if len(message) > _CHAT_MAX_MESSAGE_LEN:
        return Response(
            {"error": f"Message too long (max {_CHAT_MAX_MESSAGE_LEN} characters)"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    session_key = str(body.get("session_key") or "").strip()
    if len(session_key) > 64:
        return Response({"error": "session_key too long"}, status=status.HTTP_400_BAD_REQUEST)
    if not session_key:
        session_key = new_session_key()

    mode = str(body.get("mode") or "general").strip().lower()
    allowed_modes = {"general", "correction", "hint", "exercise"}
    if mode not in allowed_modes:
        return Response(
            {"error": f"Invalid mode. Allowed: {', '.join(sorted(allowed_modes))}"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    level = str(body.get("level") or "beginner").strip()[:64]
    module_context = str(body.get("module_context") or "").strip()[:_CHAT_MAX_MODULE_CONTEXT_LEN]

    ok, refusal = guard_input(message)
    if not ok:
        ChatRoutingAudit.objects.create(
            user=request.user,
            session_key=session_key,
            message_preview=message[:200],
            classified_intent="blocked",
            confidence=0.0,
            route_chosen="blocked",
            ambiguous=False,
        )
        return Response(
            {
                "reply": refusal,
                "session_key": session_key,
                "route": "blocked",
                "intent": "blocked",
                "intent_confidence": 0.0,
                "ambiguous": False,
                "needs_human_handoff": False,
                "mode": None,
            },
            status=status.HTTP_200_OK,
        )

    if not _check_and_increment_chat_daily(request.user):
        payload = {
            "error": "Daily chat limit reached. Try again tomorrow.",
            "code": "chat_daily_limit",
        }
        if not user_has_paid_subscription(request.user):
            payload["upgrade_available"] = True
        return Response(payload, status=status.HTTP_429_TOO_MANY_REQUESTS)

    session, _ = ChatSession.objects.get_or_create(
        user=request.user,
        session_key=session_key,
    )

    history_qs = (
        session.messages.filter(
            role__in=(ChatMessage.Role.USER, ChatMessage.Role.ASSISTANT),
        )
        .order_by("-id")[:32]
    )
    history_msgs = [{"role": m.role, "content": m.content} for m in reversed(list(history_qs))]

    classification = classify_intent(message)
    route = "support" if classification["intent"] == "support" else "tutor"
    handoff = False
    snippets = []
    kb_conf = 0.0

    if route == "support":
        snippets, kb_conf = retrieve_kb_snippets(message)
        handoff = needs_human_handoff(message, kb_conf)
        system = build_cs_system(snippets)
        if handoff:
            system += (
                "\nThe user may need a human agent. Acknowledge limits and point to the FAQ "
                "escalation contact when appropriate."
            )
    else:
        system = build_tutor_system(mode, level, module_context)
        if classification["ambiguous"]:
            system += (
                "\nThe user's intent may be ambiguous (account vs learning). "
                "Answer as English tutor; briefly offer to help with billing if they meant account support."
            )

    messages_payload = [{"role": "system", "content": system}, *history_msgs, {"role": "user", "content": message}]
    messages_payload = trim_history(messages_payload)

    history_empty = len(history_msgs) == 0
    cache_hash = compute_chat_reply_cache_lookup_hash(
        route=route,
        mode=mode,
        level=level,
        module_context=module_context,
        message=message,
        ambiguous=bool(classification["ambiguous"]),
        history_empty=history_empty,
    )
    cached_row = ChatReplyCache.objects.filter(lookup_hash=cache_hash).first() if cache_hash else None

    if cached_row:
        reply_text = cached_row.reply
        llm_meta = {"provider": "cache", "tokens": 0}
        reply_text, sanitized = guard_output(reply_text)
        ChatReplyCache.objects.filter(pk=cached_row.pk).update(
            hit_count=F("hit_count") + 1,
            last_used_at=timezone.now(),
        )
    else:
        reply_text, llm_meta = call_llm(messages_payload)
        reply_text, sanitized = guard_output(reply_text)

        if classification["ambiguous"] and route == "tutor":
            reply_text = (
                "If you meant account or billing help, say 'billing' or 'subscription'. "
                f"Otherwise: {reply_text}"
            )

        if cache_hash:
            ChatReplyCache.objects.update_or_create(
                lookup_hash=cache_hash,
                defaults={"route": route, "reply": reply_text},
            )

    ChatMessage.objects.create(
        session=session,
        role=ChatMessage.Role.USER,
        content=message,
        intent_route=route,
        mode=mode if route == "tutor" else "",
        metadata={"classification": classification},
    )
    ChatMessage.objects.create(
        session=session,
        role=ChatMessage.Role.ASSISTANT,
        content=reply_text,
        intent_route=route,
        mode=mode if route == "tutor" else "",
        metadata={**llm_meta, "sanitized": sanitized},
    )
    ChatRoutingAudit.objects.create(
        user=request.user,
        session_key=session_key,
        message_preview=message[:200],
        classified_intent=classification["intent"],
        confidence=float(classification["confidence"]),
        route_chosen=route,
        ambiguous=bool(classification["ambiguous"]),
    )

    return Response(
        {
            "reply": reply_text,
            "session_key": session.session_key,
            "route": route,
            "intent": classification["intent"],
            "intent_confidence": classification["confidence"],
            "ambiguous": classification["ambiguous"],
            "needs_human_handoff": handoff,
            "mode": mode if route == "tutor" else None,
        },
        status=status.HTTP_200_OK,
    )


chat_send.throttle_scope = "chat"

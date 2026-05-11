"""
Web discovery pipeline: search → fetch HTML → extract main text → ingest as RawContent.

Used by admin UI and management command (cron). Respect robots.txt/TOS of target sites in production.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any
from urllib.parse import urldefrag

import requests
import trafilatura
from django.conf import settings
from django.core.cache import cache
from django.db import transaction

from .models import RawContent
from .pipeline import apply_post_ingest_metadata
from .serializers import RawContentIngestSerializer

logger = logging.getLogger(__name__)

BLOCK_CACHE_PREFIX = "discovery:block:"
BLOCK_CACHE_SECONDS = int(getattr(settings, "DISCOVERY_FAILED_URL_CACHE_SECONDS", 604800))  # 7 days


def normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    base, _frag = urldefrag(u)
    return base


def _block_cache_key(url: str) -> str:
    n = normalize_url(url)
    h = hashlib.sha256(n.encode("utf-8")).hexdigest()
    return f"{BLOCK_CACHE_PREFIX}{h}"


def is_discovery_url_blocked(url: str) -> bool:
    return cache.get(_block_cache_key(url)) is not None


def block_discovery_url(url: str, reason: str = "fetch_failed") -> None:
    cache.set(_block_cache_key(url), reason[:500], timeout=BLOCK_CACHE_SECONDS)


def search_candidate_urls(query: str, max_results: int, backend: str) -> list[dict[str, str]]:
    """Return [{"url": "...", "snippet_title": "..."}, ...]."""
    q = (query or "").strip()
    if not q:
        return []
    backend = (backend or getattr(settings, "DISCOVERY_SEARCH_BACKEND", "duckduckgo")).strip().lower()
    cap = min(max_results, int(getattr(settings, "DISCOVERY_MAX_RESULTS_CAP", 15)))
    cap = max(1, cap)

    if backend == "serpapi":
        return _search_serpapi(q, cap)
    if backend in ("google", "google_cse"):
        return _search_google_cse(q, cap)
    return _search_duckduckgo(q, cap)


def _search_duckduckgo(query: str, max_results: int) -> list[dict[str, str]]:
    try:
        from duckduckgo_search import DDGS
    except ImportError as exc:
        raise RuntimeError("Install duckduckgo-search (see requirements.txt)") from exc

    out: list[dict[str, str]] = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            href = (r.get("href") or "").strip()
            if not href.startswith(("http://", "https://")):
                continue
            out.append(
                {
                    "url": normalize_url(href),
                    "snippet_title": (r.get("title") or "").strip(),
                }
            )
    return out


def _search_serpapi(query: str, max_results: int) -> list[dict[str, str]]:
    api_key = getattr(settings, "SERPAPI_API_KEY", "") or ""
    if not api_key.strip():
        raise RuntimeError("SERPAPI_API_KEY is not set (required for search backend serpapi)")

    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key.strip(),
        "num": min(max_results, 20),
    }
    resp = requests.get("https://serpapi.com/search.json", params=params, timeout=35)
    resp.raise_for_status()
    data = resp.json()
    organic = data.get("organic_results") or []
    out: list[dict[str, str]] = []
    for row in organic[:max_results]:
        link = (row.get("link") or "").strip()
        if not link.startswith(("http://", "https://")):
            continue
        out.append(
            {
                "url": normalize_url(link),
                "snippet_title": (row.get("title") or "").strip(),
            }
        )
    return out


def _search_google_cse(query: str, max_results: int) -> list[dict[str, str]]:
    """Google Custom Search JSON API (Programmable Search Engine). Max 10 hits per request."""
    api_key = (getattr(settings, "GOOGLE_CSE_API_KEY", "") or "").strip()
    cx = (getattr(settings, "GOOGLE_CSE_CX", "") or "").strip()
    if not api_key or not cx:
        raise RuntimeError(
            "GOOGLE_CSE_API_KEY (or GOOGLE_API_KEY) and GOOGLE_CSE_CX must be set for search backend google"
        )

    out: list[dict[str, str]] = []
    start = 1
    while len(out) < max_results:
        num = min(10, max_results - len(out))
        params = {
            "key": api_key,
            "cx": cx,
            "q": query,
            "num": num,
            "start": start,
        }
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params,
            timeout=35,
        )
        if resp.status_code != 200:
            try:
                payload = resp.json()
                msg = (payload.get("error") or {}).get("message") or resp.text[:240]
            except Exception:
                msg = resp.text[:240]
            raise RuntimeError(f"Google CSE HTTP {resp.status_code}: {msg}")

        data = resp.json()
        items = data.get("items") or []
        if not items:
            break
        for item in items:
            link = (item.get("link") or "").strip()
            if link.startswith(("http://", "https://")):
                out.append(
                    {
                        "url": normalize_url(link),
                        "snippet_title": (item.get("title") or "").strip(),
                    }
                )
        start += len(items)
        if len(items) < num:
            break

    return out[:max_results]


def fetch_article_text(url: str) -> tuple[str | None, str | None, str | None]:
    """
    Download URL and extract main text with trafilatura.
    Returns (page_title, raw_text, error_code_or_message).
    """
    nurl = normalize_url(url)
    if not nurl:
        return None, None, "invalid_url"

    ua = getattr(
        settings,
        "DISCOVERY_HTTP_USER_AGENT",
        "Mozilla/5.0 (compatible; EduPlatformContentBot/1.0; +https://example.com)",
    )
    try:
        r = requests.get(
            nurl,
            timeout=int(getattr(settings, "DISCOVERY_HTTP_TIMEOUT_SECONDS", 25)),
            headers={"User-Agent": ua},
        )
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "html" not in ctype and "text/xml" not in ctype:
            return None, None, f"non_html:{ctype[:40]}"

        doc = r.text
        raw_text = trafilatura.extract(doc, include_comments=False, include_tables=False)
        min_len = int(getattr(settings, "DISCOVERY_MIN_EXTRACTED_CHARS", 300))
        if not raw_text or len(raw_text.strip()) < min_len:
            return None, None, "extract_too_short"

        meta = trafilatura.extract_metadata(doc)
        title = (meta.title if meta else None) or ""
        title = title.strip() or None
        return title, raw_text.strip(), None
    except requests.RequestException as exc:
        logger.info("discovery fetch failed url=%s err=%s", nurl, exc)
        return None, None, f"http_error:{str(exc)[:180]}"


def _normalize_suggested_difficulty(raw: str | None) -> str:
    v = (raw or "beginner").strip().lower()
    if v in ("beginner", "intermediate", "advanced"):
        return v
    return "beginner"


def run_discover_and_ingest(
    *,
    query: str,
    max_results: int,
    category: str,
    language_code: str = "en",
    locale: str = "",
    suggested_difficulty: str | None = None,
    search_backend: str | None = None,
    queue_jobs: bool = True,
    sleep_seconds: float = 1.0,
) -> dict[str, Any]:
    """
    Search → fetch → ingest. Does not create ProcessedModule (same as minimal OpenClaw POST).

    Returns report dict with created / skipped / failed lists.
    """
    category = (category or "").strip()
    if not category:
        return {"error": "category is required", "created": [], "skipped": [], "failed": []}

    q = (query or "").strip()
    if len(q) < 3:
        return {"error": "query must be at least 3 characters", "created": [], "skipped": [], "failed": []}

    cap = int(getattr(settings, "DISCOVERY_MAX_RESULTS_CAP", 15))
    max_results = max(1, min(int(max_results), cap))
    diff = _normalize_suggested_difficulty(suggested_difficulty)

    backend = (search_backend or getattr(settings, "DISCOVERY_SEARCH_BACKEND", "duckduckgo")).strip().lower()
    if backend == "serpapi" and not (getattr(settings, "SERPAPI_API_KEY", "") or "").strip():
        return {
            "error": "SERPAPI_API_KEY missing for backend serpapi",
            "created": [],
            "skipped": [],
            "failed": [],
        }
    if backend in ("google", "google_cse"):
        gkey = (getattr(settings, "GOOGLE_CSE_API_KEY", "") or "").strip()
        gcx = (getattr(settings, "GOOGLE_CSE_CX", "") or "").strip()
        if not gkey or not gcx:
            return {
                "error": "GOOGLE_CSE_API_KEY (or GOOGLE_API_KEY) and GOOGLE_CSE_CX required for backend google",
                "created": [],
                "skipped": [],
                "failed": [],
            }

    try:
        candidates = search_candidate_urls(q, max_results, backend)
    except Exception as exc:
        logger.exception("discovery search failed")
        return {
            "error": str(exc)[:500],
            "created": [],
            "skipped": [],
            "failed": [],
        }

    created: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    seen_in_batch: set[str] = set()

    for item in candidates:
        url = normalize_url(item.get("url") or "")
        snippet_title = (item.get("snippet_title") or "").strip()

        if not url:
            continue
        if url in seen_in_batch:
            skipped.append({"source_url": url, "reason": "duplicate_in_batch"})
            continue
        seen_in_batch.add(url)

        if RawContent.objects.filter(source_url=url).exists():
            skipped.append({"source_url": url, "reason": "already_ingested"})
            continue

        if is_discovery_url_blocked(url):
            skipped.append({"source_url": url, "reason": "recently_failed_cached"})
            continue

        page_title, raw_text, err = fetch_article_text(url)
        if err:
            failed.append({"source_url": url, "error": err})
            block_discovery_url(url, err)
            time.sleep(sleep_seconds)
            continue

        title = (page_title or snippet_title or url)[:255]

        payload = {
            "title": title,
            "source_url": url,
            "raw_text": raw_text,
            "category": category[:100],
            "language_code": (language_code or "en").strip().lower(),
            "locale": (locale or "")[:32],
            "metadata": {
                "discovery": True,
                "discovery_query": q[:500],
                "suggested_difficulty": diff,
            },
        }
        serializer = RawContentIngestSerializer(data=payload)
        if not serializer.is_valid():
            failed.append({"source_url": url, "error": f"validation:{serializer.errors}"})
            time.sleep(sleep_seconds)
            continue

        try:
            with transaction.atomic():
                raw_content = serializer.save()
                apply_post_ingest_metadata(raw_content, queue_jobs=queue_jobs)
        except Exception as exc:
            logger.exception("discovery ingest failed url=%s", url)
            failed.append({"source_url": url, "error": str(exc)[:200]})
            block_discovery_url(url, "ingest_exception")
            time.sleep(sleep_seconds)
            continue

        created.append({"title": title, "source_url": url})
        time.sleep(sleep_seconds)

    return {
        "query": q,
        "search_backend": backend,
        "requested_max": max_results,
        "suggested_difficulty": diff,
        "candidates_found": len(candidates),
        "created": created,
        "skipped": skipped,
        "failed": failed,
    }

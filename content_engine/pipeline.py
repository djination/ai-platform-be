import hashlib
import re
from decimal import Decimal

from .models import EnrichmentCache, EnrichmentJob, RawContent

UNSAFE_KEYWORDS = {
    "hate",
    "violence",
    "terror",
    "porn",
}


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def compute_content_hash(source_url: str, raw_text: str) -> str:
    normalized = f"{source_url}|{normalize_text(raw_text)}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def detect_language_code(raw_text: str, fallback: str = "en") -> str:
    text = (raw_text or "").lower()
    id_markers = {"yang", "dan", "untuk", "dengan", "adalah"}
    if any(marker in text for marker in id_markers):
        return "id"
    return fallback


def safety_scan(raw_text: str) -> dict:
    lowered = (raw_text or "").lower()
    flags = sorted([word for word in UNSAFE_KEYWORDS if word in lowered])
    return {"has_flags": bool(flags), "flags": flags}


def quality_score(raw_text: str, title: str) -> dict:
    text = raw_text or ""
    title_score = 20 if (title or "").strip() else 0
    length_score = min(len(text) // 20, 50)
    structure_score = 30 if "." in text else 10
    total = min(title_score + length_score + structure_score, 100)
    return {"score": total, "components": {"title": title_score, "length": length_score, "structure": structure_score}}


def build_prompt_templates(raw_content) -> dict:
    base = raw_content.raw_text[:2000]
    return {
        "summary": f"Summarize the following English-learning content into concise lesson points:\n\n{base}",
        "quiz_seed": f"Generate 5 multiple-choice quiz seeds from this content:\n\n{base}",
        "cefr_tagging": f"Tag this content with CEFR level and rationale:\n\n{base}",
    }


def apply_post_ingest_metadata(raw_content: RawContent, *, queue_jobs: bool = True) -> None:
    """Same steps as HTTP ingest after RawContentIngestSerializer.save(): hash, safety, quality, jobs."""
    metadata = dict(raw_content.metadata or {})
    metadata["content_hash"] = compute_content_hash(raw_content.source_url, raw_content.raw_text)
    metadata["language_detected"] = detect_language_code(raw_content.raw_text, raw_content.language_code)
    metadata["safety"] = safety_scan(raw_content.raw_text)
    metadata["quality"] = quality_score(raw_content.raw_text, raw_content.title)
    raw_content.metadata = metadata
    raw_content.language_code = metadata["language_detected"]
    raw_content.save(update_fields=["metadata", "language_code"])
    if queue_jobs:
        queue_enrichment_jobs(raw_content)


def queue_enrichment_jobs(raw_content):
    prompts = build_prompt_templates(raw_content)
    jobs = []
    for prompt_type, prompt_body in prompts.items():
        prompt_hash = hashlib.sha256(f"{prompt_type}|{prompt_body}".encode("utf-8")).hexdigest()
        jobs.append(
            EnrichmentJob.objects.create(
                raw_content=raw_content,
                prompt_type=prompt_type,
                prompt_body=prompt_body,
                prompt_hash=prompt_hash,
            )
        )
    return jobs


def estimate_usage(prompt_body: str) -> tuple[int, Decimal]:
    tokens = max(1, len(prompt_body) // 4)
    cost = (Decimal(tokens) * Decimal("0.000001")).quantize(Decimal("0.000001"))
    return tokens, cost


def execute_enrichment_job(job: EnrichmentJob):
    cache_entry = EnrichmentCache.objects.filter(prompt_hash=job.prompt_hash).first()
    if cache_entry:
        job.response_json = cache_entry.response_json
        job.token_usage = cache_entry.token_usage
        job.estimated_cost_usd = cache_entry.estimated_cost_usd
        job.status = EnrichmentJob.Status.COMPLETED
        return

    tokens, cost = estimate_usage(job.prompt_body)
    response_payload = {
        "prompt_type": job.prompt_type,
        "note": "Baseline enrichment result. Replace with OpenRouter call in production worker.",
        "preview": job.prompt_body[:240],
    }
    EnrichmentCache.objects.create(
        prompt_hash=job.prompt_hash,
        prompt_type=job.prompt_type,
        model_name="openrouter-baseline",
        response_json=response_payload,
        token_usage=tokens,
        estimated_cost_usd=cost,
    )
    job.response_json = response_payload
    job.token_usage = tokens
    job.estimated_cost_usd = cost
    job.status = EnrichmentJob.Status.COMPLETED

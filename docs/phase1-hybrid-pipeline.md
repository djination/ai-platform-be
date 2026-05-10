# Phase 1 Hybrid Pipeline Baseline

Dokumen ini menutup scope `B1.*`, `B2.*`, dan `B3.*` pada level baseline operasional.

## B1 Discovery Layer (OpenClaw Agent)

### Source policy (`B1.1`)
- Prioritaskan domain edukasi tepercaya.
- Simpan `source_url` wajib pada setiap payload.
- Hindari konten duplikat dan konten berisiko (lihat safety scan di backend).

### Output format raw scrape (`B1.2`)
Payload minimum wajib:
- `title`
- `source_url`
- `raw_text`
- `category`
- `language_code` (opsional, default `en`)
- `metadata` (opsional)

### Retry policy (`B1.3`)
- Retry maksimal 3 kali per sumber.
- Gunakan exponential backoff (mis. 2s, 4s, 8s).
- Jika tetap gagal, simpan ke dead-letter queue eksternal untuk review manual.

## B2 Backend Quality Gate

Implemented di backend (`content_engine/pipeline.py` + `views.py`):
- `B2.1` Dedupe hash: `content_hash` dari `source_url + normalized_text`.
- `B2.2` Language detection baseline: auto-detect `id`/`en`.
- `B2.3` Safety check baseline: keyword flagging.
- `B2.4` Quality score baseline: title/length/structure scoring.

Semua hasil disimpan ke `RawContent.metadata`.

## B3 AI Enrichment via Queue Worker

### Prompt templates (`B3.1`)
- Summary prompt
- Quiz seed prompt
- CEFR tagging prompt

Template dibangun di `build_prompt_templates()` (`pipeline.py`).

### Async jobs (`B3.2`)
- Job queue model: `EnrichmentJob`
- Worker command: `python manage.py run_enrichment_worker --limit 50`

### Usage logging (`B3.3`)
- Per job menyimpan:
  - `token_usage`
  - `estimated_cost_usd`
  - `status`, `started_at`, `finished_at`, `error_message`

### Caching (`B3.4`)
- Cache model: `EnrichmentCache` keyed by `prompt_hash`.
- Jika prompt hash ditemukan di cache, worker tidak hit model eksternal lagi.

## Key Rotation and Fallback Support

- Fallback key environment tetap didukung (`OPENCLAW_API_KEY`).
- Rotation command:
  - `python manage.py rotate_ingest_key --name openclaw-main-v2 --deactivate openclaw-main`

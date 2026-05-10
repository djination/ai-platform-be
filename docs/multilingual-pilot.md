# Multilingual pilot (Phase 2 — Stream G2)

## G2.1 Pilot language & levels

| Decision | Choice |
|----------|--------|
| **Second content language (pilot)** | **Indonesian (`id`)** — aligns with learner UI copy and local market; English (`en`) remains primary. |
| **Locale tag for ingest** | Use `language_code: "id"` on raw content (see ingest contract). Optional `locale` (e.g. `id-ID`) when needed for formatting. |
| **Level rubric** | Maps to existing product tabs and backend `ProcessedModule.difficulty`: |

| Product tab | Backend difficulty | CEFR band (reference) |
|-------------|---------------------|------------------------|
| Beginner | `beginner` | A1–A2 |
| Intermediate | `intermediate` | B1–B2 |
| Advanced | `advanced` | C1–C2 |

Pilot content should target one band per module and stay consistent with quiz complexity for that band.

## G2.2 Ingest checklist (20–50 items)

1. Prepare lessons with **`language_code`: `"id"`** (and optional **`locale`**).
2. Use the same hybrid pipeline as English: **`POST /api/content-engine/ingest/`** with machine/API key.
3. Run **`draft-module` → review → publish** for each item (or batch via admin).
4. Verify learner APIs with **`?language=id`** (or frontend Learning page — **Bahasa materi → Indonesia (id)**).

### Sample seed (dev / staging)

Bundled JSON payloads (5 modules, mixed difficulty):

- `content_engine/fixtures/pilot_id_ingest_payloads.json`

Management command (same post-save steps as HTTP ingest; **no API key**):

```bash
cd backend
py -3 manage.py seed_pilot_id_content --dry-run
py -3 manage.py seed_pilot_id_content --skip-enrichment
py -3 manage.py seed_pilot_id_content --publish --skip-enrichment
```

`--publish` marks each created `ProcessedModule` published so learner UI can show them immediately. Omit `--skip-enrichment` when you want enrichment jobs queued as in production ingest.

## OpenClaw prompt vs backend (FAQ)

| Langkah | Siapa yang cocok |
|--------|-------------------|
| **Cari artikel di internet**, cek relevansi, hindari duplikat, **ambil teks** dari halaman | Agent luar (**OpenClaw**, skrip sendiri, dll.). Backend Django **tidak** punya fitur bawaan “Google + baca 10 URL” kecuali Anda tambahkan integrasi (API pencarian + scraper). |
| **Simpan** `title`, `source_url`, `raw_text`, `category` ke database (`RawContent`) | **Backend**: endpoint **`POST /api/content-engine/ingest/`** *atau* perintah Django di bawah. |

**Alur praktis tanpa bolak-balik POST manual:**

1. Jalankan prompt OpenClaw seperti biasa, tetapi minta output akhir berupa **satu file JSON**: array berisi objek persis seperti payload ingest Anda (minimal `title`, `source_url`, `raw_text`, `category`).
2. Salin file itu ke server (mis. `openclaw_export.json`).
3. Di folder `backend`:

```bash
py -3 manage.py ingest_bulk_from_json path/to/openclaw_export.json --skip-enrichment
```

Ini **setara** dengan memanggil ingest berulang kali; **tanpa** chat OpenClaw mengirim tiap POST ke production — Anda bisa review file dulu.

Contoh bentuk file: `content_engine/fixtures/openclaw_export_example.json`.

**Catatan:** Payload OpenClaw Anda (tanpa `processed_module`) hanya membuat **raw content**. Agar muncul di learner sebagai modul, tetap perlu **Buat modul draft → review → publish** (Admin UI atau API admin).

## Backend discovery (menggantikan agent OpenClaw untuk bagian A)

Fitur opsional di platform ini:

- **POST** `/api/content-engine/admin/discover-ingest/` (JWT + admin/content_manager), atau
- CLI: `python manage.py discover_ingest --query "..." --category "..."`

Alur: **pencarian** (DuckDuckGo default; **Google Custom Search JSON API** dengan `GOOGLE_CSE_API_KEY` + `GOOGLE_CSE_CX`; atau **SerpAPI** dengan `SERPAPI_API_KEY`) → unduh HTML → **trafilatura** ekstrak teks → simpan seperti ingest.

Panel **Admin** (frontend) punya form **Discovery konten**. Batas hasil dan throttle dikonfigurasi lewat env (`DISCOVERY_*`).

Penting secara hukum/etika: patuhi ToS situs sumber dan robots.txt; gunakan User-Agent yang jujur; untuk produksi pertimbangkan whitelist domain atau kontrak konten.

## G2.3 Quality bar (completion & feedback)

- Track completion rate for `id` modules vs `en` baseline (same level band).
- Collect qualitative feedback: clarity, quiz fairness, alignment with CEFR expectation for that band.
- Iterate prompts/rubric before scaling beyond pilot volume.

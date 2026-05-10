# Ingest API Contract v1

Endpoint ini dipakai oleh worker eksternal (mis. OpenClaw pipeline) untuk mengirim konten mentah ke backend.

## Endpoint

- Method: `POST`
- URL: `/api/content-engine/ingest/`
- Auth header wajib: `X-API-Key: <active-ingest-key>`

Health check:
- Method: `GET`
- URL: `/api/content-engine/ingest/`
- Response: `{"status": "ok"}`

## Request Body (JSON)

```json
{
  "source_url": "https://example.com/article-1",
  "title": "Present Perfect Basics",
  "raw_text": "Long article text ...",
  "category": "grammar",
  "language_code": "en",
  "metadata": {
    "source_name": "example-site",
    "scraped_at": "2026-05-09T14:00:00Z",
    "tags": ["present-perfect", "english"]
  },
  "processed_module": {
    "module_json": {
      "lessons": ["..."],
      "quizzes": ["..."],
      "examples": ["..."]
    },
    "difficulty": "beginner",
    "is_published": false
  }
}
```

## Field Rules

- `source_url` (required): URL valid.
- `title` (required): string non-empty.
- `raw_text` (required): string non-empty.
- `category` (required): string category.
- `language_code` (optional, default `en`): ISO code sederhana (`en`, `id`, `en-us`, dst).
- `metadata` (optional, default `{}`): object JSON.
- `processed_module` (optional): object untuk membuat `ProcessedModule` langsung.

## Responses

### 201 Created

```json
{
  "message": "Success"
}
```

### 400 Bad Request

Payload JSON invalid:

```json
{
  "error": "Invalid JSON payload"
}
```

Payload tidak lolos validasi serializer:

```json
{
  "errors": {
    "source_url": ["Enter a valid URL."]
  }
}
```

### 403 Forbidden

```json
{
  "error": "Missing API key"
}
```

atau

```json
{
  "error": "Invalid API key"
}
```

### 429 Too Many Requests

Jika melewati rate limit `openclaw_ingest` (berdasarkan API key / client identity), endpoint akan mengembalikan status 429.

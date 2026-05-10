# Paket Free vs Pro — matriks fitur (H1.1)

Dokumen ini menjadi dasar paywall (H1.3) dan integrasi pembayaran (H1.2). Angka dan nama paket bisa disesuaikan sebelum launch.

## Ringkasan

| Aspek | Free | Pro |
|--------|------|-----|
| **Target** | Coba platform, materi inti | Belajar lebih dalam + AI lebih banyak |
| **Harga** | Rp 0 | Berlangganan bulanan/tahunan (ditentukan saat H1.2) |

## Fitur — learner

| Fitur | Free | Pro |
|--------|------|-----|
| Akses modul **published** (reading + quiz) | ✅ Semua modul publish yang tersedia | ✅ Sama |
| Filter bahasa materi (`language_code`) | ✅ | ✅ |
| **Chat AI tutor** (sesi + routing tutor/support) | ✅ Dengan **batas pesan/hari** (lihat `CHAT_DAILY_MESSAGE_LIMIT`) | ✅ Kuota lebih tinggi atau **unlimited** (kebijakan produk) |
| **Mode chat** (general, correction, hint, exercise) | ✅ | ✅ |
| Konteks materi dari Learning → Chat | ✅ | ✅ |
| Sertifikat / badge | ❌ atau terbatas | ✅ (jika diaktifkan nanti) |
| Download / offline | ❌ | Opsional fase berikutnya |

## Fitur — admin / content ops

| Fitur | Free | Pro |
|--------|------|-----|
| Akun **learner** | ✅ Daftar sendiri (+ reCAPTCHA jika aktif) | ✅ |
| Akun **admin / content_manager** | ❌ Hanya via Django | ❌ Sama |

## Batas teknis (backend) — saat ini

- Chat: throttle `chat` untuk semua user. **Free**: `CHAT_DAILY_MESSAGE_LIMIT`. **Pro** (jika `LearnerEntitlement.pro_access_until` masih di masa depan): `CHAT_PRO_DAILY_MESSAGE_LIMIT` (nilai `0` = tanpa batas harian, throttle tetap).
- Model `LearnerEntitlement`: paket (`free` / `go` / `plus` / `pro`), `payment_status`, `pending_plan_code`, `pro_access_until`. Harga & copy UI: `billing_catalog.py`; aktivasi setelah payment gateway mengonfirmasi bayar.

## Keputusan produk yang masih terbuka

1. Harga Pro dan apakah ada **trial** (H1.2).
2. Kuota chat harian **Pro** vs **Free** (angka konkret).
3. Apakah **discovery / ingest** otomatis hanya internal (bukan fitur learner).

Setelah H1.2/H1.3: tambahkan pengecekan entitlement di view chat dan (opsional) di endpoint modul untuk fitur “hanya Pro”.

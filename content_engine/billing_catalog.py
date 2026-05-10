"""
Katalog paket untuk API / UI. Sumber utama: model BillingCatalogPlan (Django Admin).
Jika belum ada baris aktif, dipakai fallback bawaan (dev).
"""

from __future__ import annotations

import copy
from typing import Any

# Fallback jika DB kosong / belum dimigrasi (jangan hapus; dipakai get_catalog_plans).
_FALLBACK_PLANS: list[dict[str, Any]] = [
    {
        "code": "free",
        "title": "Free",
        "price_prefix": "",
        "price_idr_monthly": 0,
        "price_display": "Rp 0",
        "period_label": "/ bulan",
        "vat_note": "",
        "slogan": "Coba dulu, belajar santai tanpa biaya.",
        "features": [
            {"icon": "spark", "text": "AI tutor dasar untuk teman latihan"},
            {"icon": "message", "text": "Kuota chat harian untuk pemakaian ringan"},
            {"icon": "book", "text": "Akses materi belajar dasar"},
            {"icon": "memory", "text": "Konteks chat singkat per sesi"},
        ],
        "footer_note": "",
        "popular": False,
        "cta_kind": "current_free",
    },
    {
        "code": "go",
        "title": "Go",
        "price_prefix": "",
        "price_idr_monthly": 75_000,
        "price_display": "Rp 75.000",
        "period_label": "/ bulan",
        "vat_note": "Termasuk PPN",
        "slogan": "Naik level, latihan jadi lebih leluasa.",
        "features": [
            {"icon": "spark", "text": "AI tutor dasar dengan respons lebih stabil"},
            {"icon": "message", "text": "Kuota chat lebih besar dari paket Free"},
            {"icon": "book", "text": "Akses semua modul pembelajaran"},
            {"icon": "memory", "text": "Konteks chat lebih panjang"},
            {"icon": "search", "text": "Penjelasan materi lebih detail"},
        ],
        "footer_note": "",
        "popular": False,
        "cta_kind": "upgrade",
    },
    {
        "code": "plus",
        "title": "Plus",
        "price_prefix": "",
        "price_idr_monthly": 349_000,
        "price_display": "Rp 349.000",
        "period_label": "/ bulan",
        "vat_note": "Termasuk PPN",
        "slogan": "Buat yang lagi ngebut ke target belajar.",
        "features": [
            {"icon": "spark", "text": "Model tutor lebih mumpuni"},
            {"icon": "message", "text": "Kuota chat jauh lebih besar untuk belajar intensif"},
            {"icon": "book", "text": "Bahas materi lebih dalam"},
            {"icon": "memory", "text": "Memori lintas sesi belajar"},
            {"icon": "search", "text": "Eksplorasi materi lebih luas"},
            {"icon": "zap", "text": "Prioritas respons lebih tinggi"},
        ],
        "footer_note": "",
        "popular": True,
        "cta_kind": "upgrade",
    },
    {
        "code": "pro",
        "title": "Pro",
        "price_prefix": "Mulai",
        "price_idr_monthly": 1_889_000,
        "price_display": "Rp 1.889.000",
        "period_label": "/ bulan",
        "vat_note": "Termasuk PPN",
        "slogan": "Paket paling lengkap untuk belajar tanpa banyak batas.",
        "features": [
            {"icon": "stack", "text": "Semua fitur Plus, ditambah:"},
            {"icon": "message", "text": "Kuota chat sangat besar (fair usage)"},
            {"icon": "spark", "text": "Prioritas ke model terbaik"},
            {"icon": "memory", "text": "Konteks dan memori paling panjang"},
            {"icon": "zap", "text": "Coba lebih awal fitur-fitur baru"},
        ],
        "footer_note": "Batas wajar berlaku untuk antisalahgunakan.",
        "popular": False,
        "cta_kind": "upgrade",
    },
]


def default_catalog_plans() -> list[dict[str, Any]]:
    """Canonical default plans for seed/fallback/sync operations."""
    return copy.deepcopy(_FALLBACK_PLANS)


def get_catalog_plans() -> list[dict[str, Any]]:
    from .models import BillingCatalogPlan

    qs = BillingCatalogPlan.objects.filter(is_active=True).order_by("sort_order", "id")
    if qs.exists():
        return [p.to_catalog_dict() for p in qs]
    return default_catalog_plans()


def valid_paid_plan_codes() -> set[str]:
    return {p["code"] for p in get_catalog_plans() if p["code"] != "free"}


def find_plan(plan_code: str) -> dict[str, Any] | None:
    code = (plan_code or "").strip().lower()
    for p in get_catalog_plans():
        if p["code"] == code:
            return p
    return None


def plan_public_dict(plan: dict[str, Any], *, effective_code: str | None) -> dict[str, Any]:
    out = {k: v for k, v in plan.items() if k != "cta_kind"}
    out["is_current_effective"] = plan["code"] == effective_code
    return out

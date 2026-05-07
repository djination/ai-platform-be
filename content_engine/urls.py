from django.urls import path

from .views import (
    admin_processed_module_detail,
    admin_processed_modules_list,
    admin_raw_content_list,
    ingest_content,
    module_view,
)

urlpatterns = [
    path("ingest/", ingest_content, name="ingest_content"),
    path("module/", module_view, name="module_view"),
    path("admin/raw-content/", admin_raw_content_list, name="admin_raw_content_list"),
    path(
        "admin/processed-modules/",
        admin_processed_modules_list,
        name="admin_processed_modules_list",
    ),
    path(
        "admin/processed-modules/<int:module_id>/",
        admin_processed_module_detail,
        name="admin_processed_module_detail",
    ),
]

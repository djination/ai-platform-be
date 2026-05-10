from django.urls import path

from .billing_views import (
    BillingPlansView,
    DemoCompletePaymentView,
    MeEntitlementView,
    MeLimitsView,
    RequestPlanUpgradeView,
)
from .views import (
    admin_discover_ingest,
    admin_processed_module_detail,
    admin_processed_modules_list,
    admin_raw_content_create_draft_module,
    admin_raw_content_detail,
    admin_raw_content_list,
    chat_send,
    ingest_content,
    module_view,
    published_module_detail_view,
    published_modules_view,
)

urlpatterns = [
    path("ingest/", ingest_content, name="ingest_content"),
    path("billing/plans/", BillingPlansView.as_view(), name="billing_plans"),
    path(
        "billing/request-upgrade/",
        RequestPlanUpgradeView.as_view(),
        name="billing_request_upgrade",
    ),
    path(
        "billing/demo/complete-payment/",
        DemoCompletePaymentView.as_view(),
        name="billing_demo_complete_payment",
    ),
    path("me/entitlement/", MeEntitlementView.as_view(), name="me_entitlement"),
    path("me/limits/", MeLimitsView.as_view(), name="me_limits"),
    path("chat/", chat_send, name="chat_send"),
    path("module/", module_view, name="module_view"),
    path("modules/published/", published_modules_view, name="published_modules_view"),
    path(
        "modules/<int:module_id>/",
        published_module_detail_view,
        name="published_module_detail_view",
    ),
    path("admin/discover-ingest/", admin_discover_ingest, name="admin_discover_ingest"),
    path("admin/raw-content/", admin_raw_content_list, name="admin_raw_content_list"),
    path(
        "admin/raw-content/<int:raw_content_id>/",
        admin_raw_content_detail,
        name="admin_raw_content_detail",
    ),
    path(
        "admin/raw-content/<int:raw_content_id>/draft-module/",
        admin_raw_content_create_draft_module,
        name="admin_raw_content_create_draft_module",
    ),
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

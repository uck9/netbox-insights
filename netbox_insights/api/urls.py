from django.urls import path

from netbox.api.routers import NetBoxRouter

from .views import (
    DeviceInsightsViewSet,
    EoXSummaryReportAPIView,
    EoXByDeviceTypeReportAPIView,
    EoXByTenantReportAPIView,
    EoXByYearReportAPIView,
)

router = NetBoxRouter()
router.register("devices", DeviceInsightsViewSet, basename="deviceinsights")

urlpatterns = router.urls + [
    path("reports/eox-summary/", EoXSummaryReportAPIView.as_view(), name="api_eox_summary_report"),
    path("reports/eox-by-device-type/", EoXByDeviceTypeReportAPIView.as_view(), name="api_eox_by_device_type_report"),
    path("reports/eox-by-tenant/", EoXByTenantReportAPIView.as_view(), name="api_eox_by_tenant_report"),
    path("reports/eox-by-year/", EoXByYearReportAPIView.as_view(), name="api_eox_by_year_report"),
]

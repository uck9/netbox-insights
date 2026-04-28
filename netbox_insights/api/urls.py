from django.urls import path

from netbox.api.routers import NetBoxRouter

from .views import (
    DeviceInsightsViewSet,
    EoXSummaryReportAPIView,
    EoXByDeviceTypeReportAPIView,
    EoXByTenantReportAPIView,
    EoXByYearReportAPIView,
)

_REPORT_ROOT_ENTRIES = {
    "reports/eox-summary": "api_eox_summary_report",
    "reports/eox-by-device-type": "api_eox_by_device_type_report",
    "reports/eox-by-tenant": "api_eox_by_tenant_report",
    "reports/eox-by-year": "api_eox_by_year_report",
}


class _Router(NetBoxRouter):
    def get_api_root_view(self, api_urls=None):
        api_root_dict = {}
        list_name = self.routes[0].name
        for prefix, viewset, basename in sorted(self.registry, key=lambda x: x[0]):
            api_root_dict[prefix] = list_name.format(basename=basename)
        api_root_dict.update(_REPORT_ROOT_ENTRIES)
        return self.APIRootView.as_view(api_root_dict=api_root_dict)


router = _Router()
router.register("devices", DeviceInsightsViewSet, basename="deviceinsights")

urlpatterns = router.urls + [
    path("reports/eox-summary/", EoXSummaryReportAPIView.as_view(), name="api_eox_summary_report"),
    path("reports/eox-by-device-type/", EoXByDeviceTypeReportAPIView.as_view(), name="api_eox_by_device_type_report"),
    path("reports/eox-by-tenant/", EoXByTenantReportAPIView.as_view(), name="api_eox_by_tenant_report"),
    path("reports/eox-by-year/", EoXByYearReportAPIView.as_view(), name="api_eox_by_year_report"),
]

from django.urls import path
from netbox.views.generic import ObjectChangeLogView


from . import models, views


urlpatterns = (
     path('devices/', views.DeviceInsightsListView.as_view(), name='deviceinsight_list'),
     path('reports/eox/', views.EoXReportView.as_view(), name='eox_report'),
     path('reports/contract-coverage/', views.ContractCoverageReportView.as_view(), name='contract_coverage_report'),
     path('reports/asset-eox/', views.AssetEoXReportView.as_view(), name='asset_eox_report'),
     path('reports/asset-coverage/', views.AssetContractCoverageReportView.as_view(), name='asset_contract_coverage_report'),
     path('reports/installed-at-mismatch/', views.InstalledAtMismatchReportView.as_view(), name='installed_at_mismatch_report'),
     path('reports/license-budget/', views.LicenseBudgetReportView.as_view(), name='license_budget_report'),
     path('reports/hardware-budget/', views.HardwareReplacementBudgetReportView.as_view(), name='hardware_budget_report'),
     # Legacy individual report URLs kept for backwards compatibility
     path('reports/eox-summary/', views.EoXSummaryReportView.as_view(), name='eox_summary_report'),
     path('reports/eox-by-device-type/', views.EoXByDeviceTypeReportView.as_view(), name='eox_by_device_type_report'),
     path('reports/eox-by-tenant/', views.EoXByTenantReportView.as_view(), name='eox_by_tenant_report'),
     path('reports/eox-by-year/', views.EoXByYearReportView.as_view(), name='eox_by_year_report'),
)

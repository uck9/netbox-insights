from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from netbox.api.viewsets import NetBoxModelViewSet
from dcim.models import Device

from ..querysets import device_api_queryset, enrich_devices
from ..filtersets import DeviceInsightsFilterSet
from ..views.reports import (
    _build_eox_report,
    _build_eox_by_device_type_report,
    _build_eox_by_tenant_report,
    _build_eox_by_year_report,
)
from .serializers import DeviceInsightsSerializer


class DeviceInsightsViewSet(NetBoxModelViewSet):
    # Base queryset supplies the model for router introspection.
    # Annotations are added fresh per-request in get_queryset().
    queryset = Device.objects.all()
    serializer_class = DeviceInsightsSerializer
    filterset_class = DeviceInsightsFilterSet

    def get_queryset(self):
        return device_api_queryset(super().get_queryset())

    # ------------------------------------------------------------------
    # list / retrieve: enrich after pagination so the batch queries cover
    # only the current page (or single object), not the full result set.
    # ------------------------------------------------------------------

    def list(self, request, *args, **kwargs):
        qs = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(qs)

        if page is not None:
            self._lifecycle_map = enrich_devices(page)
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        devices = list(qs)
        self._lifecycle_map = enrich_devices(devices)
        serializer = self.get_serializer(devices, many=True)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        self._lifecycle_map = enrich_devices([instance])
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        # _lifecycle_map is set by list()/retrieve() before serialization.
        # Falls back to empty dict for any other actions (create, update, etc.)
        # that don't need lifecycle data.
        ctx["lifecycle_map"] = getattr(self, "_lifecycle_map", {})
        return ctx


class _EoXReportAPIView(APIView):
    """Base class for read-only EoX report endpoints."""
    permission_classes = [IsAuthenticated]

    def _check_permission(self, request):
        if not request.user.has_perm("dcim.view_device"):
            return Response({"detail": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)
        return None


class EoXSummaryReportAPIView(_EoXReportAPIView):
    def get(self, request):
        if (denied := self._check_permission(request)):
            return denied
        data = _build_eox_report()
        # year_counts is a list of (year, count) tuples — convert to dicts for JSON clarity
        for site in data["sites"]:
            for tenant in site["tenants"]:
                for dt in tenant["device_types"]:
                    dt["year_counts"] = [
                        {"year": y, "count": c} for y, c in dt["year_counts"]
                    ]
        return Response(data)


class EoXByDeviceTypeReportAPIView(_EoXReportAPIView):
    def get(self, request):
        if (denied := self._check_permission(request)):
            return denied
        data = _build_eox_by_device_type_report()
        data.pop("today", None)  # internal helper, not useful to API consumers
        return Response(data)


class EoXByTenantReportAPIView(_EoXReportAPIView):
    def get(self, request):
        if (denied := self._check_permission(request)):
            return denied
        return Response(_build_eox_by_tenant_report())


class EoXByYearReportAPIView(_EoXReportAPIView):
    def get(self, request):
        if (denied := self._check_permission(request)):
            return denied
        return Response(_build_eox_by_year_report())

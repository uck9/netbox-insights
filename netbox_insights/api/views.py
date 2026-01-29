from netbox.api.viewsets import NetBoxModelViewSet
from dcim.models import Device, DeviceType

from netbox_inventory.models.hardware import HardwareLifecycle
from django.contrib.contenttypes.models import ContentType

from ..querysets import device_insights_queryset
from ..filtersets import DeviceInsightsFilterSet
from .serializers import DeviceInsightsSerializer


class DeviceInsightsViewSet(NetBoxModelViewSet):
    queryset = device_insights_queryset(Device.objects.all())
    serializer_class = DeviceInsightsSerializer
    filterset_class = DeviceInsightsFilterSet
    
    def get_queryset(self):
        # IMPORTANT: ensures annotated lifecycle dates exist
        return device_insights_queryset(super().get_queryset())

    def get_serializer_context(self):
        ctx = super().get_serializer_context()

        # IMPORTANT: use the *filtered* queryset, not Device.objects.all()
        qs = self.filter_queryset(self.get_queryset())

        device_type_ids = list(
            qs.values_list("device_type_id", flat=True).distinct()
        )

        dt_ct = ContentType.objects.get_for_model(DeviceType)

        lifecycles = HardwareLifecycle.objects.filter(
            assigned_object_type=dt_ct,
            assigned_object_id__in=device_type_ids,
        )

        # Key by device_type_id ONLY (simple + reliable)
        ctx["lifecycle_map"] = {lc.assigned_object_id: lc for lc in lifecycles}

        return ctx

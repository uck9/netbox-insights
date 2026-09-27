from netbox.object_actions import BulkExport
from netbox.views.generic import ObjectListView
from dcim.models import Device

from ..tables import DeviceInsightsTable
from ..filtersets import DeviceInsightsFilterSet
from ..forms import DeviceInsightsFilterForm
from ..querysets import device_insights_queryset


__all__ = (
    'DeviceInsightsListView',
)


class DeviceInsightsListView(ObjectListView):
    queryset = Device.objects.all()
    table = DeviceInsightsTable
    filterset = DeviceInsightsFilterSet
    filterset_form = DeviceInsightsFilterForm
    template_name = "netbox_insights/device_insights.html"
    actions = (BulkExport,)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return device_insights_queryset(qs)

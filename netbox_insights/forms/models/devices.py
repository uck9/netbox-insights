from django import forms
from netbox.forms import NetBoxModelFilterSetForm

from dcim.models import Site, DeviceRole, Manufacturer, Device


__all__ = (
    'DeviceInsightsFilterForm',
)


class DeviceInsightsFilterForm(NetBoxModelFilterSetForm):
    model = Device
    q = forms.CharField(
        required=False,
        label="Search",
    )

    site_id = forms.ModelMultipleChoiceField(
        queryset=Site.objects.all(),
        required=False,
        label="Site",
    )

    role_id = forms.ModelMultipleChoiceField(
        queryset=DeviceRole.objects.all(),
        required=False,
        label="Device Role",
    )

    manufacturer_id = forms.ModelMultipleChoiceField(
        queryset=Manufacturer.objects.all(),
        required=False,
        label="Manufacturer",
    )

    status = forms.MultipleChoiceField(
        choices=Device._meta.get_field("status").choices,
        required=False,
        label="Status",
    )

    contract_type = forms.MultipleChoiceField(
        choices=[
            ("support_alc", "Support ALC"),
            ("support_ea", "Support EA"),
        ],
        required=False,
        label="Support Contract Type",
    )

    contract_expires_within_days = forms.IntegerField(
        required=False,
        label="Contract expires within (days)",
        min_value=1,
        help_text="Show devices whose primary support contract ends soon",
    )

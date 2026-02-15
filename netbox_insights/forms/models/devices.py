from django import forms
from django.utils.translation import gettext_lazy as _

from netbox.forms import PrimaryModelFilterSetForm
from dcim.models import Site, DeviceRole, Manufacturer, Device, DeviceType

from utilities.forms.rendering import FieldSet


__all__ = (
    'DeviceInsightsFilterForm',
)


class DeviceInsightsFilterForm(PrimaryModelFilterSetForm):
    model = Device
    fieldsets = (
        FieldSet('q'),
        FieldSet('status', 'site_id', 'role_id', 'manufacturer_id', 'device_type', name=_('Device Details')),
        FieldSet('contract_type', 'contract_expires_within_days',name=_('Contracts')),
        FieldSet('owner_id', name=_('Ownership')),
    )
    q = forms.CharField(
        required=False,
        label="Search",
    )
    status = forms.MultipleChoiceField(
        choices=Device._meta.get_field("status").choices,
        required=False,
        label="Status",
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
    device_type = forms.ModelMultipleChoiceField(
        queryset=DeviceType.objects.all(),
        required=False,
        label="Device type",
    )
    contract_type = forms.MultipleChoiceField(
        choices=[
            ("support_alc", "Support ALC"),
            ("support_ea", "Support EA"),
        ],
        required=False,
        label="Support Contract Type",
        help_text="Support Contract Type.",
    )
    contract_expires_within_days = forms.IntegerField(
        required=False,
        label="Contract expires within (days)",
        min_value=1,
        help_text="Show devices whose primary support contract ends soon",
    )

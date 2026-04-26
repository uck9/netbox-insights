from django import forms
from django.conf import settings
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
        FieldSet('eox_overdue', name=_('Hardware Lifecycle')),
        FieldSet('has_active_contract', 'contract_type', 'contract_expires_within_days', name=_('Contracts')),
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
    has_active_contract = forms.NullBooleanField(
        required=False,
        label="Has active contract",
        widget=forms.Select(choices=[
            ('', '---------'),
            (True, 'Yes'),
            (False, 'No'),
        ]),
    )
    eox_overdue = forms.NullBooleanField(
        required=False,
        label="Past end of support date",
        widget=forms.Select(choices=[
            ('', '---------'),
            (True, 'Yes'),
            (False, 'No'),
        ]),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cf_whitelist = {
            f'cf_{name}'
            for name in getattr(settings, 'PLUGINS_CONFIG', {})
            .get('netbox_insights', {})
            .get('device_cf_whitelist', [])
        }
        for field_name in list(self.fields.keys()):
            if field_name.startswith('cf_') and field_name not in cf_whitelist:
                del self.fields[field_name]
                self.custom_fields.pop(field_name, None)
        for group in list(self.custom_field_groups.keys()):
            self.custom_field_groups[group] = [
                f for f in self.custom_field_groups[group] if f in cf_whitelist
            ]
            if not self.custom_field_groups[group]:
                del self.custom_field_groups[group]

from django import forms

from dcim.models import DeviceType, Site
from tenancy.models import Tenant


__all__ = ("EoXReportFilterForm",)

_MULTI = {"class": "form-select form-select-sm", "size": "4"}


class EoXReportFilterForm(forms.Form):
    site = forms.ModelMultipleChoiceField(
        queryset=Site.objects.order_by("name"),
        required=False,
        label="Site",
        widget=forms.SelectMultiple(attrs=_MULTI),
    )
    device_type = forms.ModelMultipleChoiceField(
        queryset=DeviceType.objects.select_related("manufacturer").order_by(
            "manufacturer__name", "model"
        ),
        required=False,
        label="Device Type",
        widget=forms.SelectMultiple(attrs=_MULTI),
    )
    tenant = forms.ModelMultipleChoiceField(
        queryset=Tenant.objects.order_by("name"),
        required=False,
        label="Tenant",
        widget=forms.SelectMultiple(attrs=_MULTI),
    )

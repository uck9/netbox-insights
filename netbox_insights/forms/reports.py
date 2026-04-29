from django import forms

from dcim.models import DeviceType, Manufacturer, Site
from tenancy.models import Tenant


__all__ = ("EoXReportFilterForm", "ContractCoverageFilterForm")

_MULTI = {"class": "form-select form-select-sm", "size": "4"}


class ContractCoverageFilterForm(forms.Form):
    active_only = forms.BooleanField(
        required=False,
        initial=True,
        label="Active devices only",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    site = forms.ModelMultipleChoiceField(
        queryset=Site.objects.order_by("name"),
        required=False,
        label="Site",
        widget=forms.SelectMultiple(attrs=_MULTI),
    )
    manufacturer = forms.ModelMultipleChoiceField(
        queryset=Manufacturer.objects.order_by("name"),
        required=False,
        label="Manufacturer",
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


class EoXReportFilterForm(forms.Form):
    active_only = forms.BooleanField(
        required=False,
        initial=True,
        label="Active devices only",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    site = forms.ModelMultipleChoiceField(
        queryset=Site.objects.order_by("name"),
        required=False,
        label="Site",
        widget=forms.SelectMultiple(attrs=_MULTI),
    )
    manufacturer = forms.ModelMultipleChoiceField(
        queryset=Manufacturer.objects.order_by("name"),
        required=False,
        label="Manufacturer",
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

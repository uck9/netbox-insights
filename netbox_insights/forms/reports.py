from django import forms

from dcim.models import DeviceType, Manufacturer, Site
from tenancy.models import Tenant


__all__ = ("EoXReportFilterForm", "ContractCoverageFilterForm", "AssetReportFilterForm",
           "InstalledAtMismatchFilterForm", "InstalledAtMoveRequestForm", "HardwareBudgetFilterForm",
           "DataValidationFilterForm", "ToBeLocatedFilterForm")

_MULTI = {"class": "form-select form-select-sm", "size": "4"}


class ReportCheckboxInput(forms.CheckboxInput):
    """
    Checkbox for GET report filters. NetBox's checkbox template adds a hidden empty input of the same
    name, and NetBox's GET-form JS deletes empty values by name, dropping the checked value with it.
    """
    template_name = "netbox_insights/widgets/checkbox.html"


class ContractCoverageFilterForm(forms.Form):
    active_only = forms.BooleanField(
        required=False,
        initial=True,
        label="Active devices only",
        widget=ReportCheckboxInput(attrs={"class": "form-check-input"}),
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


class AssetReportFilterForm(forms.Form):
    exclude_retired = forms.BooleanField(
        required=False,
        initial=True,
        label="Exclude retired / disposed",
        widget=ReportCheckboxInput(attrs={"class": "form-check-input"}),
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
    owning_tenant = forms.ModelMultipleChoiceField(
        queryset=Tenant.objects.order_by("name"),
        required=False,
        label="Owning Tenant",
        widget=forms.SelectMultiple(attrs=_MULTI),
    )


class HardwareBudgetFilterForm(AssetReportFilterForm):
    exclude_spare_unassigned = forms.BooleanField(
        required=False,
        initial=True,
        label="Exclude spare / unassigned",
        help_text="Keep only assets attached to a device, or on-site assets marked Used + Allocated",
        widget=ReportCheckboxInput(attrs={"class": "form-check-input"}),
    )


class InstalledAtMismatchFilterForm(forms.Form):
    exclude_retired = forms.BooleanField(
        required=False,
        initial=True,
        label="Exclude retired / disposed",
        widget=ReportCheckboxInput(attrs={"class": "form-check-input"}),
    )
    site = forms.ModelMultipleChoiceField(
        queryset=Site.objects.order_by("name"),
        required=False,
        label="Current Site",
        widget=forms.SelectMultiple(attrs=_MULTI),
    )
    manufacturer = forms.ModelMultipleChoiceField(
        queryset=Manufacturer.objects.order_by("name"),
        required=False,
        label="Manufacturer",
        widget=forms.SelectMultiple(attrs=_MULTI),
    )


class InstalledAtMoveRequestForm(forms.Form):
    """From / to vendor-address pickers for the installed-at move-request generator."""

    from_location = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label="Move devices FROM",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    to_location = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label="Move devices TO",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from netbox_inventory.models import InstalledAtLocation

        qs = InstalledAtLocation.objects.select_related("manufacturer").order_by(
            "manufacturer__name", "vendor_site_id"
        )
        self.fields["from_location"].queryset = qs
        self.fields["to_location"].queryset = qs


class ToBeLocatedFilterForm(forms.Form):
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
    owning_tenant = forms.ModelMultipleChoiceField(
        queryset=Tenant.objects.order_by("name"),
        required=False,
        label="Owning Tenant",
        widget=forms.SelectMultiple(attrs=_MULTI),
    )


class DataValidationFilterForm(forms.Form):
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
        widget=ReportCheckboxInput(attrs={"class": "form-check-input"}),
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

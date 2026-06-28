import django_filters
from django.db.models import Q
from django.utils.timezone import now
from datetime import timedelta

from netbox.filtersets import PrimaryModelFilterSet
from dcim.models import Device, DeviceType, Site, DeviceRole, Manufacturer
from tenancy.models import Tenant

from netbox_inventory.choices import AssetSupportStateChoices


__all__ = (
    'DeviceInsightsFilterSet',
)


class DeviceInsightsFilterSet(PrimaryModelFilterSet):
    q = django_filters.CharFilter(
        method="search",
        label="Search",
    )
    name = django_filters.CharFilter(
        field_name='name',
        lookup_expr='icontains', 
        label="Device Name"
    )
    site_id = django_filters.ModelMultipleChoiceFilter(
        field_name="site",
        queryset=Site.objects.all(),
        label='Site'
    )
    tenant_id = django_filters.ModelMultipleChoiceFilter(
        field_name="tenant",
        queryset=Tenant.objects.all(),
        label='Tenant'
    )
    role_id = django_filters.ModelMultipleChoiceFilter(
        field_name="role",
        queryset=DeviceRole.objects.all(),
        label="Role (ID)",
    )
    manufacturer_id = django_filters.ModelMultipleChoiceFilter(
        queryset=Manufacturer.objects.all(),
        field_name='device_type__manufacturer',
        label="Manufacturer"
    )
    device_type = django_filters.ModelMultipleChoiceFilter(
        queryset=DeviceType.objects.all(),
        field_name='device_type',
        label="Device Type"
    )
    status = django_filters.MultipleChoiceFilter(
        field_name="status",
        choices=Device._meta.get_field("status").choices,
        label="Status",
    )
    has_primary_ip = django_filters.BooleanFilter(
        method='filter_has_primary_ip',
        label='Has Primary IP'
    )
    contract_type = django_filters.MultipleChoiceFilter(
        method="filter_contract_type",
        label="Support Contract Type",
        choices=[
            ("support-alc", "Support ALC"),
            ("support-ea", "Support EA"),
        ],
    )
    contract_expires_within_days = django_filters.NumberFilter(
        method="filter_contract_expiry",
        label="Contract expires within (days)",
    )
    asset_support_state = django_filters.ChoiceFilter(
        field_name="assigned_asset__support_state",
        label="Asset support status",
        choices=AssetSupportStateChoices,
    )
    has_active_contract = django_filters.BooleanFilter(
        method="filter_has_active_contract",
        label="Has active contract",
    )
    eox_overdue = django_filters.BooleanFilter(
        method="filter_eox_overdue",
        label="Past end of support date",
    )

    class Meta:
        model = Device
        fields = (
            "q",
            "name",
            'status',
            "site_id",
            "tenant_id",
            "role_id",
            "manufacturer_id",
            "contract_type",
            "has_primary_ip",
            'owner',
            'asset_support_state',
        )

    def search(self, queryset, name, value):
        """
        Simple free-text search across common reporting fields.
        """
        if not value.strip():
            return queryset

        return queryset.filter(
            Q(name__icontains=value)
            | Q(serial__icontains=value)
            | Q(device_type__model__icontains=value)
            | Q(site__name__icontains=value)
            | Q(tenant__name__icontains=value)
        ).distinct()

    def filter_contract_type(self, queryset, name, value):
        """
        Filter using annotated column:
          primary_contract_type
        """
        if not value:
            return queryset

        return queryset.filter(support_contract_type__in=value)

    def filter_contract_expiry(self, queryset, name, value):
        """
        Filter devices whose primary contract ends within X days.
        Uses annotated column:
          primary_contract_end_date
        """
        if not value:
            return queryset

        today = now().date()
        cutoff = today + timedelta(days=int(value))

        return queryset.filter(
            support_contract_end_date__isnull=False,
            support_contract_end_date__lte=cutoff,
        )

    def filter_has_primary_ip(self, queryset, name, value):
        if value:
            return queryset.exclude(primary_ip4__isnull=True, primary_ip6__isnull=True)
        else:
            return queryset.filter(primary_ip4__isnull=True, primary_ip6__isnull=True)

    def filter_has_active_contract(self, queryset, name, value):
        if value:
            return queryset.filter(support_contract_type__isnull=False)
        else:
            return queryset.filter(support_contract_type__isnull=True)

    def filter_eox_overdue(self, queryset, name, value):
        today = now().date()
        if value:
            return queryset.filter(
                tracked_eox_date__isnull=False,
                tracked_eox_date__lt=today,
            )
        else:
            return queryset.exclude(
                tracked_eox_date__isnull=False,
                tracked_eox_date__lt=today,
            )
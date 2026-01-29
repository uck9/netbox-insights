from datetime import date

from django.contrib.contenttypes.models import ContentType
from django.db.models import OuterRef, Subquery, Q, Case, When, DateField, F, Value
from django.db.models.functions import Concat, Coalesce
from django.utils.timezone import now

from dcim.models import Device
from netbox_inventory.models import hardware
from netbox_inventory.models.contracts import ContractAssignment

SUPPORT_CONTRACT_TYPES = ("support-alc", "support-ea")


__all__ = (
    'device_insights_queryset',
)


def device_insights_queryset(base_qs=None):
    qs = base_qs or Device.objects.all()

    device_type_ct = ContentType.objects.get_for_model(
        Device._meta.get_field("device_type").related_model
    )

    lifecycle_qs = hardware.HardwareLifecycle.objects.filter(
        assigned_object_type=device_type_ct,
        assigned_object_id=OuterRef("device_type_id"),
    )

    lifecycle_qs = hardware.HardwareLifecycle.objects.filter(
        assigned_object_type=device_type_ct,
        assigned_object_id=OuterRef("device_type_id"),
    ).annotate(
        tracked_eox_date=Case(
            When(support_basis="security", then=F("end_of_security")),
            When(support_basis="support", then=F("end_of_support")),
            default=F("end_of_support"),
            output_field=DateField(),
        )
    )

    today = now().date()

    # Use DB-expression equivalent of ContractAssignment.effective_end_date
    active_support_assignments = (
        ContractAssignment.objects.filter(
            asset__device_id=OuterRef("pk"),
            contract__contract_type__in=SUPPORT_CONTRACT_TYPES,
        )
        .annotate(_effective_end_date=Coalesce("end_date", "contract__end_date"))
        .filter(
            Q(_effective_end_date__gte=today)
            | (Q(end_date__isnull=True) & Q(contract__end_date__isnull=True))
        )
        .order_by(Coalesce("_effective_end_date", date.max).desc(), "-pk")
    )

    return (
        qs.annotate(
            tracked_eox_date=Subquery(lifecycle_qs.values("tracked_eox_date")[:1]),
            hw_end_of_sale=Subquery(lifecycle_qs.values("end_of_sale")[:1]),
            hw_end_of_support=Subquery(lifecycle_qs.values("end_of_support")[:1]),
            hw_end_of_security=Subquery(lifecycle_qs.values("end_of_security")[:1]),
            tracked_eox_basis=Subquery(lifecycle_qs.values("support_basis")[:1]),

            support_contract_pk = Subquery(
                active_support_assignments.values("contract_id")[:1]
            ),
            support_contract_id=Subquery(
                active_support_assignments.values("contract__contract_id")[:1]
            ),
            support_contract_type=Subquery(
                active_support_assignments.values("contract__contract_type")[:1]
            ),
            support_contract_end_date=Subquery(
                active_support_assignments.values("_effective_end_date")[:1]
            ),
            support_contract_sku=Subquery(
                active_support_assignments.values("sku__sku")[:1]
            ),
            support_contract_sku_desc=Subquery(
                active_support_assignments.values("sku__description")[:1]
            ),
            support_contract_sku_display=Case(
                # No SKU at all → em dash
                When(
                    support_contract_sku__isnull=True,
                    then=Value("—"),
                ),
                When(
                    support_contract_sku="",
                    then=Value("—"),
                ),

                # SKU exists but no description → just SKU
                When(
                    support_contract_sku_desc__isnull=True,
                    then="support_contract_sku",
                ),
                When(
                    support_contract_sku_desc="",
                    then="support_contract_sku",
                ),

                # Both exist → SKU (description)
                default=Concat(
                    "support_contract_sku",
                    Value(" ("),
                    "support_contract_sku_desc",
                    Value(")"),
                ),
            )
        )
        .select_related("device_type__manufacturer", "site", "tenant", "role")
        .exclude(status="unmanaged")
        .exclude(status="passive")
    )
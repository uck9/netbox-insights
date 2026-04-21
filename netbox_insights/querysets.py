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
    'device_api_queryset',
    'enrich_devices',
)


def _get_device_type_ct():
    return ContentType.objects.get_for_model(
        Device._meta.get_field("device_type").related_model
    )


def _active_support_assignments_subquery():
    today = now().date()
    return (
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


def device_insights_queryset(base_qs=None):
    """Full annotated queryset used by the UI table."""
    qs = base_qs or Device.objects.all()

    device_type_ct = _get_device_type_ct()

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

    active_support_assignments = _active_support_assignments_subquery()

    return (
        qs.annotate(
            tracked_eox_date=Subquery(lifecycle_qs.values("tracked_eox_date")[:1]),
            hw_end_of_sale=Subquery(lifecycle_qs.values("end_of_sale")[:1]),
            hw_end_of_support=Subquery(lifecycle_qs.values("end_of_support")[:1]),
            hw_end_of_security=Subquery(lifecycle_qs.values("end_of_security")[:1]),
            tracked_eox_basis=Subquery(lifecycle_qs.values("support_basis")[:1]),

            support_contract_pk=Subquery(
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
                When(support_contract_sku__isnull=True, then=Value("—")),
                When(support_contract_sku="", then=Value("—")),
                When(support_contract_sku_desc__isnull=True, then="support_contract_sku"),
                When(support_contract_sku_desc="", then="support_contract_sku"),
                default=Concat(
                    "support_contract_sku",
                    Value(" ("),
                    "support_contract_sku_desc",
                    Value(")"),
                ),
            )
        )
        .select_related("device_type__manufacturer", "site", "tenant", "tenant__group", "role")
        .prefetch_related("assigned_asset")
        .exclude(status="unmanaged")
        .exclude(status="passive")
    )


def device_api_queryset(base_qs=None):
    """
    Lean queryset for the API. Emits three correlated subqueries per row
    instead of eleven — lifecycle and full contract details are populated in
    Python by enrich_devices() after pagination.

    The three annotations kept here are:
      support_assignment_pk   — used by enrich_devices() for the batch lookup
      support_contract_type   — needed so filter_contract_type() works
      support_contract_end_date — needed so filter_contract_expiry() works
    """
    qs = base_qs or Device.objects.all()
    active_support_assignments = _active_support_assignments_subquery()

    return (
        qs.annotate(
            support_assignment_pk=Subquery(
                active_support_assignments.values("pk")[:1]
            ),
            support_contract_type=Subquery(
                active_support_assignments.values("contract__contract_type")[:1]
            ),
            support_contract_end_date=Subquery(
                active_support_assignments.values("_effective_end_date")[:1]
            ),
        )
        .select_related("device_type__manufacturer", "site", "tenant", "tenant__group", "role")
        .exclude(status="unmanaged")
        .exclude(status="passive")
    )


def enrich_devices(devices):
    """
    Batch-load lifecycle and contract data for a page of Device instances and
    set attributes so serializers read them without any per-row subqueries.

    Replaces up to 11 correlated subqueries per row with two flat batch queries
    for the entire page.

    Returns the lifecycle_map (device_type_id → HardwareLifecycle) for use in
    serializer context (is_supported, calc_budget_year, etc.).
    """
    if not devices:
        return {}

    # --- Lifecycle (one query keyed by device_type_id) ---
    device_type_ct = _get_device_type_ct()
    device_type_ids = {d.device_type_id for d in devices}

    lifecycle_map = {
        lc.assigned_object_id: lc
        for lc in hardware.HardwareLifecycle.objects.filter(
            assigned_object_type=device_type_ct,
            assigned_object_id__in=device_type_ids,
        )
    }

    for device in devices:
        lc = lifecycle_map.get(device.device_type_id)
        if lc:
            basis = lc.support_basis
            device.tracked_eox_date = (
                lc.end_of_security if basis == "security" else lc.end_of_support
            )
            device.hw_end_of_sale = lc.end_of_sale
            device.hw_end_of_support = lc.end_of_support
            device.hw_end_of_security = lc.end_of_security
            device.tracked_eox_basis = basis
        else:
            device.tracked_eox_date = None
            device.hw_end_of_sale = None
            device.hw_end_of_support = None
            device.hw_end_of_security = None
            device.tracked_eox_basis = None

    # --- Contracts (one query keyed by ContractAssignment pk) ---
    assignment_pks = [
        d.support_assignment_pk
        for d in devices
        if getattr(d, "support_assignment_pk", None)
    ]

    assignment_map = {}
    if assignment_pks:
        assignment_map = {
            a.pk: a
            for a in ContractAssignment.objects.filter(
                pk__in=assignment_pks
            ).select_related("contract", "sku")
        }

    for device in devices:
        pk = getattr(device, "support_assignment_pk", None)
        a = assignment_map.get(pk) if pk else None

        device.support_contract_pk = a.contract_id if a else None
        device.support_contract_id = a.contract.contract_id if a and a.contract else None
        device.support_contract_type = a.contract.contract_type if a and a.contract else None
        device.support_contract_end_date = (
            a.end_date or (a.contract.end_date if a.contract else None)
        ) if a else None

        sku = a.sku if a else None
        device.support_contract_sku = sku.sku if sku else None
        device.support_contract_sku_desc = sku.description if sku else None

        s = device.support_contract_sku
        desc = device.support_contract_sku_desc
        if not s:
            device.support_contract_sku_display = "—"
        elif not desc:
            device.support_contract_sku_display = s
        else:
            device.support_contract_sku_display = f"{s} ({desc})"

    return lifecycle_map

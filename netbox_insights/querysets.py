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
    """Active (non-expired) contracts, soonest expiry first. Perpetual contracts sort last."""
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
        .order_by(Coalesce("_effective_end_date", date.max), "pk")
    )


def _expired_support_assignment_subquery():
    """Expired contracts only, most recently expired first. Used as a fallback for table display
    when a device has no active contracts."""
    today = now().date()
    return (
        ContractAssignment.objects.filter(
            asset__device_id=OuterRef("pk"),
            contract__contract_type__in=SUPPORT_CONTRACT_TYPES,
        )
        .annotate(_effective_end_date=Coalesce("end_date", "contract__end_date"))
        .filter(_effective_end_date__lt=today)
        .order_by(F("_effective_end_date").desc(), "-pk")
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

    active_qs = _active_support_assignments_subquery()
    expired_qs = _expired_support_assignment_subquery()

    def _contract_field(field):
        """Return active contract field, falling back to most-recently-expired when no active contract exists."""
        return Coalesce(
            Subquery(active_qs.values(field)[:1]),
            Subquery(expired_qs.values(field)[:1]),
        )

    return (
        qs.annotate(
            tracked_eox_date=Subquery(lifecycle_qs.values("tracked_eox_date")[:1]),
            hw_end_of_sale=Subquery(lifecycle_qs.values("end_of_sale")[:1]),
            hw_end_of_support=Subquery(lifecycle_qs.values("end_of_support")[:1]),
            hw_end_of_security=Subquery(lifecycle_qs.values("end_of_security")[:1]),
            tracked_eox_basis=Subquery(lifecycle_qs.values("support_basis")[:1]),

            support_contract_pk=_contract_field("contract_id"),
            support_contract_id=_contract_field("contract__contract_id"),
            support_contract_type=_contract_field("contract__contract_type"),
            support_contract_end_date=_contract_field("_effective_end_date"),
            support_contract_sku=_contract_field("sku__sku"),
            support_contract_sku_desc=_contract_field("sku__description"),
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
    Lean queryset for the API. Lifecycle details are populated in Python by
    enrich_devices() after pagination; annotations here exist only so the
    shared filterset can filter on them.

    Annotations:
      tracked_eox_date          — filter_eox_overdue()
      support_contract_type     — filter_contract_type()
      support_contract_end_date — filter_contract_expiry()
    """
    qs = base_qs or Device.objects.all()
    device_type_ct = _get_device_type_ct()
    active_support_assignments = _active_support_assignments_subquery()

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

    return (
        qs.annotate(
            tracked_eox_date=Subquery(lifecycle_qs.values("tracked_eox_date")[:1]),
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

    # --- Contracts (one query for all devices on this page) ---
    # Fetch ALL support assignments (active and expired) so we can apply the
    # same "active first, expired fallback" logic as the table queryset.
    today = now().date()
    device_pks = [d.pk for d in devices]

    all_assignments = list(
        ContractAssignment.objects.filter(
            asset__device_id__in=device_pks,
            contract__contract_type__in=SUPPORT_CONTRACT_TYPES,
        )
        .annotate(
            _effective_end_date=Coalesce("end_date", "contract__end_date"),
            device_id=F("asset__device_id"),
        )
        .select_related("contract", "sku")
    )

    assignments_by_device: dict[int, list] = {d.pk: [] for d in devices}
    for a in all_assignments:
        assignments_by_device[a.device_id].append(a)

    for device in devices:
        assignments = assignments_by_device.get(device.pk, [])

        active = [
            a for a in assignments
            if a._effective_end_date is None or a._effective_end_date >= today
        ]
        if active:
            device.support_contracts_list = sorted(
                active,
                key=lambda a: (a._effective_end_date or date.max),
            )
        else:
            expired = [a for a in assignments if a._effective_end_date and a._effective_end_date < today]
            device.support_contracts_list = sorted(
                expired,
                key=lambda a: a._effective_end_date,
                reverse=True,
            )

    return lifecycle_map

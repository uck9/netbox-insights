from collections import defaultdict

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Count, Exists, Prefetch, Q
from django.shortcuts import render
from django.views import View

from .asset_reports import _resolve_site
from .reports import _csv_response, _coverage_status, _asset_state_subquery
from ..querysets import device_insights_queryset, _active_support_assignments_subquery

__all__ = ('DataValidationReportView',)

# Only these device statuses are in scope for every tab of this report — retired,
# decommissioning, offline, failed, and inventory devices are noise for data-quality
# purposes (per explicit product decision, applied consistently across all 4 tabs).
ACTIVE_STATUSES = ("active", "planned", "staged")


# ── Shared helpers ────────────────────────────────────────────────────────────

def _dv_base_qs(site_ids=None, manufacturer_ids=None, device_type_ids=None, tenant_ids=None):
    qs = (
        device_insights_queryset()
        .filter(status__in=ACTIVE_STATUSES)
        .select_related("site", "tenant", "device_type__manufacturer")
        .order_by("site__name", "name")
    )
    if site_ids:
        qs = qs.filter(site_id__in=site_ids)
    if manufacturer_ids:
        qs = qs.filter(device_type__manufacturer_id__in=manufacturer_ids)
    if device_type_ids:
        qs = qs.filter(device_type_id__in=device_type_ids)
    if tenant_ids:
        qs = qs.filter(tenant_id__in=tenant_ids)
    return qs


def _device_row_base(device):
    mfr = device.device_type.manufacturer.name if device.device_type and device.device_type.manufacturer else ""
    model = device.device_type.model if device.device_type else ""
    return {
        "pk": device.pk,
        "name": device.name or f"Device {device.pk}",
        "status": device.status,
        "status_display": device.get_status_display(),
        "status_color": device.get_status_color(),
        "site_pk": device.site_id,
        "site_name": device.site.name if device.site else "—",
        "device_type_pk": device.device_type_id,
        "device_type": f"{mfr} {model}".strip() if mfr else model,
    }


# ── Tab 1: Asset ID / Serial Match ─────────────────────────────────────────────

def _build_id_match(site_ids=None, manufacturer_ids=None, device_type_ids=None, tenant_ids=None):
    qs = _dv_base_qs(site_ids, manufacturer_ids, device_type_ids, tenant_ids).filter(
        assigned_asset__isnull=False
    )

    total = 0
    asset_id_matched = 0
    serial_matched = 0
    rows = []

    for device in qs.iterator(chunk_size=2000):
        asset = device.assigned_asset
        total += 1

        dev_tag = (device.asset_tag or "").strip()
        ast_tag = (asset.asset_tag or "").strip()
        # Blank on either side counts as unmatched — can't confirm a match without both present.
        id_match = bool(dev_tag) and bool(ast_tag) and dev_tag == ast_tag

        dev_serial = (device.serial or "").strip()
        ast_serial = (asset.serial or "").strip()
        serial_match = bool(dev_serial) and bool(ast_serial) and dev_serial == ast_serial

        if id_match:
            asset_id_matched += 1
        if serial_match:
            serial_matched += 1

        if not id_match or not serial_match:
            row = _device_row_base(device)
            row.update({
                "asset_pk": asset.pk,
                "device_asset_tag": device.asset_tag or "—",
                "asset_asset_tag": asset.asset_tag or "—",
                "id_match": id_match,
                "device_serial": device.serial or "—",
                "asset_serial": asset.serial or "—",
                "serial_match": serial_match,
            })
            rows.append(row)

    id_pct, id_status = _coverage_status(asset_id_matched, total)
    serial_pct, serial_status = _coverage_status(serial_matched, total)

    return {
        "total": total,
        "asset_id_matched": asset_id_matched,
        "asset_id_unmatched": total - asset_id_matched,
        "asset_id_pct": id_pct,
        "asset_id_status": id_status,
        "serial_matched": serial_matched,
        "serial_unmatched": total - serial_matched,
        "serial_pct": serial_pct,
        "serial_status": serial_status,
        "rows": rows,
    }


# ── Tab 2: No Asset Attached ───────────────────────────────────────────────────

def _build_no_asset(site_ids=None, manufacturer_ids=None, device_type_ids=None, tenant_ids=None):
    qs = _dv_base_qs(site_ids, manufacturer_ids, device_type_ids, tenant_ids).filter(
        assigned_asset__isnull=True
    )

    groups = defaultdict(list)
    for device in qs.iterator(chunk_size=2000):
        groups[device.status].append(_device_row_base(device))

    statuses = []
    for status in ACTIVE_STATUSES:
        devices = groups.get(status)
        if not devices:
            continue
        statuses.append({
            "status": status,
            "status_display": devices[0]["status_display"],
            "status_color": devices[0]["status_color"],
            "devices": devices,
            "total": len(devices),
        })

    return {"statuses": statuses, "total": sum(s["total"] for s in statuses)}


# ── Tab 3: No Attached Contract ────────────────────────────────────────────────

def _build_no_contract(site_ids=None, manufacturer_ids=None, device_type_ids=None, tenant_ids=None):
    qs = (
        _dv_base_qs(site_ids, manufacturer_ids, device_type_ids, tenant_ids)
        .annotate(
            _has_active_contract=Exists(_active_support_assignments_subquery()),
            _asset_support_state=_asset_state_subquery(),
        )
        .filter(_has_active_contract=False)
        # Assets intentionally marked Excluded (lab/spare/decommission_planned, etc.) are
        # not a real coverage gap — drop them rather than flagging them as missing a contract.
        # NULL-safe: plain .exclude(_asset_support_state="excluded") would also drop devices
        # with no asset at all, since SQL's NOT (NULL = 'excluded') is NULL, not true.
        .filter(Q(_asset_support_state__isnull=True) | ~Q(_asset_support_state="excluded"))
    )

    rows = []
    for device in qs.iterator(chunk_size=2000):
        # Reverse OneToOneField access raises RelatedObjectDoesNotExist (not just
        # returning None) when no asset is attached — getattr's default avoids that.
        asset = getattr(device, 'assigned_asset', None)
        row = _device_row_base(device)
        row.update({
            "has_asset": asset is not None,
            "asset_pk": asset.pk if asset else None,
            "asset_tag": (asset.asset_tag if asset else None) or "—",
        })
        rows.append(row)

    return {"rows": rows, "total": len(rows)}


# ── Tab 4: Warranty-Only Support ───────────────────────────────────────────────

def _build_warranty_only(site_ids=None, manufacturer_ids=None, device_type_ids=None, tenant_ids=None):
    qs = _dv_base_qs(site_ids, manufacturer_ids, device_type_ids, tenant_ids).filter(
        assigned_asset__isnull=False
    ).order_by("device_type__manufacturer__name", "device_type__model", "site__name", "name")

    dt_groups = defaultdict(list)
    dt_info = {}

    for device in qs.iterator(chunk_size=2000):
        asset = device.assigned_asset
        if asset.support_reason != "covered_warranty":
            continue

        dt_pk = device.device_type_id
        if dt_pk not in dt_info:
            mfr = device.device_type.manufacturer.name if device.device_type and device.device_type.manufacturer else ""
            model = device.device_type.model if device.device_type else ""
            dt_info[dt_pk] = {
                "pk": dt_pk,
                "name": f"{mfr} {model}".strip() if mfr else model,
            }

        row = _device_row_base(device)
        row.update({
            "asset_pk": asset.pk,
            "warranty_type": asset.get_warranty_type_display() if asset.warranty_type else "—",
            "warranty_end": asset.warranty_end,
        })
        dt_groups[dt_pk].append(row)

    device_types = []
    for dt_pk in sorted(dt_groups.keys(), key=lambda x: dt_info[x]["name"]):
        devices = dt_groups[dt_pk]
        device_types.append({**dt_info[dt_pk], "devices": devices, "total": len(devices)})

    return {"device_types": device_types, "total": sum(g["total"] for g in device_types)}


# ── Tab 5: Duplicate Serial Numbers ────────────────────────────────────────────
# Not scoped to ACTIVE_STATUSES like the other tabs — a duplicate/typo'd serial matters
# regardless of whether the asset is currently deployed on an in-scope device. Excludes
# retired/disposed assets only, matching this plugin's existing exclude_retired convention.
# The DB already enforces uniqueness per (device_type, serial), so any duplicate found
# here necessarily spans two different device types — usually a copy-paste/typo error.

def _build_duplicate_serials(site_ids=None, manufacturer_ids=None, device_type_ids=None, tenant_ids=None):
    from netbox_inventory.models import Asset

    qs = (
        Asset.objects.exclude(status__in=["retired", "disposed"])
        .exclude(serial__isnull=True)
        .exclude(serial="")
        .select_related("device_type__manufacturer", "device__site", "installed_site_override", "owning_tenant")
    )
    if manufacturer_ids:
        qs = qs.filter(device_type__manufacturer_id__in=manufacturer_ids)
    if device_type_ids:
        qs = qs.filter(device_type_id__in=device_type_ids)
    if tenant_ids:
        qs = qs.filter(owning_tenant_id__in=tenant_ids)
    if site_ids:
        qs = qs.filter(
            Q(device__site_id__in=site_ids) |
            Q(device__isnull=True, installed_site_override_id__in=site_ids)
        )

    dup_serials = list(
        qs.values("serial").annotate(n=Count("id")).filter(n__gt=1).values_list("serial", flat=True)
    )
    if not dup_serials:
        return {"groups": [], "total": 0}

    groups_map = defaultdict(list)
    for asset in qs.filter(serial__in=dup_serials).order_by("serial", "pk").iterator(chunk_size=2000):
        mfr = asset.device_type.manufacturer.name if asset.device_type and asset.device_type.manufacturer else ""
        model = asset.device_type.model if asset.device_type else ""
        site = _resolve_site(asset)
        groups_map[asset.serial].append({
            "pk": asset.pk,
            "asset_tag": asset.asset_tag or "—",
            "status": asset.get_status_display(),
            "device_type_pk": asset.device_type_id,
            "device_type": f"{mfr} {model}".strip() if mfr else model,
            "device_pk": asset.device_id,
            "device_name": asset.device.name if asset.device_id and asset.device else None,
            "site_name": site.name if site else "—",
            "owning_tenant": asset.owning_tenant.name if asset.owning_tenant else "—",
        })

    groups = [
        {"serial": serial, "assets": groups_map[serial], "total": len(groups_map[serial])}
        for serial in sorted(groups_map.keys())
    ]
    return {"groups": groups, "total": sum(g["total"] for g in groups)}


# ── Tab 6: Device Type Mismatch ────────────────────────────────────────────────

def _build_device_type_mismatch(site_ids=None, manufacturer_ids=None, device_type_ids=None, tenant_ids=None):
    from netbox_inventory.models import Asset

    qs = _dv_base_qs(site_ids, manufacturer_ids, device_type_ids, tenant_ids).filter(
        assigned_asset__isnull=False
    ).prefetch_related(None).prefetch_related(
        Prefetch('assigned_asset', queryset=Asset.objects.select_related('device_type__manufacturer'))
    )

    rows = []
    for device in qs.iterator(chunk_size=2000):
        asset = device.assigned_asset
        if asset.device_type_id == device.device_type_id:
            continue
        a_mfr = asset.device_type.manufacturer.name if asset.device_type and asset.device_type.manufacturer else ""
        a_model = asset.device_type.model if asset.device_type else ""
        row = _device_row_base(device)
        row.update({
            "asset_pk": asset.pk,
            "asset_tag": asset.asset_tag or "—",
            "asset_device_type_pk": asset.device_type_id,
            "asset_device_type": f"{a_mfr} {a_model}".strip() if a_mfr else a_model,
        })
        rows.append(row)

    return {"rows": rows, "total": len(rows)}


# ── Tab 7: Disposed/Retired Asset on an In-Scope Device ───────────────────────

def _build_stale_asset_status(site_ids=None, manufacturer_ids=None, device_type_ids=None, tenant_ids=None):
    qs = _dv_base_qs(site_ids, manufacturer_ids, device_type_ids, tenant_ids).filter(
        assigned_asset__isnull=False
    )

    rows = []
    for device in qs.iterator(chunk_size=2000):
        asset = device.assigned_asset
        if asset.status not in ("retired", "disposed"):
            continue
        row = _device_row_base(device)
        row.update({
            "asset_pk": asset.pk,
            "asset_tag": asset.asset_tag or "—",
            "asset_status": asset.get_status_display(),
            "asset_status_color": asset.get_status_color(),
        })
        rows.append(row)

    return {"rows": rows, "total": len(rows)}


# ── Tab 8: Missing Owning Tenant ───────────────────────────────────────────────

def _build_missing_tenant(site_ids=None, manufacturer_ids=None, device_type_ids=None, tenant_ids=None):
    qs = _dv_base_qs(site_ids, manufacturer_ids, device_type_ids, tenant_ids).filter(
        assigned_asset__isnull=False
    )

    rows = []
    for device in qs.iterator(chunk_size=2000):
        asset = device.assigned_asset
        if asset.owning_tenant_id is not None:
            continue
        row = _device_row_base(device)
        row.update({
            "asset_pk": asset.pk,
            "asset_tag": asset.asset_tag or "—",
        })
        rows.append(row)

    return {"rows": rows, "total": len(rows)}


# ── CSV exports ───────────────────────────────────────────────────────────────

def _id_match_csv(data):
    response, writer = _csv_response("data_validation_id_serial_match.csv")
    writer.writerow(["Device", "Status", "Site", "Device Type", "Device Asset Tag", "Asset's Asset Tag",
                     "Asset ID Match", "Device Serial", "Asset Serial", "Serial Match"])
    for r in data.get("rows", []):
        writer.writerow([
            r["name"], r["status_display"], r["site_name"], r["device_type"],
            r["device_asset_tag"], r["asset_asset_tag"], "Yes" if r["id_match"] else "No",
            r["device_serial"], r["asset_serial"], "Yes" if r["serial_match"] else "No",
        ])
    return response


def _no_asset_csv(data):
    response, writer = _csv_response("data_validation_no_asset_attached.csv")
    writer.writerow(["Device Status", "Device", "Site", "Device Type"])
    for group in data.get("statuses", []):
        for d in group["devices"]:
            writer.writerow([group["status_display"], d["name"], d["site_name"], d["device_type"]])
    return response


def _no_contract_csv(data):
    response, writer = _csv_response("data_validation_no_contract.csv")
    writer.writerow(["Device", "Status", "Site", "Device Type", "Asset Attached", "Asset Tag"])
    for r in data.get("rows", []):
        writer.writerow([
            r["name"], r["status_display"], r["site_name"], r["device_type"],
            "Yes" if r["has_asset"] else "No", r["asset_tag"],
        ])
    return response


def _warranty_only_csv(data):
    response, writer = _csv_response("data_validation_warranty_only.csv")
    writer.writerow(["Device Type", "Device", "Status", "Site", "Warranty Type", "Warranty End"])
    for group in data.get("device_types", []):
        for d in group["devices"]:
            writer.writerow([
                group["name"], d["name"], d["status_display"], d["site_name"],
                d["warranty_type"], d["warranty_end"] or "",
            ])
    return response


def _duplicate_serials_csv(data):
    response, writer = _csv_response("data_validation_duplicate_serials.csv")
    writer.writerow(["Serial", "Asset Tag", "Status", "Device Type", "Device", "Site", "Owning Tenant"])
    for group in data.get("groups", []):
        for a in group["assets"]:
            writer.writerow([
                group["serial"], a["asset_tag"], a["status"], a["device_type"],
                a["device_name"] or "—", a["site_name"], a["owning_tenant"],
            ])
    return response


def _device_type_mismatch_csv(data):
    response, writer = _csv_response("data_validation_device_type_mismatch.csv")
    writer.writerow(["Device", "Status", "Site", "Device's Device Type", "Asset Tag", "Asset's Device Type"])
    for r in data.get("rows", []):
        writer.writerow([
            r["name"], r["status_display"], r["site_name"], r["device_type"],
            r["asset_tag"], r["asset_device_type"],
        ])
    return response


def _stale_asset_status_csv(data):
    response, writer = _csv_response("data_validation_stale_asset_status.csv")
    writer.writerow(["Device", "Device Status", "Site", "Device Type", "Asset Tag", "Asset Status"])
    for r in data.get("rows", []):
        writer.writerow([
            r["name"], r["status_display"], r["site_name"], r["device_type"],
            r["asset_tag"], r["asset_status"],
        ])
    return response


def _missing_tenant_csv(data):
    response, writer = _csv_response("data_validation_missing_tenant.csv")
    writer.writerow(["Device", "Status", "Site", "Device Type", "Asset Tag"])
    for r in data.get("rows", []):
        writer.writerow([r["name"], r["status_display"], r["site_name"], r["device_type"], r["asset_tag"]])
    return response


_DATA_VALIDATION_CONFIG = {
    "id_serial_match": ("Asset ID / Serial Match", _build_id_match, _id_match_csv),
    "no_asset": ("No Asset Attached", _build_no_asset, _no_asset_csv),
    "no_contract": ("No Attached Contract", _build_no_contract, _no_contract_csv),
    "warranty_only": ("Warranty-Only Support", _build_warranty_only, _warranty_only_csv),
    "duplicate_serials": ("Duplicate Serial Numbers", _build_duplicate_serials, _duplicate_serials_csv),
    "device_type_mismatch": ("Device Type Mismatch", _build_device_type_mismatch, _device_type_mismatch_csv),
    "stale_asset_status": ("Disposed/Retired Asset on Active Device", _build_stale_asset_status, _stale_asset_status_csv),
    "missing_tenant": ("Missing Owning Tenant", _build_missing_tenant, _missing_tenant_csv),
}


class DataValidationReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'dcim.view_device'
    template_name = 'netbox_insights/data_validation_report.html'

    def get(self, request):
        from ..forms.reports import DataValidationFilterForm

        report_key = request.GET.get('report', 'id_serial_match')
        if report_key not in _DATA_VALIDATION_CONFIG:
            report_key = 'id_serial_match'

        form = DataValidationFilterForm(request.GET or None)
        submitted = 'submitted' in request.GET

        filters = {}
        if form.is_valid():
            if sites := form.cleaned_data.get('site'):
                filters['site_ids'] = [s.pk for s in sites]
            if manufacturers := form.cleaned_data.get('manufacturer'):
                filters['manufacturer_ids'] = [m.pk for m in manufacturers]
            if device_types := form.cleaned_data.get('device_type'):
                filters['device_type_ids'] = [d.pk for d in device_types]
            if tenants := form.cleaned_data.get('tenant'):
                filters['tenant_ids'] = [t.pk for t in tenants]

        label, builder, csv_func = _DATA_VALIDATION_CONFIG[report_key]
        data = builder(**filters) if submitted else {}

        if submitted and request.GET.get('format') == 'csv':
            return csv_func(data)

        tab_urls = {}
        for key in _DATA_VALIDATION_CONFIG:
            params = request.GET.copy()
            params['report'] = key
            params.pop('format', None)
            tab_urls[key] = '?' + params.urlencode()

        csv_params = request.GET.copy()
        csv_params['format'] = 'csv'

        return render(request, self.template_name, {
            **data,
            'form': form,
            'report_key': report_key,
            'report_label': label,
            'tab_urls': tab_urls,
            'csv_url': '?' + csv_params.urlencode(),
            'submitted': submitted,
        })

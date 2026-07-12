import csv
from collections import defaultdict
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Count, Q
from django.shortcuts import render
from django.utils.timezone import now
from django.views import View

from .asset_reports import _resolve_site
from .reports import _csv_response

__all__ = ('LicenseBudgetReportView',)


def _filtered_license_qs(model, site_ids=None, device_type_ids=None, owning_tenant_ids=None,
                          manufacturer_ids=None, exclude_retired=True):
    """Shared asset/sku-scoped filtering — used for both AssetLicense and LicenseBundle,
    which have identical relevant field names (asset, sku, end_date)."""
    qs = (
        model.objects.filter(end_date__isnull=False)
        .select_related(
            "sku__manufacturer",
            "asset__owning_tenant",
            "asset__device__site",
            "asset__installed_site_override",
        )
        .order_by("end_date", "sku__manufacturer__name", "sku__sku")
    )
    if exclude_retired:
        qs = qs.exclude(asset__status__in=["retired", "disposed"])
    if site_ids:
        qs = qs.filter(
            Q(asset__device__site_id__in=site_ids) |
            Q(asset__device__isnull=True, asset__installed_site_override_id__in=site_ids)
        )
    if manufacturer_ids:
        qs = qs.filter(sku__manufacturer_id__in=manufacturer_ids)
    if device_type_ids:
        qs = qs.filter(asset__device_type_id__in=device_type_ids)
    if owning_tenant_ids:
        qs = qs.filter(asset__owning_tenant_id__in=owning_tenant_ids)
    return qs


def _license_qs(site_ids=None, device_type_ids=None, owning_tenant_ids=None,
                manufacturer_ids=None, exclude_retired=True):
    from netbox_inventory.models.licenses import AssetLicense
    return _filtered_license_qs(
        AssetLicense, site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
    )


def _bundle_qs(site_ids=None, device_type_ids=None, owning_tenant_ids=None,
               manufacturer_ids=None, exclude_retired=True):
    """LicenseBundle rows — the priced 'parent' for bundled feature AssetLicense
    rows (AssetLicense.bundle set). See _build_license_budget_by_year/_by_device:
    a bundle is priced once via its own sku.renewal_budget_per_unit instead of
    pricing (or leaving as 'missing budget') each of its component feature SKUs."""
    from netbox_inventory.models.licenses import LicenseBundle
    return _filtered_license_qs(
        LicenseBundle, site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
    )


def _budget_year_status(budget_year, current_year):
    if budget_year < current_year:
        return "danger"
    if budget_year == current_year:
        return "warning"
    return "success"


def _accumulate_sku_bucket(by_year, ot_names, end_date, sku, quantity, ot_pk, ot_name):
    """Shared aggregation for _build_license_budget_by_year — called once per
    unbundled AssetLicense (priced via its own SKU) and once per LicenseBundle
    (priced via its bundle SKU), so both land in the same year -> sku rollup."""
    year = end_date.year
    unit_budget = sku.renewal_budget_per_unit
    ot_names[ot_pk] = ot_name

    bucket = by_year[year]
    sku_pk = sku.pk
    if sku_pk not in bucket:
        bucket[sku_pk] = {
            "sku_pk": sku_pk,
            "sku": sku.sku,
            "sku_name": sku.name,
            "manufacturer": sku.manufacturer.name if sku.manufacturer else "",
            "license_kind": sku.license_kind,
            "unit_budget": unit_budget,
            "total_quantity": 0,
            "total_budget": Decimal("0") if unit_budget is not None else None,
            "missing_budget": unit_budget is None,
            "ot_counts": defaultdict(int),
        }
    entry = bucket[sku_pk]
    entry["total_quantity"] += quantity
    if unit_budget is None:
        entry["missing_budget"] = True
        entry["total_budget"] = None
    elif entry["total_budget"] is not None:
        entry["total_budget"] += unit_budget * quantity
    entry["ot_counts"][ot_pk] += quantity


def _build_license_budget_by_year(site_ids=None, device_type_ids=None, owning_tenant_ids=None,
                                   manufacturer_ids=None, exclude_retired=True):
    today = now().date()
    current_year = today.year

    # Feature AssetLicense rows that belong to a LicenseBundle are priced via
    # that bundle instead (their own SKU typically has no price of its own) —
    # excluded here so they don't also show up unpriced under their own SKU.
    # Assets planned for decommission are excluded from budget totals entirely
    # here (see _build_license_budget_by_device for where they're still shown,
    # in a separate non-budgeted section — this report has no per-device
    # breakdown, so there's nowhere sensible to surface them without a device).
    # Licenses/bundles flagged do_not_renew are a deliberate "not renewing
    # this" decision made in NetBox — excluded from budget totals the same
    # way, with a count surfaced so it's clear why they've disappeared rather
    # than silently vanishing.
    qs = _license_qs(
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
    ).filter(bundle__isnull=True, asset__planned_decommission_date__isnull=True)

    bundle_qs = _bundle_qs(
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
    ).filter(asset__planned_decommission_date__isnull=True)

    # year -> sku_pk -> accumulation
    by_year: dict = defaultdict(dict)
    ot_names: dict = {}
    do_not_renew_count = 0

    for al in qs.iterator():
        if al.do_not_renew:
            do_not_renew_count += 1
            continue
        ot_pk = al.asset.owning_tenant_id or 0
        ot_name = al.asset.owning_tenant.name if al.asset.owning_tenant else "(No Owner)"
        _accumulate_sku_bucket(by_year, ot_names, al.end_date, al.sku, al.quantity, ot_pk, ot_name)

    for b in bundle_qs.iterator():
        if b.do_not_renew:
            do_not_renew_count += 1
            continue
        ot_pk = b.asset.owning_tenant_id or 0
        ot_name = b.asset.owning_tenant.name if b.asset.owning_tenant else "(No Owner)"
        _accumulate_sku_bucket(by_year, ot_names, b.end_date, b.sku, b.quantity, ot_pk, ot_name)

    years = []
    grand_total_budget = Decimal("0")
    grand_missing_count = 0

    for year in sorted(by_year.keys()):
        budget_year = year - 1
        year_status = _budget_year_status(budget_year, current_year)

        skus = []
        year_total_qty = 0
        year_total_budget = Decimal("0")
        year_missing_count = 0

        for sku_pk, info in sorted(
            by_year[year].items(), key=lambda x: (x[1]["manufacturer"], x[1]["sku"])
        ):
            ot_rows = [
                {"pk": ot_pk or None, "name": ot_names.get(ot_pk, "(No Owner)"), "quantity": q}
                for ot_pk, q in sorted(info["ot_counts"].items(), key=lambda x: ot_names.get(x[0], ""))
            ]
            skus.append({
                "sku_pk": info["sku_pk"],
                "sku": info["sku"],
                "sku_name": info["sku_name"],
                "manufacturer": info["manufacturer"],
                "license_kind": info["license_kind"],
                "unit_budget": info["unit_budget"],
                "total_quantity": info["total_quantity"],
                "total_budget": info["total_budget"],
                "missing_budget": info["missing_budget"],
                "owning_tenants": ot_rows,
            })
            year_total_qty += info["total_quantity"]
            if info["total_budget"] is not None:
                year_total_budget += info["total_budget"]
            if info["missing_budget"]:
                year_missing_count += 1

        grand_total_budget += year_total_budget
        grand_missing_count += year_missing_count

        years.append({
            "year": year,
            "budget_year": budget_year,
            "year_status": year_status,
            "skus": skus,
            "total_quantity": year_total_qty,
            "total_budget": year_total_budget,
            "missing_budget_count": year_missing_count,
        })

    return {
        "years": years,
        "current_year": current_year,
        "grand_total_budget": grand_total_budget,
        "grand_missing_count": grand_missing_count,
        "do_not_renew_count": do_not_renew_count,
    }


def _build_budget_year_summary(site_ids=None, device_type_ids=None, owning_tenant_ids=None,
                                manufacturer_ids=None, exclude_retired=True):
    """Budget Request Year -> owning tenant -> total renewal budget. Powers the
    summary table at the top of the report, shown above both tabs (By Year /
    By Device) rather than nested under either one. Mirrors the exclusion rules
    of _build_license_budget_by_year: decommission-planned assets and
    do_not_renew licenses/bundles are left out of every total, and SKUs with no
    renewal_budget_per_unit set contribute nothing (rather than showing as an
    unknown dollar amount)."""
    today = now().date()
    current_year = today.year

    qs = _license_qs(
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
    ).filter(bundle__isnull=True, asset__planned_decommission_date__isnull=True)

    bundle_qs = _bundle_qs(
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
    ).filter(asset__planned_decommission_date__isnull=True)

    # budget_year -> ot_pk -> Decimal total
    by_budget_year: dict = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    ot_names: dict = {}

    def _accumulate(rows):
        for row in rows:
            if row.do_not_renew:
                continue
            unit_budget = row.sku.renewal_budget_per_unit
            if unit_budget is None:
                continue
            budget_year = row.end_date.year - 1
            ot_pk = row.asset.owning_tenant_id or 0
            ot_names[ot_pk] = row.asset.owning_tenant.name if row.asset.owning_tenant else "(No Owner)"
            by_budget_year[budget_year][ot_pk] += unit_budget * row.quantity

    _accumulate(qs.iterator())
    _accumulate(bundle_qs.iterator())

    summary = []
    for budget_year in sorted(by_budget_year.keys()):
        tenants = [
            {"pk": ot_pk or None, "name": ot_names.get(ot_pk, "(No Owner)"), "total_budget": total}
            for ot_pk, total in sorted(
                by_budget_year[budget_year].items(), key=lambda x: ot_names.get(x[0], "")
            )
        ]
        summary.append({
            "budget_year": budget_year,
            "year_status": _budget_year_status(budget_year, current_year),
            "total_budget": sum((t["total_budget"] for t in tenants), Decimal("0")),
            "tenants": tenants,
        })

    return summary


def _license_budget_by_year_csv(data):
    response, writer = _csv_response("license_renewal_budget_by_year.csv")
    writer.writerow([
        "Expiry Year", "Budget Request Year", "Manufacturer", "SKU", "SKU Name", "License Kind",
        "Total Quantity", "Unit Budget", "Total Budget", "Missing Budget Data",
        "Owning Tenant", "Tenant Quantity",
    ])
    for year_data in data["years"]:
        for sku in year_data["skus"]:
            for ot in sku["owning_tenants"]:
                writer.writerow([
                    year_data["year"], year_data["budget_year"],
                    sku["manufacturer"], sku["sku"], sku["sku_name"], sku["license_kind"],
                    sku["total_quantity"],
                    sku["unit_budget"] if sku["unit_budget"] is not None else "",
                    sku["total_budget"] if sku["total_budget"] is not None else "",
                    "Yes" if sku["missing_budget"] else "No",
                    ot["name"], ot["quantity"],
                ])
    return response


def _get_or_create_device(devices, asset):
    asset_pk = asset.pk
    if asset_pk not in devices:
        site = _resolve_site(asset)
        devices[asset_pk] = {
            "pk": asset_pk,
            "name": str(asset),
            "asset_name": asset.name or str(asset),
            "serial": asset.serial or "—",
            "device_pk": asset.device_id,
            "device_name": asset.device.name if asset.device_id and asset.device else None,
            "site": site.name if site else "(No Site)",
            "site_pk": site.pk if site else None,
            "owning_tenant": asset.owning_tenant.name if asset.owning_tenant else "(No Owner)",
            "owning_tenant_pk": asset.owning_tenant_id,
            "planned_decommission_date": asset.planned_decommission_date,
            "licenses": [],
            "total_budget": Decimal("0"),
            "missing_budget_count": 0,
            "earliest_end_date": None,
        }
    return devices[asset_pk]


def _add_device_license_row(device, row):
    device["licenses"].append(row)
    if row["total_budget"] is not None:
        device["total_budget"] += row["total_budget"]
    else:
        device["missing_budget_count"] += 1
    end_date = row["end_date"]
    if device["earliest_end_date"] is None or end_date < device["earliest_end_date"]:
        device["earliest_end_date"] = end_date


def _build_license_budget_by_device(site_ids=None, device_type_ids=None, owning_tenant_ids=None,
                                     manufacturer_ids=None, exclude_retired=True):
    """
    Device-parent view: each asset lists the licenses expiring on it.

    Enterprise-wide SKUs (LicenseSKU.is_enterprise_wide) are pulled out into a
    separate top-level section instead of being nested under every asset they're
    assigned to — e.g. a shared logging/telemetry entitlement synced against
    100+ devices individually would otherwise dominate a device-parent report
    with the same line repeated under every device.

    Feature AssetLicense rows that belong to a LicenseBundle are excluded from
    their asset's list and replaced with a single row for the bundle itself,
    priced via the bundle's own SKU instead of pricing (or leaving as missing
    budget) each of its several component feature SKUs.

    Assets flagged Asset.planned_decommission_date are pulled into their own
    "planned for decommission" section instead of the main per-device list —
    still visible for reference, but excluded from every budget total so the
    report reflects only what actually needs to be budgeted for.

    Individual licenses/bundles flagged do_not_renew are dropped entirely
    (not shown under their device, not counted in any total) — a count is
    returned so the UI can note how many were excluded this way.
    """
    today = now().date()
    current_year = today.year

    qs = _license_qs(
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
    ).select_related("sku").filter(bundle__isnull=True)

    bundle_qs = _bundle_qs(
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
    ).annotate(feature_count=Count("asset_licenses"))

    # (sku_pk, end_date) -> accumulation, no per-device breakdown
    enterprise_buckets: dict = {}
    # asset_pk -> accumulation
    devices: dict = {}
    # asset_pk -> accumulation, for assets planned for decommission — kept
    # entirely separate so they never contribute to any budget total below
    excluded_devices: dict = {}
    do_not_renew_count = 0

    def _target_devices(asset):
        return excluded_devices if asset.planned_decommission_date else devices

    for al in qs.iterator():
        if al.do_not_renew:
            do_not_renew_count += 1
            continue
        year = al.end_date.year
        budget_year = year - 1
        unit_budget = al.sku.renewal_budget_per_unit
        total_budget = (unit_budget * al.quantity) if unit_budget is not None else None

        if al.sku.is_enterprise_wide and not al.asset.planned_decommission_date:
            key = (al.sku_id, al.end_date)
            if key not in enterprise_buckets:
                enterprise_buckets[key] = {
                    "sku_pk": al.sku_id,
                    "sku": al.sku.sku,
                    "sku_name": al.sku.name,
                    "manufacturer": al.sku.manufacturer.name if al.sku.manufacturer else "",
                    "end_date": al.end_date,
                    "year": year,
                    "budget_year": budget_year,
                    "year_status": _budget_year_status(budget_year, current_year),
                    "unit_budget": unit_budget,
                    "device_count": 0,
                    "total_quantity": 0,
                    "total_budget": Decimal("0") if unit_budget is not None else None,
                    "missing_budget": unit_budget is None,
                }
            eb = enterprise_buckets[key]
            eb["device_count"] += 1
            eb["total_quantity"] += al.quantity
            if unit_budget is None:
                eb["missing_budget"] = True
                eb["total_budget"] = None
            elif eb["total_budget"] is not None:
                eb["total_budget"] += total_budget
            continue

        device = _get_or_create_device(_target_devices(al.asset), al.asset)
        _add_device_license_row(device, {
            "sku_pk": al.sku_id,
            "sku": al.sku.sku,
            "sku_name": al.sku.name,
            "end_date": al.end_date,
            "year": year,
            "budget_year": budget_year,
            "year_status": _budget_year_status(budget_year, current_year),
            "quantity": al.quantity,
            "unit_budget": unit_budget,
            "total_budget": total_budget,
            "missing_budget": unit_budget is None,
            "is_bundle": False,
        })

    for b in bundle_qs.iterator():
        if b.do_not_renew:
            do_not_renew_count += 1
            continue
        year = b.end_date.year
        budget_year = year - 1
        unit_budget = b.sku.renewal_budget_per_unit
        total_budget = (unit_budget * b.quantity) if unit_budget is not None else None

        device = _get_or_create_device(_target_devices(b.asset), b.asset)
        _add_device_license_row(device, {
            "sku_pk": b.sku_id,
            "sku": b.sku.sku,
            "sku_name": b.sku.name,
            "end_date": b.end_date,
            "year": year,
            "budget_year": budget_year,
            "year_status": _budget_year_status(budget_year, current_year),
            "quantity": b.quantity,
            "unit_budget": unit_budget,
            "total_budget": total_budget,
            "missing_budget": unit_budget is None,
            "is_bundle": True,
            "bundle_pk": b.pk,
            "feature_count": b.feature_count,
        })

    enterprise_licenses = sorted(
        enterprise_buckets.values(), key=lambda e: (e["end_date"], e["manufacturer"], e["sku"])
    )

    def _sorted_device_list(d):
        device_list = []
        for entry in sorted(d.values(), key=lambda x: (x["earliest_end_date"], x["name"])):
            entry["licenses"].sort(key=lambda lic: lic["end_date"])
            device_list.append(entry)
        return device_list

    device_list = _sorted_device_list(devices)
    excluded_device_list = _sorted_device_list(excluded_devices)

    grand_total_budget = sum(
        (e["total_budget"] for e in enterprise_licenses if e["total_budget"] is not None),
        Decimal("0"),
    ) + sum(
        (d["total_budget"] for d in device_list), Decimal("0")
    )
    grand_missing_count = (
        sum(1 for e in enterprise_licenses if e["missing_budget"])
        + sum(d["missing_budget_count"] for d in device_list)
    )

    return {
        "enterprise_licenses": enterprise_licenses,
        "devices": device_list,
        "excluded_devices": excluded_device_list,
        "current_year": current_year,
        "grand_total_budget": grand_total_budget,
        "grand_missing_count": grand_missing_count,
        "do_not_renew_count": do_not_renew_count,
    }


def _license_budget_by_device_csv(data):
    response, writer = _csv_response("license_renewal_budget_by_device.csv")
    writer.writerow([
        "Section", "Device Name", "Asset ID", "Asset/Serial", "Site", "Owning Tenant",
        "SKU", "SKU Name", "End Date", "Expiry Year", "Budget Request Year",
        "Quantity", "Unit Budget", "Total Budget", "Missing Budget Data",
        "Device Count", "Is Bundle", "Bundled Feature Count", "Planned Decommission Date",
    ])
    for e in data["enterprise_licenses"]:
        writer.writerow([
            "Enterprise-Wide", "", "", "", "", "",
            e["sku"], e["sku_name"], e["end_date"], e["year"], e["budget_year"],
            e["total_quantity"],
            e["unit_budget"] if e["unit_budget"] is not None else "",
            e["total_budget"] if e["total_budget"] is not None else "",
            "Yes" if e["missing_budget"] else "No",
            e["device_count"], "", "", "",
        ])
    for d in data["devices"]:
        for lic in d["licenses"]:
            writer.writerow([
                "Device", d["device_name"] or "", d["pk"], d["name"], d["site"], d["owning_tenant"],
                lic["sku"], lic["sku_name"], lic["end_date"], lic["year"], lic["budget_year"],
                lic["quantity"],
                lic["unit_budget"] if lic["unit_budget"] is not None else "",
                lic["total_budget"] if lic["total_budget"] is not None else "",
                "Yes" if lic["missing_budget"] else "No",
                "",
                "Yes" if lic["is_bundle"] else "No",
                lic["feature_count"] if lic["is_bundle"] else "",
                "",
            ])
    for d in data["excluded_devices"]:
        for lic in d["licenses"]:
            writer.writerow([
                "Planned Decommission", d["device_name"] or "", d["pk"], d["name"], d["site"], d["owning_tenant"],
                lic["sku"], lic["sku_name"], lic["end_date"], lic["year"], lic["budget_year"],
                lic["quantity"],
                lic["unit_budget"] if lic["unit_budget"] is not None else "",
                lic["total_budget"] if lic["total_budget"] is not None else "",
                "Yes" if lic["missing_budget"] else "No",
                "",
                "Yes" if lic["is_bundle"] else "No",
                lic["feature_count"] if lic["is_bundle"] else "",
                d["planned_decommission_date"] or "",
            ])
    return response


_LICENSE_BUDGET_CONFIG = {
    "by_year":   ("By Year",   _build_license_budget_by_year,   _license_budget_by_year_csv),
    "by_device": ("By Device", _build_license_budget_by_device, _license_budget_by_device_csv),
}


class LicenseBudgetReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "netbox_inventory.view_assetlicense"
    template_name = "netbox_insights/license_budget_report.html"

    def get(self, request):
        from ..forms.reports import AssetReportFilterForm

        report_key = request.GET.get("report", "by_year")
        if report_key not in _LICENSE_BUDGET_CONFIG:
            report_key = "by_year"

        form = AssetReportFilterForm(request.GET or None)
        submitted = "submitted" in request.GET

        filters = {}
        if form.is_valid():
            if sites := form.cleaned_data.get("site"):
                filters["site_ids"] = [s.pk for s in sites]
            if manufacturers := form.cleaned_data.get("manufacturer"):
                filters["manufacturer_ids"] = [m.pk for m in manufacturers]
            if dts := form.cleaned_data.get("device_type"):
                filters["device_type_ids"] = [dt.pk for dt in dts]
            if owning_tenants := form.cleaned_data.get("owning_tenant"):
                filters["owning_tenant_ids"] = [t.pk for t in owning_tenants]
            filters["exclude_retired"] = (
                form.cleaned_data.get("exclude_retired", True) if submitted else True
            )
        else:
            filters["exclude_retired"] = True

        label, builder, csv_func = _LICENSE_BUDGET_CONFIG[report_key]
        data = builder(**filters) if submitted else {}
        budget_year_summary = _build_budget_year_summary(**filters) if submitted else []

        if submitted and request.GET.get("format") == "csv":
            return csv_func(data)

        tab_urls = {}
        for key in _LICENSE_BUDGET_CONFIG:
            params = request.GET.copy()
            params["report"] = key
            params.pop("format", None)
            tab_urls[key] = "?" + params.urlencode()

        csv_params = request.GET.copy()
        csv_params["format"] = "csv"

        return render(request, self.template_name, {
            **data,
            "budget_year_summary": budget_year_summary,
            "form": form,
            "exclude_retired": filters["exclude_retired"],
            "report_key": report_key,
            "report_label": label,
            "tab_urls": tab_urls,
            "csv_url": "?" + csv_params.urlencode(),
            "submitted": submitted,
        })

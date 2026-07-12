from collections import defaultdict
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Q
from django.shortcuts import render
from django.utils.timezone import now
from django.views import View

from .asset_reports import _apply_filters, _asset_base_qs, _resolve_site
from .license_reports import _budget_year_status
from .reports import _csv_response, _eox_status, _lifecycle_years

__all__ = ('HardwareReplacementBudgetReportView',)


def _is_active_asset(asset):
    """An asset counts as "actively deployed" (not a spare/unassigned unit sitting
    in inventory) if it's attached to a Device, or — for assets tracked directly
    against a site with no Device object — its status/allocation say it's actually
    in use rather than stored/unallocated."""
    return asset.device_id is not None or (
        asset.status == "used" and asset.allocation_status == "allocated"
    )


def _budgetable_qs(site_ids=None, device_type_ids=None, owning_tenant_ids=None,
                    manufacturer_ids=None, exclude_retired=True, include_decommission_planned=False,
                    exclude_spare_unassigned=True):
    """Assets with a known EoX date (needed to compute a budget year). By default
    also excludes assets flagged for decommission — pass include_decommission_planned=True
    (only the By Device builder needs this) to keep them in so they can be routed
    into a separate excluded section instead of being dropped outright. Same idea
    for exclude_spare_unassigned=False in the By Device builder, which needs spare/
    unassigned assets present in the queryset so it can list (not just drop) them."""
    qs = _asset_base_qs().filter(tracked_eox_date__isnull=False)
    if not include_decommission_planned:
        qs = qs.filter(planned_decommission_date__isnull=True)
    if exclude_spare_unassigned:
        qs = qs.filter(Q(device__isnull=False) | Q(status="used", allocation_status="allocated"))
    return _apply_filters(
        qs, site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
    )


def _dt_display_name(asset):
    mfr = (
        asset.device_type.manufacturer.name
        if asset.device_type and asset.device_type.manufacturer else ""
    )
    model = asset.device_type.model if asset.device_type else ""
    return (f"{mfr} {model}".strip() if mfr else model), mfr


# ══════════════════════════════════════════════════════════════════════════════
# Summary tab
# ══════════════════════════════════════════════════════════════════════════════

def _build_hardware_budget_year_summary(site_ids=None, device_type_ids=None, owning_tenant_ids=None,
                                         manufacturer_ids=None, exclude_retired=True,
                                         exclude_spare_unassigned=True):
    """Budget Request Year -> owning tenant -> total estimated replacement cost.
    Mirrors _build_budget_year_summary in license_reports.py, but the budget year
    comes from the month-threshold HardwareLifecycle calculation (_lifecycle_years),
    not a naive end_date.year - 1. Decommission-planned assets are excluded here —
    see _build_hardware_budget_by_device for where they're still shown."""
    today = now().date()
    current_year = today.year

    qs = _budgetable_qs(
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired, exclude_spare_unassigned=exclude_spare_unassigned,
    )

    # budget_year -> ot_pk -> Decimal total
    by_budget_year: dict = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    ot_names: dict = {}

    for asset in qs.iterator():
        unit_cost = asset.tracked_replacement_cost
        if unit_cost is None:
            continue
        _, budget_year = _lifecycle_years(asset.tracked_eox_date)
        ot_pk = asset.owning_tenant_id or 0
        ot_names[ot_pk] = asset.owning_tenant.name if asset.owning_tenant else "(No Owner)"
        by_budget_year[budget_year][ot_pk] += unit_cost

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


def _build_hardware_budget_year_by_site_summary(site_ids=None, device_type_ids=None, owning_tenant_ids=None,
                                                 manufacturer_ids=None, exclude_retired=True,
                                                 exclude_spare_unassigned=True):
    """Budget Request Year -> site -> total estimated replacement cost. Same shape
    as _build_hardware_budget_year_summary, grouped by _resolve_site(asset) instead
    of owning tenant — the site-level counterpart shown alongside it on the
    Summary tab."""
    today = now().date()
    current_year = today.year

    qs = _budgetable_qs(
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired, exclude_spare_unassigned=exclude_spare_unassigned,
    )

    # budget_year -> site_pk -> Decimal total
    by_budget_year: dict = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    site_names: dict = {}

    for asset in qs.iterator():
        unit_cost = asset.tracked_replacement_cost
        if unit_cost is None:
            continue
        _, budget_year = _lifecycle_years(asset.tracked_eox_date)
        site = _resolve_site(asset)
        site_pk = site.pk if site else 0
        site_names[site_pk] = site.name if site else "(No Site)"
        by_budget_year[budget_year][site_pk] += unit_cost

    summary = []
    for budget_year in sorted(by_budget_year.keys()):
        sites = [
            {"pk": site_pk or None, "name": site_names.get(site_pk, "(No Site)"), "total_budget": total}
            for site_pk, total in sorted(
                by_budget_year[budget_year].items(), key=lambda x: site_names.get(x[0], "")
            )
        ]
        summary.append({
            "budget_year": budget_year,
            "year_status": _budget_year_status(budget_year, current_year),
            "total_budget": sum((s["total_budget"] for s in sites), Decimal("0")),
            "sites": sites,
        })

    return summary


def _build_hardware_budget_summary(site_ids=None, device_type_ids=None, owning_tenant_ids=None,
                                    manufacturer_ids=None, exclude_retired=True,
                                    exclude_spare_unassigned=True):
    """Combined data for the Summary tab: budget year broken down by tenant, and
    separately by site. Kept as two parallel tables rather than merging into one
    year x site x tenant table — that got unreadably wide, which is why this
    moved out of the always-visible top-of-page card into its own tab."""
    kwargs = dict(
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired, exclude_spare_unassigned=exclude_spare_unassigned,
    )
    return {
        "tenant_summary": _build_hardware_budget_year_summary(**kwargs),
        "site_summary": _build_hardware_budget_year_by_site_summary(**kwargs),
    }


def _hardware_budget_summary_csv(data):
    response, writer = _csv_response("hardware_replacement_budget_summary.csv")
    writer.writerow(["Grouping", "Budget Request Year", "Group", "Total Replacement Cost"])
    for by in data["tenant_summary"]:
        for t in by["tenants"]:
            writer.writerow(["Tenant", by["budget_year"], t["name"], t["total_budget"]])
    for by in data["site_summary"]:
        for s in by["sites"]:
            writer.writerow(["Site", by["budget_year"], s["name"], s["total_budget"]])
    return response


# ══════════════════════════════════════════════════════════════════════════════
# By Year
# ══════════════════════════════════════════════════════════════════════════════

def _build_hardware_budget_by_year(site_ids=None, device_type_ids=None, owning_tenant_ids=None,
                                    manufacturer_ids=None, exclude_retired=True,
                                    exclude_spare_unassigned=True):
    today = now().date()
    current_year = today.year

    qs = _budgetable_qs(
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired, exclude_spare_unassigned=exclude_spare_unassigned,
    )

    # budget_year -> dt_pk -> accumulation
    by_year: dict = defaultdict(dict)
    ot_names: dict = {}

    for asset in qs.iterator():
        eox_date = asset.tracked_eox_date
        replacement_year, budget_year = _lifecycle_years(eox_date)
        dt_pk = asset.device_type_id
        unit_cost = asset.tracked_replacement_cost
        ot_pk = asset.owning_tenant_id or 0
        ot_names[ot_pk] = asset.owning_tenant.name if asset.owning_tenant else "(No Owner)"

        bucket = by_year[budget_year]
        if dt_pk not in bucket:
            name, mfr = _dt_display_name(asset)
            bucket[dt_pk] = {
                "dt_pk": dt_pk,
                "name": name,
                "manufacturer": mfr,
                "eox_date": eox_date,
                "eox_status": _eox_status(eox_date, today),
                "replacement_year": replacement_year,
                "unit_cost": unit_cost,
                "total_count": 0,
                "total_cost": Decimal("0") if unit_cost is not None else None,
                "missing_cost": unit_cost is None,
                "ot_counts": defaultdict(int),
            }
        entry = bucket[dt_pk]
        entry["total_count"] += 1
        if unit_cost is None:
            entry["missing_cost"] = True
            entry["total_cost"] = None
        elif entry["total_cost"] is not None:
            entry["total_cost"] += unit_cost
        entry["ot_counts"][ot_pk] += 1

    years = []
    grand_missing_count = 0

    for budget_year in sorted(by_year.keys()):
        year_status = _budget_year_status(budget_year, current_year)

        device_types = []
        year_total_count = 0
        year_total_cost = Decimal("0")
        year_missing_count = 0

        for dt_pk, info in sorted(
            by_year[budget_year].items(), key=lambda x: (x[1]["manufacturer"], x[1]["name"])
        ):
            ot_rows = [
                {"pk": ot_pk or None, "name": ot_names.get(ot_pk, "(No Owner)"), "count": count}
                for ot_pk, count in sorted(info["ot_counts"].items(), key=lambda x: ot_names.get(x[0], ""))
            ]
            device_types.append({
                "dt_pk": info["dt_pk"],
                "name": info["name"],
                "manufacturer": info["manufacturer"],
                "eox_date": info["eox_date"],
                "eox_status": info["eox_status"],
                "replacement_year": info["replacement_year"],
                "unit_cost": info["unit_cost"],
                "total_count": info["total_count"],
                "total_cost": info["total_cost"],
                "missing_cost": info["missing_cost"],
                "owning_tenants": ot_rows,
            })
            year_total_count += info["total_count"]
            if info["total_cost"] is not None:
                year_total_cost += info["total_cost"]
            if info["missing_cost"]:
                year_missing_count += 1

        grand_missing_count += year_missing_count

        years.append({
            "budget_year": budget_year,
            "year_status": year_status,
            "device_types": device_types,
            "total_count": year_total_count,
            "total_cost": year_total_cost,
            "missing_cost_count": year_missing_count,
        })

    return {
        "years": years,
        "current_year": current_year,
        "grand_missing_count": grand_missing_count,
    }


def _hardware_budget_by_year_csv(data):
    response, writer = _csv_response("hardware_replacement_budget_by_year.csv")
    writer.writerow([
        "Budget Request Year", "Manufacturer", "Device Type", "EoX Date", "Replacement Year",
        "Total Quantity", "Unit Replacement Cost", "Total Replacement Cost", "Missing Cost Data",
        "Owning Tenant", "Tenant Quantity",
    ])
    for year_data in data["years"]:
        for dt in year_data["device_types"]:
            for ot in dt["owning_tenants"]:
                writer.writerow([
                    year_data["budget_year"], dt["manufacturer"], dt["name"],
                    dt["eox_date"] or "-", dt["replacement_year"] or "-",
                    dt["total_count"],
                    dt["unit_cost"] if dt["unit_cost"] is not None else "",
                    dt["total_cost"] if dt["total_cost"] is not None else "",
                    "Yes" if dt["missing_cost"] else "No",
                    ot["name"], ot["count"],
                ])
    return response


# ══════════════════════════════════════════════════════════════════════════════
# By Site
# ══════════════════════════════════════════════════════════════════════════════

def _build_hardware_budget_by_site(site_ids=None, device_type_ids=None, owning_tenant_ids=None,
                                    manufacturer_ids=None, exclude_retired=True,
                                    exclude_spare_unassigned=True):
    """Site -> Budget Request Year -> Owning Tenant -> Device Type, so budget can be
    booked per site instead of only globally by year. Same grain/exclusions as
    _build_hardware_budget_by_year (decommission-planned assets excluded — see
    _build_hardware_budget_by_device for where those are still shown)."""
    today = now().date()
    current_year = today.year

    qs = _budgetable_qs(
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired, exclude_spare_unassigned=exclude_spare_unassigned,
    )

    # site_pk -> budget_year -> ot_pk -> dt_pk -> accumulation
    counts: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    site_names: dict = {}
    ot_names: dict = {}

    for asset in qs.iterator():
        site = _resolve_site(asset)
        site_pk = site.pk if site else 0
        site_names[site_pk] = site.name if site else "(No Site)"

        eox_date = asset.tracked_eox_date
        replacement_year, budget_year = _lifecycle_years(eox_date)

        ot_pk = asset.owning_tenant_id or 0
        ot_names[(site_pk, ot_pk)] = asset.owning_tenant.name if asset.owning_tenant else "(No Owner)"

        dt_pk = asset.device_type_id
        unit_cost = asset.tracked_replacement_cost

        bucket = counts[site_pk][budget_year][ot_pk]
        if dt_pk not in bucket:
            name, mfr = _dt_display_name(asset)
            bucket[dt_pk] = {
                "dt_pk": dt_pk,
                "name": name,
                "manufacturer": mfr,
                "eox_date": eox_date,
                "eox_status": _eox_status(eox_date, today),
                "replacement_year": replacement_year,
                "unit_cost": unit_cost,
                "count": 0,
                "total_cost": Decimal("0") if unit_cost is not None else None,
                "missing_cost": unit_cost is None,
            }
        entry = bucket[dt_pk]
        entry["count"] += 1
        if unit_cost is None:
            entry["missing_cost"] = True
            entry["total_cost"] = None
        elif entry["total_cost"] is not None:
            entry["total_cost"] += unit_cost

    sites = []
    grand_missing_count = 0

    for site_pk, years_data in sorted(counts.items(), key=lambda x: site_names.get(x[0], "")):
        budget_years = []
        site_total_cost = Decimal("0")
        site_total_count = 0

        for budget_year, ot_data in sorted(years_data.items()):
            owning_tenants = []
            year_total_cost = Decimal("0")
            year_total_count = 0
            year_missing_count = 0

            for ot_pk, dt_bucket in sorted(ot_data.items(), key=lambda x: ot_names.get((site_pk, x[0]), "")):
                dt_rows = []
                for dt_pk, info in sorted(
                    dt_bucket.items(), key=lambda x: (x[1]["manufacturer"], x[1]["name"])
                ):
                    dt_rows.append(info)
                    year_total_count += info["count"]
                    if info["total_cost"] is not None:
                        year_total_cost += info["total_cost"]
                    if info["missing_cost"]:
                        year_missing_count += 1

                ot_total_count = sum(dt["count"] for dt in dt_rows)
                ot_total_cost = sum((dt["total_cost"] for dt in dt_rows if dt["total_cost"] is not None), Decimal("0"))
                owning_tenants.append({
                    "pk": ot_pk or None,
                    "name": ot_names.get((site_pk, ot_pk), "(No Owner)"),
                    "device_types": dt_rows,
                    "total_count": ot_total_count,
                    "total_cost": ot_total_cost,
                })

            grand_missing_count += year_missing_count
            site_total_cost += year_total_cost
            site_total_count += year_total_count

            budget_years.append({
                "budget_year": budget_year,
                "year_status": _budget_year_status(budget_year, current_year),
                "owning_tenants": owning_tenants,
                "total_count": year_total_count,
                "total_cost": year_total_cost,
                "missing_cost_count": year_missing_count,
            })

        sites.append({
            "pk": site_pk or None,
            "name": site_names.get(site_pk, "(No Site)"),
            "budget_years": budget_years,
            "total_count": site_total_count,
            "total_cost": site_total_cost,
        })

    return {
        "sites": sites,
        "current_year": current_year,
        "grand_missing_count": grand_missing_count,
    }


def _hardware_budget_by_site_csv(data):
    response, writer = _csv_response("hardware_replacement_budget_by_site.csv")
    writer.writerow([
        "Site", "Budget Request Year", "Owning Tenant", "Manufacturer", "Device Type",
        "EoX Date", "Replacement Year", "Quantity",
        "Unit Replacement Cost", "Total Replacement Cost", "Missing Cost Data",
    ])
    for site in data["sites"]:
        for by in site["budget_years"]:
            for ot in by["owning_tenants"]:
                for dt in ot["device_types"]:
                    writer.writerow([
                        site["name"], by["budget_year"], ot["name"],
                        dt["manufacturer"], dt["name"],
                        dt["eox_date"] or "-", dt["replacement_year"] or "-", dt["count"],
                        dt["unit_cost"] if dt["unit_cost"] is not None else "",
                        dt["total_cost"] if dt["total_cost"] is not None else "",
                        "Yes" if dt["missing_cost"] else "No",
                    ])
    return response


# ══════════════════════════════════════════════════════════════════════════════
# By Device
# ══════════════════════════════════════════════════════════════════════════════

def _build_hardware_budget_by_device(site_ids=None, device_type_ids=None, owning_tenant_ids=None,
                                      manufacturer_ids=None, exclude_retired=True,
                                      exclude_spare_unassigned=True):
    """
    Site -> Owning Tenant -> asset rows, one row per asset (each physical unit
    needs exactly one replacement, unlike licenses which can stack several lines
    per device) — mirrors the site/tenant grouping shape of _build_asset_uncovered.

    Assets flagged Asset.planned_decommission_date are routed into a separate
    excluded_sites section instead of the main sites list — still visible for
    reference (cost shown), but excluded from every total on this page, mirroring
    _build_license_budget_by_device's excluded_devices handling.

    Assets that fail the "actively deployed" check (see _is_active_asset — no
    device attached and not status=used/allocation=allocated) are routed the same
    way into a spare_sites section when exclude_spare_unassigned=True. Decommission
    takes priority: an asset flagged for both is shown under decommission, not
    spare, since that's the more specific/actionable reason. When the toggle is
    off, spare/unassigned assets are just included in the main totals like any
    other asset — nothing gets built into spare_sites.
    """
    today = now().date()
    current_year = today.year

    qs = _budgetable_qs(
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
        include_decommission_planned=True,
        exclude_spare_unassigned=False,
    ).order_by("device__site__name", "installed_site_override__name",
               "owning_tenant__name", "tracked_eox_date")

    # site_pk -> ot_pk -> list of asset rows
    rows: dict = defaultdict(lambda: defaultdict(list))
    excluded_rows: dict = defaultdict(lambda: defaultdict(list))
    spare_rows: dict = defaultdict(lambda: defaultdict(list))
    site_names: dict = {}
    ot_names: dict = {}

    for asset in qs.iterator():
        site = _resolve_site(asset)
        site_pk = site.pk if site else 0
        site_names[site_pk] = site.name if site else "(No Site)"

        ot_pk = asset.owning_tenant_id or 0
        ot_names[(site_pk, ot_pk)] = asset.owning_tenant.name if asset.owning_tenant else "(No Owner)"

        name, mfr = _dt_display_name(asset)
        eox_date = asset.tracked_eox_date
        replacement_year, budget_year = _lifecycle_years(eox_date)
        unit_cost = asset.tracked_replacement_cost

        row = {
            "pk": asset.pk,
            "asset_name": asset.name or str(asset),
            "serial": asset.serial or "—",
            "device_pk": asset.device_id,
            "device_name": asset.device.name if asset.device_id and asset.device else None,
            "device_type_pk": asset.device_type_id,
            "device_type": name,
            "manufacturer": mfr,
            "eox_date": eox_date,
            "eox_status": _eox_status(eox_date, today),
            "replacement_year": replacement_year,
            "budget_year": budget_year,
            "year_status": _budget_year_status(budget_year, current_year),
            "unit_cost": unit_cost,
            "missing_cost": unit_cost is None,
            "planned_decommission_date": asset.planned_decommission_date,
            "status": asset.get_status_display(),
            "allocation_status": asset.get_allocation_status_display() if asset.allocation_status else "—",
        }

        if asset.planned_decommission_date:
            target = excluded_rows
        elif exclude_spare_unassigned and not _is_active_asset(asset):
            target = spare_rows
        else:
            target = rows
        target[site_pk][ot_pk].append(row)

    def _build_site_list(grouped):
        sites = []
        for site_pk in sorted(grouped.keys(), key=lambda x: site_names.get(x, "")):
            ot_list = []
            for ot_pk in sorted(grouped[site_pk].keys(), key=lambda x: ot_names.get((site_pk, x), "")):
                assets = sorted(grouped[site_pk][ot_pk], key=lambda a: a["eox_date"])
                total_cost = sum((a["unit_cost"] for a in assets if a["unit_cost"] is not None), Decimal("0"))
                missing_cost_count = sum(1 for a in assets if a["missing_cost"])
                ot_list.append({
                    "pk": ot_pk or None,
                    "name": ot_names.get((site_pk, ot_pk), "(No Owner)"),
                    "assets": assets,
                    "total": len(assets),
                    "total_cost": total_cost,
                    "missing_cost_count": missing_cost_count,
                })
            sites.append({
                "pk": site_pk or None,
                "name": site_names.get(site_pk, "(No Site)"),
                "owning_tenants": ot_list,
                "total": sum(ot["total"] for ot in ot_list),
                "total_cost": sum((ot["total_cost"] for ot in ot_list), Decimal("0")),
            })
        return sites

    site_list = _build_site_list(rows)
    excluded_site_list = _build_site_list(excluded_rows)
    spare_site_list = _build_site_list(spare_rows)

    grand_total_cost = sum((s["total_cost"] for s in site_list), Decimal("0"))
    grand_missing_count = sum(
        ot["missing_cost_count"] for s in site_list for ot in s["owning_tenants"]
    )

    return {
        "sites": site_list,
        "excluded_sites": excluded_site_list,
        "spare_sites": spare_site_list,
        "current_year": current_year,
        "grand_total_cost": grand_total_cost,
        "grand_missing_count": grand_missing_count,
    }


def _hardware_budget_by_device_csv(data):
    response, writer = _csv_response("hardware_replacement_budget_by_device.csv")
    writer.writerow([
        "Section", "Device Name", "Asset ID", "Asset/Serial", "Site", "Owning Tenant",
        "Device Type", "EoX Date", "Replacement Year", "Budget Year",
        "Unit Replacement Cost", "Missing Cost Data", "Planned Decommission Date",
        "Status", "Allocation Status",
    ])
    for s in data["sites"]:
        for ot in s["owning_tenants"]:
            for a in ot["assets"]:
                writer.writerow([
                    "Device", a["device_name"] or "", a["pk"], a["asset_name"],
                    s["name"], ot["name"], a["device_type"],
                    a["eox_date"] or "-", a["replacement_year"] or "-", a["budget_year"] or "-",
                    a["unit_cost"] if a["unit_cost"] is not None else "",
                    "Yes" if a["missing_cost"] else "No", "",
                    a["status"], a["allocation_status"],
                ])
    for s in data["excluded_sites"]:
        for ot in s["owning_tenants"]:
            for a in ot["assets"]:
                writer.writerow([
                    "Planned Decommission", a["device_name"] or "", a["pk"], a["asset_name"],
                    s["name"], ot["name"], a["device_type"],
                    a["eox_date"] or "-", a["replacement_year"] or "-", a["budget_year"] or "-",
                    a["unit_cost"] if a["unit_cost"] is not None else "",
                    "Yes" if a["missing_cost"] else "No", a["planned_decommission_date"] or "",
                    a["status"], a["allocation_status"],
                ])
    for s in data["spare_sites"]:
        for ot in s["owning_tenants"]:
            for a in ot["assets"]:
                writer.writerow([
                    "Spare / Unassigned", a["device_name"] or "", a["pk"], a["asset_name"],
                    s["name"], ot["name"], a["device_type"],
                    a["eox_date"] or "-", a["replacement_year"] or "-", a["budget_year"] or "-",
                    a["unit_cost"] if a["unit_cost"] is not None else "",
                    "Yes" if a["missing_cost"] else "No", "",
                    a["status"], a["allocation_status"],
                ])
    return response


_HARDWARE_BUDGET_CONFIG = {
    "summary":   ("Summary",   _build_hardware_budget_summary,   _hardware_budget_summary_csv),
    "by_year":   ("By Year",   _build_hardware_budget_by_year,   _hardware_budget_by_year_csv),
    "by_site":   ("By Site",   _build_hardware_budget_by_site,   _hardware_budget_by_site_csv),
    "by_device": ("By Device", _build_hardware_budget_by_device, _hardware_budget_by_device_csv),
}


class HardwareReplacementBudgetReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "netbox_inventory.view_asset"
    template_name = "netbox_insights/hardware_budget_report.html"

    def get(self, request):
        from ..forms.reports import HardwareBudgetFilterForm

        report_key = request.GET.get("report", "by_year")
        if report_key not in _HARDWARE_BUDGET_CONFIG:
            report_key = "by_year"

        form = HardwareBudgetFilterForm(request.GET or None)
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
            filters["exclude_spare_unassigned"] = (
                form.cleaned_data.get("exclude_spare_unassigned", True) if submitted else True
            )
        else:
            filters["exclude_retired"] = True
            filters["exclude_spare_unassigned"] = True

        label, builder, csv_func = _HARDWARE_BUDGET_CONFIG[report_key]
        data = builder(**filters) if submitted else {}

        if submitted and request.GET.get("format") == "csv":
            return csv_func(data)

        tab_urls = {}
        for key in _HARDWARE_BUDGET_CONFIG:
            params = request.GET.copy()
            params["report"] = key
            params.pop("format", None)
            tab_urls[key] = "?" + params.urlencode()

        csv_params = request.GET.copy()
        csv_params["format"] = "csv"

        return render(request, self.template_name, {
            **data,
            "form": form,
            "exclude_retired": filters["exclude_retired"],
            "exclude_spare_unassigned": filters["exclude_spare_unassigned"],
            "report_key": report_key,
            "report_label": label,
            "tab_urls": tab_urls,
            "csv_url": "?" + csv_params.urlencode(),
            "submitted": submitted,
        })

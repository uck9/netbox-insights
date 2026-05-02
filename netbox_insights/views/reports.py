import csv
from collections import defaultdict

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Count, Exists, OuterRef, Subquery, Q
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.timezone import now
from django.views import View

from dcim.models import Device
from ..querysets import device_insights_queryset

try:
    from netbox_inventory.models.hardware import MIGRATION_CALC_MONTH
except ImportError:
    MIGRATION_CALC_MONTH = 6


def _lifecycle_years(eox_date):
    """Return (replacement_year, budget_year) from an EoX date, mirroring HardwareLifecycle properties."""
    if not eox_date:
        return None, None
    if eox_date.month <= MIGRATION_CALC_MONTH:
        return eox_date.year - 1, eox_date.year - 2
    return eox_date.year, eox_date.year - 1


__all__ = (
    'EoXReportView',
    'EoXSummaryReportView',
    'EoXByDeviceTypeReportView',
    'EoXByTenantReportView',
    'EoXByYearReportView',
    'ContractCoverageReportView',
)


def _build_eox_report(site_ids=None, device_type_ids=None, tenant_ids=None, manufacturer_ids=None, active_only=True):
    today = now().date()
    current_year = today.year

    qs = (
        device_insights_queryset()
        .filter(tracked_eox_date__isnull=False)
        .prefetch_related(None)  # assigned_asset prefetch not needed here
        .select_related("site", "tenant", "device_type__manufacturer")
        .order_by("site__name", "tenant__name", "device_type__manufacturer__name", "device_type__model")
    )
    if active_only:
        qs = qs.filter(status="active")
    if site_ids:
        qs = qs.filter(site_id__in=site_ids)
    if manufacturer_ids:
        qs = qs.filter(device_type__manufacturer_id__in=manufacturer_ids)
    if device_type_ids:
        qs = qs.filter(device_type_id__in=device_type_ids)
    if tenant_ids:
        qs = qs.filter(tenant_id__in=tenant_ids)

    # Denominator for EoX %: total active devices per site (one flat query, independent of filters).
    total_active_by_site = dict(
        Device.objects.filter(status="active")
        .values("site_id")
        .annotate(n=Count("id"))
        .values_list("site_id", "n")
    )

    # site_pk → tenant_pk → dt_pk → year → device count
    counts: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int))))
    site_names: dict = {}
    tenant_names: dict = {}
    dt_names: dict = {}
    dt_eox_dates: dict = {}
    all_years: set = set()
    # Numerator for EoX %: active devices per site whose EoX date has already passed.
    # Accumulated inline — no extra query needed.
    past_eox_active_by_site: dict = defaultdict(int)

    for device in qs.iterator():
        site_pk = device.site_id or 0
        site_names[site_pk] = device.site.name if device.site else "(No Site)"

        tenant_pk = device.tenant_id or 0
        tenant_names[(site_pk, tenant_pk)] = device.tenant.name if device.tenant else "(No Tenant)"

        dt_pk = device.device_type_id
        mfr = (
            device.device_type.manufacturer.name
            if device.device_type and device.device_type.manufacturer
            else ""
        )
        model = device.device_type.model if device.device_type else ""
        dt_names[dt_pk] = f"{mfr} {model}".strip() if mfr else model
        if dt_pk not in dt_eox_dates:
            dt_eox_dates[dt_pk] = device.tracked_eox_date

        year = device.tracked_eox_date.year
        counts[site_pk][tenant_pk][dt_pk][year] += 1
        all_years.add(year)

        if device.status == "active" and device.tracked_eox_date < today:
            past_eox_active_by_site[site_pk] += 1

    all_years_sorted = sorted(all_years)

    sites = []
    for site_pk, tenants_data in sorted(counts.items(), key=lambda x: site_names.get(x[0], "")):
        tenant_list = []
        for tenant_pk, dts_data in sorted(
            tenants_data.items(), key=lambda x: tenant_names.get((site_pk, x[0]), "")
        ):
            dt_list = []
            for dt_pk, year_data in sorted(
                dts_data.items(), key=lambda x: (min(x[1].keys()), dt_names.get(x[0], ""))
            ):
                replacement_year, budget_year = _lifecycle_years(dt_eox_dates.get(dt_pk))
                dt_list.append({
                    "pk": dt_pk,
                    "name": dt_names.get(dt_pk, ""),
                    # List of (year, count) tuples in sorted year order — avoids dict lookups in template
                    "year_counts": [(y, year_data.get(y, 0)) for y in all_years_sorted],
                    "total": sum(year_data.values()),
                    "replacement_year": replacement_year,
                    "budget_year": budget_year,
                })
            tenant_list.append({
                "pk": tenant_pk or None,
                "name": tenant_names.get((site_pk, tenant_pk), "(No Tenant)"),
                "device_types": dt_list,
                "total": sum(sum(yd.values()) for yd in dts_data.values()),
            })

        past_eox = past_eox_active_by_site.get(site_pk, 0)
        total_active = total_active_by_site.get(site_pk or None, 0)
        if total_active:
            eox_pct = round(past_eox / total_active * 100, 1)
            eox_pct_status = "success" if eox_pct == 0 else ("danger" if eox_pct >= 25 else "warning")
        else:
            eox_pct = None
            eox_pct_status = None

        sites.append({
            "pk": site_pk or None,
            "name": site_names.get(site_pk, "(No Site)"),
            "tenants": tenant_list,
            "total": sum(t["total"] for t in tenant_list),
            "eox_pct": eox_pct,
            "eox_pct_status": eox_pct_status,
            "past_eox_count": past_eox,
            "total_active": total_active,
        })

    return {
        "sites": sites,
        "all_years": all_years_sorted,
        "current_year": current_year,
    }


def _build_eox_by_device_type_report(site_ids=None, device_type_ids=None, tenant_ids=None, manufacturer_ids=None, active_only=True):
    today = now().date()

    qs = (
        device_insights_queryset()
        .filter(tracked_eox_date__isnull=False)
        .prefetch_related(None)
        .select_related("site", "tenant", "device_type__manufacturer")
        .order_by("device_type__manufacturer__name", "device_type__model", "site__name", "tenant__name")
    )
    if active_only:
        qs = qs.filter(status="active")
    if site_ids:
        qs = qs.filter(site_id__in=site_ids)
    if manufacturer_ids:
        qs = qs.filter(device_type__manufacturer_id__in=manufacturer_ids)
    if device_type_ids:
        qs = qs.filter(device_type_id__in=device_type_ids)
    if tenant_ids:
        qs = qs.filter(tenant_id__in=tenant_ids)

    # dt_pk → site_pk → tenant_pk → count
    counts: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    dt_info: dict = {}   # pk → {name, manufacturer, eox_date}
    site_names: dict = {}
    tenant_names: dict = {}

    for device in qs.iterator():
        dt_pk = device.device_type_id
        site_pk = device.site_id or 0
        tenant_pk = device.tenant_id or 0

        if dt_pk not in dt_info:
            mfr = (
                device.device_type.manufacturer.name
                if device.device_type and device.device_type.manufacturer
                else ""
            )
            model = device.device_type.model if device.device_type else ""
            eox_date = device.tracked_eox_date
            replacement_year, budget_year = _lifecycle_years(eox_date)
            dt_info[dt_pk] = {
                "pk": dt_pk,
                "name": f"{mfr} {model}".strip() if mfr else model,
                "manufacturer": mfr,
                "eox_date": eox_date,
                "replacement_year": replacement_year,
                "budget_year": budget_year,
            }

        site_names[site_pk] = device.site.name if device.site else "(No Site)"
        tenant_names[(site_pk, tenant_pk)] = device.tenant.name if device.tenant else "(No Tenant)"
        counts[dt_pk][site_pk][tenant_pk] += 1

    device_types = []
    for dt_pk, sites_data in sorted(
        counts.items(),
        key=lambda x: (dt_info[x[0]]["eox_date"], dt_info[x[0]]["name"]),
    ):
        info = dt_info[dt_pk]
        eox_date = info["eox_date"]
        if eox_date < today:
            status = "danger"
        elif eox_date.year == today.year:
            status = "warning"
        else:
            status = "success"

        site_list = []
        for site_pk, tenants_data in sorted(sites_data.items(), key=lambda x: site_names.get(x[0], "")):
            tenant_list = []
            for tenant_pk, count in sorted(
                tenants_data.items(), key=lambda x: tenant_names.get((site_pk, x[0]), "")
            ):
                tenant_list.append({
                    "pk": tenant_pk or None,
                    "name": tenant_names.get((site_pk, tenant_pk), "(No Tenant)"),
                    "count": count,
                })
            site_list.append({
                "pk": site_pk or None,
                "name": site_names.get(site_pk, "(No Site)"),
                "tenants": tenant_list,
                "total": sum(t["count"] for t in tenant_list),
            })

        total = sum(s["total"] for s in site_list)
        device_types.append({
            **info,
            "eox_status": status,
            "sites": site_list,
            "total": total,
        })

    return {
        "device_types": device_types,
        "today": today,
    }


def _csv_response(filename):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response, csv.writer(response)


def _eox_summary_csv(data):
    response, writer = _csv_response("eox_summary_report.csv")
    writer.writerow(["Site", "Tenant", "Device Type", "Replacement Year", "Budget Year"] + [str(y) for y in data["all_years"]] + ["Total"])
    for site in data["sites"]:
        for tenant in site["tenants"]:
            for dt in tenant["device_types"]:
                writer.writerow(
                    [site["name"], tenant["name"], dt["name"],
                     dt.get("replacement_year") or "-", dt.get("budget_year") or "-"]
                    + [count for _, count in dt["year_counts"]]
                    + [dt["total"]]
                )
    return response


def _eox_by_device_type_csv(data):
    response, writer = _csv_response("eox_by_device_type_report.csv")
    writer.writerow(["Device Type", "Manufacturer", "EoX Date", "EoX Status", "Replacement Year", "Budget Year", "Site", "Tenant", "Count"])
    for dt in data["device_types"]:
        for site in dt["sites"]:
            for tenant in site["tenants"]:
                writer.writerow([
                    dt["name"], dt["manufacturer"], dt["eox_date"], dt["eox_status"],
                    dt.get("replacement_year") or "-", dt.get("budget_year") or "-",
                    site["name"], tenant["name"], tenant["count"],
                ])
    return response


def _eox_by_tenant_csv(data):
    response, writer = _csv_response("eox_by_tenant_report.csv")
    writer.writerow(["Tenant", "Year", "Device Type", "EoX Date", "EoX Status", "Replacement Year", "Budget Year", "Count"])
    for tenant in data["tenants"]:
        for year_group in tenant["year_groups"]:
            for dt in year_group["device_types"]:
                writer.writerow([
                    tenant["name"], year_group["year"],
                    dt["name"], dt["eox_date"], dt["eox_status"],
                    dt.get("replacement_year") or "-", dt.get("budget_year") or "-",
                    dt["count"],
                ])
    return response


def _eox_by_year_csv(data):
    response, writer = _csv_response("eox_by_year_report.csv")
    writer.writerow(["Year", "Device Type", "EoX Date", "EoX Status", "Replacement Year", "Budget Year", "Tenant", "Count"])
    for year in data["years"]:
        for dt in year["device_types"]:
            for tenant in dt["tenants"]:
                writer.writerow([
                    year["year"], dt["name"], dt["eox_date"], dt["eox_status"],
                    dt.get("replacement_year") or "-", dt.get("budget_year") or "-",
                    tenant["name"], tenant["count"],
                ])
    return response


class EoXSummaryReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "dcim.view_device"
    template_name = "netbox_insights/eox_summary_report.html"

    def get(self, request):
        data = _build_eox_report()
        if request.GET.get("format") == "csv":
            return _eox_summary_csv(data)
        return render(request, self.template_name, data)


class EoXByDeviceTypeReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "dcim.view_device"
    template_name = "netbox_insights/eox_by_device_type_report.html"

    def get(self, request):
        data = _build_eox_by_device_type_report()
        if request.GET.get("format") == "csv":
            return _eox_by_device_type_csv(data)
        return render(request, self.template_name, data)


def _eox_status(eox_date, today):
    if eox_date < today:
        return "danger"
    if eox_date.year == today.year:
        return "warning"
    return "success"


def _build_eox_by_tenant_report(site_ids=None, device_type_ids=None, tenant_ids=None, manufacturer_ids=None, active_only=True):
    today = now().date()

    qs = (
        device_insights_queryset()
        .filter(tracked_eox_date__isnull=False)
        .prefetch_related(None)
        .select_related("site", "tenant", "device_type__manufacturer")
        .order_by("tenant__name", "tracked_eox_date", "device_type__manufacturer__name", "device_type__model")
    )
    if active_only:
        qs = qs.filter(status="active")
    if site_ids:
        qs = qs.filter(site_id__in=site_ids)
    if manufacturer_ids:
        qs = qs.filter(device_type__manufacturer_id__in=manufacturer_ids)
    if device_type_ids:
        qs = qs.filter(device_type_id__in=device_type_ids)
    if tenant_ids:
        qs = qs.filter(tenant_id__in=tenant_ids)

    # tenant_pk → year → dt_pk → count
    counts: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    tenant_info: dict = {}
    dt_info: dict = {}

    for device in qs.iterator():
        tenant_pk = device.tenant_id or 0
        dt_pk = device.device_type_id
        year = device.tracked_eox_date.year

        if tenant_pk not in tenant_info:
            tenant_info[tenant_pk] = {
                "pk": tenant_pk or None,
                "name": device.tenant.name if device.tenant else "(No Tenant)",
            }

        if dt_pk not in dt_info:
            mfr = (
                device.device_type.manufacturer.name
                if device.device_type and device.device_type.manufacturer
                else ""
            )
            model = device.device_type.model if device.device_type else ""
            eox_date = device.tracked_eox_date
            replacement_year, budget_year = _lifecycle_years(eox_date)
            dt_info[dt_pk] = {
                "pk": dt_pk,
                "name": f"{mfr} {model}".strip() if mfr else model,
                "eox_date": eox_date,
                "eox_status": _eox_status(eox_date, today),
                "replacement_year": replacement_year,
                "budget_year": budget_year,
            }

        counts[tenant_pk][year][dt_pk] += 1

    tenants = []
    for tenant_pk, years_data in sorted(counts.items(), key=lambda x: tenant_info[x[0]]["name"]):
        year_groups = []
        for year, dt_counts in sorted(years_data.items()):
            dt_rows = []
            for dt_pk, count in sorted(
                dt_counts.items(),
                key=lambda x: (dt_info[x[0]]["eox_date"], dt_info[x[0]]["name"]),
            ):
                dt_rows.append({**dt_info[dt_pk], "count": count})
            year_groups.append({
                "year": year,
                "year_status": "danger" if year < today.year else ("warning" if year == today.year else "success"),
                "device_types": dt_rows,
                "total": sum(dt_counts.values()),
            })
        tenants.append({
            **tenant_info[tenant_pk],
            "year_groups": year_groups,
            "total": sum(sum(dc.values()) for dc in years_data.values()),
        })

    return {"tenants": tenants}


class EoXByTenantReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "dcim.view_device"
    template_name = "netbox_insights/eox_by_tenant_report.html"

    def get(self, request):
        data = _build_eox_by_tenant_report()
        if request.GET.get("format") == "csv":
            return _eox_by_tenant_csv(data)
        return render(request, self.template_name, data)


def _build_eox_by_year_report(site_ids=None, device_type_ids=None, tenant_ids=None, manufacturer_ids=None, active_only=True):
    today = now().date()

    qs = (
        device_insights_queryset()
        .filter(tracked_eox_date__isnull=False)
        .prefetch_related(None)
        .select_related("site", "tenant", "device_type__manufacturer")
        .order_by("tracked_eox_date", "device_type__manufacturer__name", "device_type__model", "tenant__name")
    )
    if active_only:
        qs = qs.filter(status="active")
    if site_ids:
        qs = qs.filter(site_id__in=site_ids)
    if manufacturer_ids:
        qs = qs.filter(device_type__manufacturer_id__in=manufacturer_ids)
    if device_type_ids:
        qs = qs.filter(device_type_id__in=device_type_ids)
    if tenant_ids:
        qs = qs.filter(tenant_id__in=tenant_ids)

    # year → dt_pk → tenant_pk → count
    counts: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    dt_info: dict = {}
    tenant_info: dict = {}

    for device in qs.iterator():
        year = device.tracked_eox_date.year
        dt_pk = device.device_type_id
        tenant_pk = device.tenant_id or 0

        if dt_pk not in dt_info:
            mfr = (
                device.device_type.manufacturer.name
                if device.device_type and device.device_type.manufacturer
                else ""
            )
            model = device.device_type.model if device.device_type else ""
            eox_date = device.tracked_eox_date
            replacement_year, budget_year = _lifecycle_years(eox_date)
            dt_info[dt_pk] = {
                "pk": dt_pk,
                "name": f"{mfr} {model}".strip() if mfr else model,
                "eox_date": eox_date,
                "eox_status": _eox_status(eox_date, today),
                "replacement_year": replacement_year,
                "budget_year": budget_year,
            }

        if tenant_pk not in tenant_info:
            tenant_info[tenant_pk] = {
                "pk": tenant_pk or None,
                "name": device.tenant.name if device.tenant else "(No Tenant)",
            }

        counts[year][dt_pk][tenant_pk] += 1

    years = []
    for year, dts_data in sorted(counts.items()):
        dt_rows = []
        for dt_pk, tenant_counts in sorted(
            dts_data.items(),
            key=lambda x: (dt_info[x[0]]["eox_date"], dt_info[x[0]]["name"]),
        ):
            tenant_rows = []
            for tenant_pk, count in sorted(
                tenant_counts.items(), key=lambda x: tenant_info[x[0]]["name"]
            ):
                tenant_rows.append({**tenant_info[tenant_pk], "count": count})
            dt_rows.append({
                **dt_info[dt_pk],
                "tenants": tenant_rows,
                "total": sum(tenant_counts.values()),
            })
        years.append({
            "year": year,
            "year_status": "danger" if year < today.year else ("warning" if year == today.year else "success"),
            "device_types": dt_rows,
            "total": sum(sum(tc.values()) for tc in dts_data.values()),
        })

    return {"years": years}


class EoXByYearReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "dcim.view_device"
    template_name = "netbox_insights/eox_by_year_report.html"

    def get(self, request):
        data = _build_eox_by_year_report()
        if request.GET.get("format") == "csv":
            return _eox_by_year_csv(data)
        return render(request, self.template_name, data)


_REPORT_CONFIG = {
    "summary":        ("By Site",        _build_eox_report,               _eox_summary_csv),
    "by_device_type": ("By Device Type", _build_eox_by_device_type_report, _eox_by_device_type_csv),
    "by_tenant":      ("By Tenant",      _build_eox_by_tenant_report,      _eox_by_tenant_csv),
    "by_year":        ("By Year",        _build_eox_by_year_report,        _eox_by_year_csv),
}


# ── Contract Coverage Report ──────────────────────────────────────────────────

def _coverage_status(covered, total):
    if not total:
        return None, None
    pct = round(covered / total * 100, 1)
    status = "success" if pct >= 90 else ("warning" if pct >= 50 else "danger")
    return pct, status


def _dual_coverage(covered, uncovered, unknown, excluded):
    """Return (eligible_pct, eligible_status, total_pct, total_status).

    Eligible denominator excludes intentionally-excluded devices.
    Total denominator covers all asset-linked devices.
    """
    eligible = covered + uncovered + unknown
    total = eligible + excluded
    elig_pct, elig_status = _coverage_status(covered, eligible)
    total_pct, total_status = _coverage_status(covered, total)
    return elig_pct, elig_status, total_pct, total_status


def _asset_exists_subquery():
    from netbox_inventory.models.assets import Asset
    return Exists(Asset.objects.filter(device_id=OuterRef("pk")))


def _asset_state_subquery():
    from netbox_inventory.models.assets import Asset
    return Subquery(Asset.objects.filter(device_id=OuterRef("pk")).values("support_state")[:1])


def _asset_reason_subquery():
    from netbox_inventory.models.assets import Asset
    return Subquery(Asset.objects.filter(device_id=OuterRef("pk")).values("support_reason")[:1])


def _build_contract_by_site_report(site_ids=None, manufacturer_ids=None, device_type_ids=None, tenant_ids=None, active_only=True):
    qs = (
        device_insights_queryset()
        .prefetch_related(None)
        .annotate(
            _has_asset=_asset_exists_subquery(),
            _asset_state=_asset_state_subquery(),
        )
        .order_by("site__name", "tenant__name")
    )
    if active_only:
        qs = qs.filter(status="active")
    if site_ids:
        qs = qs.filter(site_id__in=site_ids)
    if manufacturer_ids:
        qs = qs.filter(device_type__manufacturer_id__in=manufacturer_ids)
    if device_type_ids:
        qs = qs.filter(device_type_id__in=device_type_ids)
    if tenant_ids:
        qs = qs.filter(tenant_id__in=tenant_ids)

    # Buckets tracked per (site, tenant): the 4 asset states + "no_asset".
    # "no_asset" is excluded from the coverage % denominator.
    ASSET_STATES = ("covered", "uncovered", "excluded", "unknown")

    counts: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    site_names: dict = {}
    tenant_names: dict = {}

    for device in qs.iterator():
        site_pk = device.site_id or 0
        site_names[site_pk] = device.site.name if device.site else "(No Site)"
        tenant_pk = device.tenant_id or 0
        tenant_names[(site_pk, tenant_pk)] = device.tenant.name if device.tenant else "(No Tenant)"

        if not device._has_asset:
            state = "no_asset"
        else:
            state = device._asset_state or "unknown"
        counts[site_pk][tenant_pk][state] += 1

    sites = []
    for site_pk, tenants_data in sorted(counts.items(), key=lambda x: site_names.get(x[0], "")):
        tenant_list = []
        site_totals: dict = defaultdict(int)
        for tenant_pk, state_counts in sorted(tenants_data.items(), key=lambda x: tenant_names.get((site_pk, x[0]), "")):
            no_asset  = state_counts.get("no_asset", 0)
            covered   = state_counts.get("covered", 0)
            uncovered = state_counts.get("uncovered", 0)
            excluded  = state_counts.get("excluded", 0)
            unknown   = state_counts.get("unknown", 0)
            with_asset = covered + uncovered + excluded + unknown
            for s in ASSET_STATES:
                site_totals[s] += state_counts.get(s, 0)
            site_totals["no_asset"] += no_asset
            elig_pct, elig_status, tot_pct, tot_status = _dual_coverage(covered, uncovered, unknown, excluded)
            tenant_list.append({
                "pk": tenant_pk or None,
                "name": tenant_names.get((site_pk, tenant_pk), "(No Tenant)"),
                "total": with_asset + no_asset,
                "with_asset": with_asset,
                "covered": covered,
                "uncovered": uncovered,
                "excluded": excluded,
                "unknown": unknown,
                "no_asset": no_asset,
                "eligible_pct": elig_pct,
                "eligible_status": elig_status,
                "coverage_pct": tot_pct,
                "coverage_status": tot_status,
            })
        site_with_asset = sum(site_totals.get(s, 0) for s in ASSET_STATES)
        site_no_asset = site_totals.get("no_asset", 0)
        sc = site_totals["covered"]
        su = site_totals["uncovered"]
        sx = site_totals["excluded"]
        sk = site_totals["unknown"]
        s_elig_pct, s_elig_status, s_tot_pct, s_tot_status = _dual_coverage(sc, su, sk, sx)
        sites.append({
            "pk": site_pk or None,
            "name": site_names.get(site_pk, "(No Site)"),
            "tenants": tenant_list,
            "total": site_with_asset + site_no_asset,
            "with_asset": site_with_asset,
            "covered": sc,
            "uncovered": su,
            "excluded": sx,
            "unknown": sk,
            "no_asset": site_no_asset,
            "eligible_pct": s_elig_pct,
            "eligible_status": s_elig_status,
            "coverage_pct": s_tot_pct,
            "coverage_status": s_tot_status,
        })

    return {"sites": sites}


def _build_contract_uncovered_report(site_ids=None, manufacturer_ids=None, device_type_ids=None, tenant_ids=None, active_only=True):
    from netbox_inventory.choices import AssetSupportStateChoices, AssetSupportReasonChoices

    _state_color = {c[0]: c[2] for c in AssetSupportStateChoices.CHOICES}
    _state_label = {c[0]: str(c[1]) for c in AssetSupportStateChoices.CHOICES}
    _reason_color = {c[0]: c[2] for c in AssetSupportReasonChoices.CHOICES}
    _reason_label = {c[0]: str(c[1]) for c in AssetSupportReasonChoices.CHOICES}

    qs = (
        device_insights_queryset()
        .prefetch_related(None)
        .annotate(
            _has_asset=_asset_exists_subquery(),
            _asset_state=_asset_state_subquery(),
            _asset_reason=_asset_reason_subquery(),
        )
        .order_by("site__name", "tenant__name", "name")
    )
    if active_only:
        qs = qs.filter(status="active")
    if site_ids:
        qs = qs.filter(site_id__in=site_ids)
    if manufacturer_ids:
        qs = qs.filter(device_type__manufacturer_id__in=manufacturer_ids)
    if device_type_ids:
        qs = qs.filter(device_type_id__in=device_type_ids)
    if tenant_ids:
        qs = qs.filter(tenant_id__in=tenant_ids)

    # Per (site, tenant): list of non-covered devices and list of no-asset devices
    covered_rows: dict = defaultdict(lambda: defaultdict(list))
    no_asset_rows: dict = defaultdict(lambda: defaultdict(list))
    site_names: dict = {}
    tenant_names: dict = {}

    for device in qs.iterator():
        if not device._has_asset:
            state = "no_asset"
        else:
            state = device._asset_state or "unknown"

        if state == "covered":
            continue

        site_pk = device.site_id or 0
        site_names[site_pk] = device.site.name if device.site else "(No Site)"
        tenant_pk = device.tenant_id or 0
        tenant_names[(site_pk, tenant_pk)] = device.tenant.name if device.tenant else "(No Tenant)"

        mfr = device.device_type.manufacturer.name if device.device_type and device.device_type.manufacturer else ""
        model = device.device_type.model if device.device_type else ""
        contract_type = device.support_contract_type
        contract_type_label = {"support-ea": "EA", "support-alc": "ALC"}.get(contract_type)

        row = {
            "pk": device.pk,
            "name": device.name,
            "device_type_pk": device.device_type_id,
            "device_type": f"{mfr} {model}".strip() if mfr else model,
            "contract_type_label": contract_type_label,
            "contract_end_date": device.support_contract_end_date,
        }

        if state == "no_asset":
            no_asset_rows[site_pk][tenant_pk].append(row)
        else:
            reason = device._asset_reason
            row.update({
                "support_state": state,
                "support_state_color": _state_color.get(state, "secondary"),
                "support_state_display": _state_label.get(state, state.capitalize()),
                "support_reason": reason,
                "support_reason_color": _reason_color.get(reason, "secondary") if reason else None,
                "support_reason_display": _reason_label.get(reason, reason.capitalize()) if reason else "—",
            })
            covered_rows[site_pk][tenant_pk].append(row)

    all_site_pks = set(covered_rows) | set(no_asset_rows)
    sites = []
    for site_pk in sorted(all_site_pks, key=lambda x: site_names.get(x, "")):
        tenant_pks = set(covered_rows.get(site_pk, {})) | set(no_asset_rows.get(site_pk, {}))
        tenant_list = []
        for tenant_pk in sorted(tenant_pks, key=lambda x: tenant_names.get((site_pk, x), "")):
            devices = covered_rows.get(site_pk, {}).get(tenant_pk, [])
            no_asset = no_asset_rows.get(site_pk, {}).get(tenant_pk, [])
            tenant_list.append({
                "pk": tenant_pk or None,
                "name": tenant_names.get((site_pk, tenant_pk), "(No Tenant)"),
                "devices": devices,
                "no_asset_devices": no_asset,
                "total": len(devices) + len(no_asset),
            })
        sites.append({
            "pk": site_pk or None,
            "name": site_names.get(site_pk, "(No Site)"),
            "tenants": tenant_list,
            "total": sum(t["total"] for t in tenant_list),
        })

    return {"sites": sites}


_EA_CUTOFF_YEAR = 2031


def _build_contract_by_year_report(site_ids=None, manufacturer_ids=None, device_type_ids=None, tenant_ids=None, active_only=True):
    today = now().date()

    qs = (
        device_insights_queryset()
        .prefetch_related(None)
        .filter(support_contract_end_date__isnull=False)
        .order_by("support_contract_end_date", "name")
    )
    if active_only:
        qs = qs.filter(status="active")
    if site_ids:
        qs = qs.filter(site_id__in=site_ids)
    if manufacturer_ids:
        qs = qs.filter(device_type__manufacturer_id__in=manufacturer_ids)
    if device_type_ids:
        qs = qs.filter(device_type_id__in=device_type_ids)
    if tenant_ids:
        qs = qs.filter(tenant_id__in=tenant_ids)

    # year → contract_pk → accumulation dict
    contracts_by_year: dict = defaultdict(dict)
    tenant_names: dict = {}

    for device in qs.iterator():
        end_date = device.support_contract_end_date
        year = end_date.year
        contract_pk = device.support_contract_pk or f"anon_{year}"
        contract_type = device.support_contract_type

        mfr = (
            device.device_type.manufacturer.name
            if device.device_type and device.device_type.manufacturer
            else ""
        )
        is_cisco = "cisco" in mfr.lower() if mfr else False

        tenant_pk = device.tenant_id or 0
        tenant_names[tenant_pk] = device.tenant.name if device.tenant else "(No Tenant)"

        if contract_pk not in contracts_by_year[year]:
            contracts_by_year[year][contract_pk] = {
                "contract_pk": device.support_contract_pk,
                "contract_id": device.support_contract_id or "—",
                "contract_type": contract_type,
                "contract_type_label": "EA" if contract_type == "support-ea" else "ALC",
                "end_date": end_date,
                "device_count": 0,
                "cisco_count": 0,
                "tenant_counts": defaultdict(int),
            }

        entry = contracts_by_year[year][contract_pk]
        entry["device_count"] += 1
        if is_cisco:
            entry["cisco_count"] += 1
        entry["tenant_counts"][tenant_pk] += 1

    years = []
    for year in sorted(contracts_by_year.keys()):
        if year < today.year:
            year_status = "danger"
        elif year == today.year:
            year_status = "warning"
        else:
            year_status = "success"

        year_contracts = []
        alc_total = 0
        ea_total = 0
        ea_eligible_total = 0

        for contract_pk, info in sorted(
            contracts_by_year[year].items(),
            key=lambda x: (x[1]["contract_type"], x[1]["end_date"]),
        ):
            is_alc = info["contract_type"] == "support-alc"
            is_ea_eligible = is_alc and year < _EA_CUTOFF_YEAR and info["cisco_count"] > 0

            tenants = []
            for tenant_pk, count in sorted(info["tenant_counts"].items(), key=lambda x: tenant_names.get(x[0], "")):
                tenants.append({
                    "pk": tenant_pk or None,
                    "name": tenant_names.get(tenant_pk, "(No Tenant)"),
                    "count": count,
                })

            if is_alc:
                alc_total += info["device_count"]
            else:
                ea_total += info["device_count"]
            if is_ea_eligible:
                ea_eligible_total += info["cisco_count"]

            year_contracts.append({
                "contract_pk": info["contract_pk"],
                "contract_id": info["contract_id"],
                "contract_type": info["contract_type"],
                "contract_type_label": info["contract_type_label"],
                "end_date": info["end_date"],
                "device_count": info["device_count"],
                "cisco_count": info["cisco_count"],
                "ea_eligible": is_ea_eligible,
                "ea_eligible_count": info["cisco_count"] if is_ea_eligible else 0,
                "tenants": tenants,
            })

        years.append({
            "year": year,
            "year_status": year_status,
            "contracts": year_contracts,
            "total": alc_total + ea_total,
            "alc_total": alc_total,
            "ea_total": ea_total,
            "ea_eligible_total": ea_eligible_total,
        })

    return {"years": years, "ea_cutoff_year": _EA_CUTOFF_YEAR}


def _contract_by_site_csv(data):
    response, writer = _csv_response("contract_coverage_by_site.csv")
    writer.writerow(["Site", "Tenant", "Total", "Covered", "Uncovered", "Excluded", "Unknown", "No Asset", "Eligible Coverage %", "Total Coverage %"])
    for site in data["sites"]:
        for tenant in site["tenants"]:
            writer.writerow([
                site["name"], tenant["name"],
                tenant["total"], tenant["covered"], tenant["uncovered"],
                tenant["excluded"], tenant["unknown"], tenant["no_asset"],
                tenant["eligible_pct"] if tenant["eligible_pct"] is not None else "",
                tenant["coverage_pct"] if tenant["coverage_pct"] is not None else "",
            ])
    return response


def _contract_uncovered_csv(data):
    response, writer = _csv_response("contract_uncovered_devices.csv")
    writer.writerow(["Site", "Tenant", "Device", "Device Type", "Support State", "Support Reason", "Contract Type", "Contract End Date"])
    for site in data["sites"]:
        for tenant in site["tenants"]:
            for device in tenant["devices"]:
                writer.writerow([
                    site["name"], tenant["name"],
                    device["name"], device["device_type"],
                    device["support_state_display"], device["support_reason_display"],
                    device["contract_type_label"] or "",
                    device["contract_end_date"] or "",
                ])
            for device in tenant["no_asset_devices"]:
                writer.writerow([
                    site["name"], tenant["name"],
                    device["name"], device["device_type"],
                    "No Asset Linked", "",
                    device["contract_type_label"] or "",
                    device["contract_end_date"] or "",
                ])
    return response


def _contract_by_year_csv(data):
    response, writer = _csv_response("contract_expiry_by_year.csv")
    writer.writerow(["Year", "Contract ID", "Type", "End Date", "Devices", "Cisco Devices", "EA Eligible", "Tenant", "Tenant Count"])
    for year_data in data["years"]:
        for contract in year_data["contracts"]:
            for tenant in contract["tenants"]:
                writer.writerow([
                    year_data["year"],
                    contract["contract_id"],
                    contract["contract_type_label"],
                    contract["end_date"],
                    contract["device_count"],
                    contract["cisco_count"],
                    "Yes" if contract["ea_eligible"] else "No",
                    tenant["name"],
                    tenant["count"],
                ])
    return response


_CONTRACT_REPORT_CONFIG = {
    "by_site":   ("By Site",           _build_contract_by_site_report,   _contract_by_site_csv),
    "uncovered": ("Uncovered Devices", _build_contract_uncovered_report,  _contract_uncovered_csv),
    "by_year":   ("By Contract Year",  _build_contract_by_year_report,    _contract_by_year_csv),
}


class EoXReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "dcim.view_device"
    template_name = "netbox_insights/eox_report.html"

    def get(self, request):
        from ..forms.reports import EoXReportFilterForm

        report_key = request.GET.get("report", "summary")
        if report_key not in _REPORT_CONFIG:
            report_key = "summary"

        form = EoXReportFilterForm(request.GET or None)

        # "submitted" sentinel: distinguishes first load (default active_only=True)
        # from a form submission where the checkbox may be intentionally unchecked.
        submitted = "submitted" in request.GET

        filters = {}
        if form.is_valid():
            if sites := form.cleaned_data.get("site"):
                filters["site_ids"] = [s.pk for s in sites]
            if manufacturers := form.cleaned_data.get("manufacturer"):
                filters["manufacturer_ids"] = [m.pk for m in manufacturers]
            if dts := form.cleaned_data.get("device_type"):
                filters["device_type_ids"] = [dt.pk for dt in dts]
            if tenants := form.cleaned_data.get("tenant"):
                filters["tenant_ids"] = [t.pk for t in tenants]
            filters["active_only"] = form.cleaned_data.get("active_only", False) if submitted else True
        else:
            filters["active_only"] = True

        label, builder, csv_func = _REPORT_CONFIG[report_key]
        data = builder(**filters) if submitted else {}

        if submitted and request.GET.get("format") == "csv":
            return csv_func(data)

        # Build per-tab URLs that preserve active filters but swap the report type.
        tab_urls = {}
        for key in _REPORT_CONFIG:
            params = request.GET.copy()
            params["report"] = key
            params.pop("format", None)
            tab_urls[key] = "?" + params.urlencode()

        # CSV export URL: current params + format=csv
        csv_params = request.GET.copy()
        csv_params["format"] = "csv"
        csv_url = "?" + csv_params.urlencode()

        return render(request, self.template_name, {
            **data,
            "form": form,
            "active_only": filters["active_only"],
            "report_key": report_key,
            "report_label": label,
            "tab_urls": tab_urls,
            "csv_url": csv_url,
            "submitted": submitted,
        })


class ContractCoverageReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "dcim.view_device"
    template_name = "netbox_insights/contract_coverage_report.html"

    def get(self, request):
        from ..forms.reports import ContractCoverageFilterForm

        report_key = request.GET.get("report", "by_site")
        if report_key not in _CONTRACT_REPORT_CONFIG:
            report_key = "by_site"

        form = ContractCoverageFilterForm(request.GET or None)
        submitted = "submitted" in request.GET

        filters = {}
        if form.is_valid():
            if sites := form.cleaned_data.get("site"):
                filters["site_ids"] = [s.pk for s in sites]
            if manufacturers := form.cleaned_data.get("manufacturer"):
                filters["manufacturer_ids"] = [m.pk for m in manufacturers]
            if dts := form.cleaned_data.get("device_type"):
                filters["device_type_ids"] = [dt.pk for dt in dts]
            if tenants := form.cleaned_data.get("tenant"):
                filters["tenant_ids"] = [t.pk for t in tenants]
            filters["active_only"] = form.cleaned_data.get("active_only", False) if submitted else True
        else:
            filters["active_only"] = True

        label, builder, csv_func = _CONTRACT_REPORT_CONFIG[report_key]
        data = builder(**filters) if submitted else {}

        if submitted and request.GET.get("format") == "csv":
            return csv_func(data)

        tab_urls = {}
        for key in _CONTRACT_REPORT_CONFIG:
            params = request.GET.copy()
            params["report"] = key
            params.pop("format", None)
            tab_urls[key] = "?" + params.urlencode()

        csv_params = request.GET.copy()
        csv_params["format"] = "csv"
        csv_url = "?" + csv_params.urlencode()

        return render(request, self.template_name, {
            **data,
            "form": form,
            "active_only": filters["active_only"],
            "report_key": report_key,
            "report_label": label,
            "tab_urls": tab_urls,
            "csv_url": csv_url,
            "submitted": submitted,
            "selected_tenant_ids": filters.get("tenant_ids", []),
        })

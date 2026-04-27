import csv
from collections import defaultdict

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.timezone import now
from django.views import View

from ..querysets import device_insights_queryset


__all__ = (
    'EoXSummaryReportView',
    'EoXByDeviceTypeReportView',
    'EoXByTenantReportView',
    'EoXByYearReportView',
)


def _build_eox_report():
    today = now().date()
    current_year = today.year

    qs = (
        device_insights_queryset()
        .filter(tracked_eox_date__isnull=False)
        .prefetch_related(None)  # assigned_asset prefetch not needed here
        .select_related("site", "tenant", "device_type__manufacturer")
        .order_by("site__name", "tenant__name", "device_type__manufacturer__name", "device_type__model")
    )

    # site_pk → tenant_pk → dt_pk → year → device count
    counts: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int))))
    site_names: dict = {}
    tenant_names: dict = {}
    dt_names: dict = {}
    all_years: set = set()

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

        year = device.tracked_eox_date.year
        counts[site_pk][tenant_pk][dt_pk][year] += 1
        all_years.add(year)

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
                dt_list.append({
                    "pk": dt_pk,
                    "name": dt_names.get(dt_pk, ""),
                    # List of (year, count) tuples in sorted year order — avoids dict lookups in template
                    "year_counts": [(y, year_data.get(y, 0)) for y in all_years_sorted],
                    "total": sum(year_data.values()),
                })
            tenant_list.append({
                "pk": tenant_pk or None,
                "name": tenant_names.get((site_pk, tenant_pk), "(No Tenant)"),
                "device_types": dt_list,
                "total": sum(sum(yd.values()) for yd in dts_data.values()),
            })
        sites.append({
            "pk": site_pk or None,
            "name": site_names.get(site_pk, "(No Site)"),
            "tenants": tenant_list,
            "total": sum(t["total"] for t in tenant_list),
        })

    return {
        "sites": sites,
        "all_years": all_years_sorted,
        "current_year": current_year,
    }


def _build_eox_by_device_type_report():
    today = now().date()

    qs = (
        device_insights_queryset()
        .filter(tracked_eox_date__isnull=False)
        .prefetch_related(None)
        .select_related("site", "tenant", "device_type__manufacturer")
        .order_by("device_type__manufacturer__name", "device_type__model", "site__name", "tenant__name")
    )

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
            dt_info[dt_pk] = {
                "pk": dt_pk,
                "name": f"{mfr} {model}".strip() if mfr else model,
                "manufacturer": mfr,
                "eox_date": device.tracked_eox_date,
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
    writer.writerow(["Site", "Tenant", "Device Type"] + [str(y) for y in data["all_years"]] + ["Total"])
    for site in data["sites"]:
        for tenant in site["tenants"]:
            for dt in tenant["device_types"]:
                writer.writerow(
                    [site["name"], tenant["name"], dt["name"]]
                    + [count for _, count in dt["year_counts"]]
                    + [dt["total"]]
                )
    return response


def _eox_by_device_type_csv(data):
    response, writer = _csv_response("eox_by_device_type_report.csv")
    writer.writerow(["Device Type", "Manufacturer", "EoX Date", "EoX Status", "Site", "Tenant", "Count"])
    for dt in data["device_types"]:
        for site in dt["sites"]:
            for tenant in site["tenants"]:
                writer.writerow([
                    dt["name"], dt["manufacturer"], dt["eox_date"], dt["eox_status"],
                    site["name"], tenant["name"], tenant["count"],
                ])
    return response


def _eox_by_tenant_csv(data):
    response, writer = _csv_response("eox_by_tenant_report.csv")
    writer.writerow(["Tenant", "Year", "Device Type", "EoX Date", "EoX Status", "Count"])
    for tenant in data["tenants"]:
        for year_group in tenant["year_groups"]:
            for dt in year_group["device_types"]:
                writer.writerow([
                    tenant["name"], year_group["year"],
                    dt["name"], dt["eox_date"], dt["eox_status"], dt["count"],
                ])
    return response


def _eox_by_year_csv(data):
    response, writer = _csv_response("eox_by_year_report.csv")
    writer.writerow(["Year", "Device Type", "EoX Date", "EoX Status", "Tenant", "Count"])
    for year in data["years"]:
        for dt in year["device_types"]:
            for tenant in dt["tenants"]:
                writer.writerow([
                    year["year"], dt["name"], dt["eox_date"], dt["eox_status"],
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


def _build_eox_by_tenant_report():
    today = now().date()

    qs = (
        device_insights_queryset()
        .filter(tracked_eox_date__isnull=False)
        .prefetch_related(None)
        .select_related("site", "tenant", "device_type__manufacturer")
        .order_by("tenant__name", "tracked_eox_date", "device_type__manufacturer__name", "device_type__model")
    )

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
            dt_info[dt_pk] = {
                "pk": dt_pk,
                "name": f"{mfr} {model}".strip() if mfr else model,
                "eox_date": eox_date,
                "eox_status": _eox_status(eox_date, today),
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


def _build_eox_by_year_report():
    today = now().date()

    qs = (
        device_insights_queryset()
        .filter(tracked_eox_date__isnull=False)
        .prefetch_related(None)
        .select_related("site", "tenant", "device_type__manufacturer")
        .order_by("tracked_eox_date", "device_type__manufacturer__name", "device_type__model", "tenant__name")
    )

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
            dt_info[dt_pk] = {
                "pk": dt_pk,
                "name": f"{mfr} {model}".strip() if mfr else model,
                "eox_date": eox_date,
                "eox_status": _eox_status(eox_date, today),
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

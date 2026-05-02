import csv
from collections import defaultdict
from datetime import date

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Case, DateField, F, OuterRef, Q, Subquery, When
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.utils.timezone import now
from django.views import View

from .reports import _csv_response, _eox_status, _lifecycle_years
from ..querysets import _get_device_type_ct

__all__ = ('AssetEoXReportView', 'AssetContractCoverageReportView')

SUPPORT_CONTRACT_TYPES = ("support-alc", "support-ea")


# ── Shared helpers ────────────────────────────────────────────────────────────

def _asset_base_qs():
    from netbox_inventory.models import Asset
    from netbox_inventory.models.hardware import HardwareLifecycle

    device_type_ct = _get_device_type_ct()
    lifecycle_qs = HardwareLifecycle.objects.filter(
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
        Asset.objects.filter(device_type__isnull=False)
        .annotate(
            tracked_eox_date=Subquery(lifecycle_qs.values("tracked_eox_date")[:1]),
        )
        .select_related(
            "device_type__manufacturer",
            "owning_tenant",
            "device__site",
            "installed_site_override",
        )
    )


def _apply_filters(qs, site_ids=None, device_type_ids=None, owning_tenant_ids=None,
                   manufacturer_ids=None, exclude_retired=True):
    if exclude_retired:
        qs = qs.exclude(status__in=["retired", "disposed"])
    if site_ids:
        qs = qs.filter(
            Q(device__site_id__in=site_ids) |
            Q(device__isnull=True, installed_site_override_id__in=site_ids)
        )
    if manufacturer_ids:
        qs = qs.filter(device_type__manufacturer_id__in=manufacturer_ids)
    if device_type_ids:
        qs = qs.filter(device_type_id__in=device_type_ids)
    if owning_tenant_ids:
        qs = qs.filter(owning_tenant_id__in=owning_tenant_ids)
    return qs


def _resolve_site(asset):
    if asset.device_id:
        return asset.device.site if asset.device else None
    return asset.installed_site_override


def _dt_entry(asset, today):
    mfr = (
        asset.device_type.manufacturer.name
        if asset.device_type and asset.device_type.manufacturer else ""
    )
    model = asset.device_type.model if asset.device_type else ""
    eox_date = asset.tracked_eox_date
    replacement_year, budget_year = _lifecycle_years(eox_date)
    return {
        "pk": asset.device_type_id,
        "name": f"{mfr} {model}".strip() if mfr else model,
        "manufacturer": mfr,
        "eox_date": eox_date,
        "eox_status": _eox_status(eox_date, today) if eox_date else "secondary",
        "replacement_year": replacement_year,
        "budget_year": budget_year,
    }


def _coverage(state_counts):
    covered = state_counts.get("covered", 0)
    uncovered = state_counts.get("uncovered", 0)
    excluded = state_counts.get("excluded", 0)
    unknown = state_counts.get("unknown", 0)
    total = covered + uncovered + excluded + unknown
    eligible = covered + uncovered + unknown
    if eligible:
        pct = round(covered / eligible * 100, 1)
        status = "success" if pct >= 90 else ("warning" if pct >= 50 else "danger")
    else:
        pct = status = None
    return {
        "covered": covered, "uncovered": uncovered,
        "excluded": excluded, "unknown": unknown,
        "total": total, "eligible_pct": pct, "eligible_status": status,
    }


# ══════════════════════════════════════════════════════════════════════════════
# EoX Report
# ══════════════════════════════════════════════════════════════════════════════

def _eox_qs(site_ids=None, device_type_ids=None, owning_tenant_ids=None,
             manufacturer_ids=None, exclude_retired=True, order_by=None):
    qs = _apply_filters(
        _asset_base_qs().filter(tracked_eox_date__isnull=False),
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
    )
    if order_by:
        qs = qs.order_by(*order_by)
    return qs


# ── By Site ───────────────────────────────────────────────────────────────────

def _build_asset_eox_by_site(site_ids=None, device_type_ids=None,
                              owning_tenant_ids=None, manufacturer_ids=None,
                              exclude_retired=True):
    today = now().date()
    current_year = today.year

    qs = _eox_qs(
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
        order_by=["device__site__name", "installed_site_override__name",
                  "owning_tenant__name", "device_type__manufacturer__name", "device_type__model"],
    )

    # site_pk → ot_pk → dt_pk → year → count
    counts: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int))))
    site_names: dict = {}
    ot_names: dict = {}
    dt_names: dict = {}
    dt_eox_dates: dict = {}
    all_years: set = set()
    past_eox_by_site: dict = defaultdict(int)

    for asset in qs.iterator():
        site = _resolve_site(asset)
        site_pk = site.pk if site else 0
        site_names[site_pk] = site.name if site else "(No Site)"

        ot_pk = asset.owning_tenant_id or 0
        ot_names[(site_pk, ot_pk)] = asset.owning_tenant.name if asset.owning_tenant else "(No Owner)"

        dt_pk = asset.device_type_id
        if dt_pk not in dt_names:
            mfr = (asset.device_type.manufacturer.name
                   if asset.device_type and asset.device_type.manufacturer else "")
            model = asset.device_type.model if asset.device_type else ""
            dt_names[dt_pk] = f"{mfr} {model}".strip() if mfr else model
            dt_eox_dates[dt_pk] = asset.tracked_eox_date

        year = asset.tracked_eox_date.year
        counts[site_pk][ot_pk][dt_pk][year] += 1
        all_years.add(year)

        if asset.tracked_eox_date < today:
            past_eox_by_site[site_pk] += 1

    all_years_sorted = sorted(all_years)

    sites = []
    for site_pk, ot_data in sorted(counts.items(), key=lambda x: site_names.get(x[0], "")):
        ot_list = []
        site_total = 0
        for ot_pk, dt_data in sorted(ot_data.items(), key=lambda x: ot_names.get((site_pk, x[0]), "")):
            dt_list = []
            for dt_pk, year_data in sorted(
                dt_data.items(),
                key=lambda x: (dt_eox_dates.get(x[0]) or date.max, dt_names.get(x[0], "")),
            ):
                repl_year, budget_year = _lifecycle_years(dt_eox_dates.get(dt_pk))
                dt_list.append({
                    "pk": dt_pk,
                    "name": dt_names.get(dt_pk, ""),
                    "year_counts": [(y, year_data.get(y, 0)) for y in all_years_sorted],
                    "total": sum(year_data.values()),
                    "replacement_year": repl_year,
                    "budget_year": budget_year,
                })
            ot_total = sum(dt["total"] for dt in dt_list)
            site_total += ot_total
            ot_list.append({
                "pk": ot_pk or None,
                "name": ot_names.get((site_pk, ot_pk), "(No Owner)"),
                "device_types": dt_list,
                "total": ot_total,
            })

        past_eox = past_eox_by_site.get(site_pk, 0)
        if site_total:
            eox_pct = round(past_eox / site_total * 100, 1)
            eox_pct_status = "success" if eox_pct == 0 else ("danger" if eox_pct >= 25 else "warning")
        else:
            eox_pct = eox_pct_status = None

        sites.append({
            "pk": site_pk or None,
            "name": site_names.get(site_pk, "(No Site)"),
            "owning_tenants": ot_list,
            "total": site_total,
            "eox_pct": eox_pct,
            "eox_pct_status": eox_pct_status,
            "past_eox_count": past_eox,
        })

    return {"sites": sites, "all_years": all_years_sorted, "current_year": current_year}


# ── By Device Type ────────────────────────────────────────────────────────────

def _build_asset_eox_by_device_type(site_ids=None, device_type_ids=None,
                                    owning_tenant_ids=None, manufacturer_ids=None,
                                    exclude_retired=True):
    today = now().date()

    qs = _eox_qs(
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
        order_by=["device_type__manufacturer__name", "device_type__model",
                  "device__site__name", "installed_site_override__name", "owning_tenant__name"],
    )

    # dt_pk → site_pk → ot_pk → count
    counts: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    dt_info: dict = {}
    site_names: dict = {}
    ot_names: dict = {}

    for asset in qs.iterator():
        dt_pk = asset.device_type_id
        if dt_pk not in dt_info:
            dt_info[dt_pk] = _dt_entry(asset, today)

        site = _resolve_site(asset)
        site_pk = site.pk if site else 0
        site_names[site_pk] = site.name if site else "(No Site)"

        ot_pk = asset.owning_tenant_id or 0
        ot_names[(site_pk, ot_pk)] = asset.owning_tenant.name if asset.owning_tenant else "(No Owner)"

        counts[dt_pk][site_pk][ot_pk] += 1

    device_types = []
    for dt_pk, sites_data in sorted(
        counts.items(),
        key=lambda x: (dt_info[x[0]]["eox_date"] or date.max, dt_info[x[0]]["name"]),
    ):
        site_list = []
        for site_pk, ot_data in sorted(sites_data.items(), key=lambda x: site_names.get(x[0], "")):
            ot_list = []
            for ot_pk, count in sorted(ot_data.items(), key=lambda x: ot_names.get((site_pk, x[0]), "")):
                ot_list.append({
                    "pk": ot_pk or None,
                    "name": ot_names.get((site_pk, ot_pk), "(No Owner)"),
                    "count": count,
                })
            site_list.append({
                "pk": site_pk or None,
                "name": site_names.get(site_pk, "(No Site)"),
                "owning_tenants": ot_list,
                "total": sum(ot["count"] for ot in ot_list),
            })
        device_types.append({
            **dt_info[dt_pk],
            "sites": site_list,
            "total": sum(s["total"] for s in site_list),
        })

    return {"device_types": device_types}


# ── By Owning Tenant ──────────────────────────────────────────────────────────

def _build_asset_eox_by_owning_tenant(site_ids=None, device_type_ids=None,
                                      owning_tenant_ids=None, manufacturer_ids=None,
                                      exclude_retired=True):
    today = now().date()

    qs = _eox_qs(
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
        order_by=["owning_tenant__name", "tracked_eox_date",
                  "device_type__manufacturer__name", "device_type__model"],
    )

    # ot_pk → year → dt_pk → count
    counts: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    ot_info: dict = {}
    dt_info: dict = {}

    for asset in qs.iterator():
        ot_pk = asset.owning_tenant_id or 0
        if ot_pk not in ot_info:
            ot_info[ot_pk] = {
                "pk": ot_pk or None,
                "name": asset.owning_tenant.name if asset.owning_tenant else "(No Owner)",
            }

        dt_pk = asset.device_type_id
        if dt_pk not in dt_info:
            dt_info[dt_pk] = _dt_entry(asset, today)

        year = asset.tracked_eox_date.year
        counts[ot_pk][year][dt_pk] += 1

    owning_tenants = []
    for ot_pk, years_data in sorted(counts.items(), key=lambda x: ot_info[x[0]]["name"]):
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
        owning_tenants.append({
            **ot_info[ot_pk],
            "year_groups": year_groups,
            "total": sum(sum(dc.values()) for dc in years_data.values()),
        })

    return {"owning_tenants": owning_tenants}


# ── By Year ───────────────────────────────────────────────────────────────────

def _build_asset_eox_by_year(site_ids=None, device_type_ids=None,
                              owning_tenant_ids=None, manufacturer_ids=None,
                              exclude_retired=True):
    today = now().date()

    qs = _eox_qs(
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
        order_by=["tracked_eox_date", "device_type__manufacturer__name",
                  "device_type__model", "owning_tenant__name"],
    )

    # year → dt_pk → ot_pk → count
    counts: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    dt_info: dict = {}
    ot_info: dict = {}

    for asset in qs.iterator():
        year = asset.tracked_eox_date.year
        dt_pk = asset.device_type_id
        ot_pk = asset.owning_tenant_id or 0

        if dt_pk not in dt_info:
            dt_info[dt_pk] = _dt_entry(asset, today)
        if ot_pk not in ot_info:
            ot_info[ot_pk] = {
                "pk": ot_pk or None,
                "name": asset.owning_tenant.name if asset.owning_tenant else "(No Owner)",
            }

        counts[year][dt_pk][ot_pk] += 1

    years = []
    for year, dts_data in sorted(counts.items()):
        dt_rows = []
        for dt_pk, ot_counts in sorted(
            dts_data.items(),
            key=lambda x: (dt_info[x[0]]["eox_date"], dt_info[x[0]]["name"]),
        ):
            ot_rows = []
            for ot_pk, count in sorted(ot_counts.items(), key=lambda x: ot_info[x[0]]["name"]):
                ot_rows.append({**ot_info[ot_pk], "count": count})
            dt_rows.append({
                **dt_info[dt_pk],
                "owning_tenants": ot_rows,
                "total": sum(ot_counts.values()),
            })
        years.append({
            "year": year,
            "year_status": "danger" if year < today.year else ("warning" if year == today.year else "success"),
            "device_types": dt_rows,
            "total": sum(sum(oc.values()) for oc in dts_data.values()),
        })

    return {"years": years}


# ── EoX CSV exports ───────────────────────────────────────────────────────────

def _asset_eox_by_site_csv(data):
    response, writer = _csv_response("asset_eox_by_site.csv")
    writer.writerow(["Site", "Owning Tenant", "Device Type", "Replacement Year", "Budget Year"]
                    + [str(y) for y in data["all_years"]] + ["Total"])
    for site in data["sites"]:
        for ot in site["owning_tenants"]:
            for dt in ot["device_types"]:
                writer.writerow(
                    [site["name"], ot["name"], dt["name"],
                     dt["replacement_year"] or "-", dt["budget_year"] or "-"]
                    + [count for _, count in dt["year_counts"]]
                    + [dt["total"]]
                )
    return response


def _asset_eox_by_device_type_csv(data):
    response, writer = _csv_response("asset_eox_by_device_type.csv")
    writer.writerow(["Device Type", "Manufacturer", "EoX Date", "Replacement Year", "Budget Year",
                     "Site", "Owning Tenant", "Count"])
    for dt in data["device_types"]:
        for site in dt["sites"]:
            for ot in site["owning_tenants"]:
                writer.writerow([
                    dt["name"], dt["manufacturer"], dt["eox_date"] or "-",
                    dt["replacement_year"] or "-", dt["budget_year"] or "-",
                    site["name"], ot["name"], ot["count"],
                ])
    return response


def _asset_eox_by_owning_tenant_csv(data):
    response, writer = _csv_response("asset_eox_by_owning_tenant.csv")
    writer.writerow(["Owning Tenant", "Year", "Device Type", "EoX Date",
                     "Replacement Year", "Budget Year", "Count"])
    for ot in data["owning_tenants"]:
        for yg in ot["year_groups"]:
            for dt in yg["device_types"]:
                writer.writerow([
                    ot["name"], yg["year"], dt["name"], dt["eox_date"] or "-",
                    dt["replacement_year"] or "-", dt["budget_year"] or "-", dt["count"],
                ])
    return response


def _asset_eox_by_year_csv(data):
    response, writer = _csv_response("asset_eox_by_year.csv")
    writer.writerow(["Year", "Device Type", "EoX Date", "Replacement Year", "Budget Year",
                     "Owning Tenant", "Count"])
    for year_data in data["years"]:
        for dt in year_data["device_types"]:
            for ot in dt["owning_tenants"]:
                writer.writerow([
                    year_data["year"], dt["name"], dt["eox_date"] or "-",
                    dt["replacement_year"] or "-", dt["budget_year"] or "-",
                    ot["name"], ot["count"],
                ])
    return response


_ASSET_EOX_CONFIG = {
    "by_site":          ("By Site",          _build_asset_eox_by_site,          _asset_eox_by_site_csv),
    "by_device_type":   ("By Device Type",   _build_asset_eox_by_device_type,   _asset_eox_by_device_type_csv),
    "by_owning_tenant": ("By Owning Tenant", _build_asset_eox_by_owning_tenant, _asset_eox_by_owning_tenant_csv),
    "by_year":          ("By Year",          _build_asset_eox_by_year,          _asset_eox_by_year_csv),
}


class AssetEoXReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "netbox_inventory.view_asset"
    template_name = "netbox_insights/asset_eox_report.html"

    def get(self, request):
        from ..forms.reports import AssetReportFilterForm

        report_key = request.GET.get("report", "by_site")
        if report_key not in _ASSET_EOX_CONFIG:
            report_key = "by_site"

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

        label, builder, csv_func = _ASSET_EOX_CONFIG[report_key]
        data = builder(**filters) if submitted else {}

        if submitted and request.GET.get("format") == "csv":
            return csv_func(data)

        tab_urls = {}
        for key in _ASSET_EOX_CONFIG:
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
            "report_key": report_key,
            "report_label": label,
            "tab_urls": tab_urls,
            "csv_url": "?" + csv_params.urlencode(),
            "submitted": submitted,
        })


# ══════════════════════════════════════════════════════════════════════════════
# Contract Coverage Report
# ══════════════════════════════════════════════════════════════════════════════

def _coverage_qs(site_ids=None, device_type_ids=None, owning_tenant_ids=None,
                 manufacturer_ids=None, exclude_retired=True, order_by=None):
    qs = _apply_filters(
        _asset_base_qs(),
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
    )
    if order_by:
        qs = qs.order_by(*order_by)
    return qs


# ── By Site ───────────────────────────────────────────────────────────────────

def _build_asset_coverage_by_site(site_ids=None, device_type_ids=None,
                                  owning_tenant_ids=None, manufacturer_ids=None,
                                  exclude_retired=True):
    qs = _coverage_qs(
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
        order_by=["device__site__name", "installed_site_override__name", "owning_tenant__name"],
    )

    # site_pk → ot_pk → state → count
    counts: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    site_names: dict = {}
    ot_names: dict = {}

    for asset in qs.iterator():
        site = _resolve_site(asset)
        site_pk = site.pk if site else 0
        site_names[site_pk] = site.name if site else "(No Site)"

        ot_pk = asset.owning_tenant_id or 0
        ot_names[(site_pk, ot_pk)] = asset.owning_tenant.name if asset.owning_tenant else "(No Owner)"

        state = asset.support_state or "unknown"
        counts[site_pk][ot_pk][state] += 1

    sites = []
    for site_pk, ot_data in sorted(counts.items(), key=lambda x: site_names.get(x[0], "")):
        ot_list = []
        for ot_pk, state_counts in sorted(ot_data.items(), key=lambda x: ot_names.get((site_pk, x[0]), "")):
            ot_list.append({
                "pk": ot_pk or None,
                "name": ot_names.get((site_pk, ot_pk), "(No Owner)"),
                **_coverage(state_counts),
            })
        site_totals = defaultdict(int)
        for sc in ot_data.values():
            for state, n in sc.items():
                site_totals[state] += n
        sites.append({
            "pk": site_pk or None,
            "name": site_names.get(site_pk, "(No Site)"),
            "owning_tenants": ot_list,
            **_coverage(site_totals),
        })

    return {"sites": sites}


# ── Uncovered Assets ──────────────────────────────────────────────────────────

def _build_asset_uncovered(site_ids=None, device_type_ids=None,
                           owning_tenant_ids=None, manufacturer_ids=None,
                           exclude_retired=True):
    from netbox_inventory.choices import AssetSupportStateChoices, AssetSupportReasonChoices
    from netbox_inventory.models.contracts import ContractAssignment

    today = now().date()

    _state_color = {c[0]: c[2] for c in AssetSupportStateChoices.CHOICES}
    _state_label = {c[0]: str(c[1]) for c in AssetSupportStateChoices.CHOICES}
    _reason_color = {c[0]: c[2] for c in AssetSupportReasonChoices.CHOICES}
    _reason_label = {c[0]: str(c[1]) for c in AssetSupportReasonChoices.CHOICES}

    active_contract_qs = (
        ContractAssignment.objects.filter(
            asset_id=OuterRef("pk"),
            contract__contract_type__in=SUPPORT_CONTRACT_TYPES,
        )
        .annotate(_eff=Coalesce("end_date", "contract__end_date"))
        .filter(
            Q(_eff__gte=today) | (Q(end_date__isnull=True) & Q(contract__end_date__isnull=True))
        )
        .order_by(Coalesce("_eff", date.max), "pk")
    )

    qs = _apply_filters(
        _asset_base_qs()
        .exclude(support_state="covered")
        .annotate(
            _contract_type=Subquery(active_contract_qs.values("contract__contract_type")[:1]),
            _contract_end_date=Subquery(active_contract_qs.values("_eff")[:1]),
        )
        .order_by("device__site__name", "installed_site_override__name",
                  "owning_tenant__name", "device_type__manufacturer__name", "device_type__model"),
        site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
    )

    # site_pk → ot_pk → list of asset rows
    rows: dict = defaultdict(lambda: defaultdict(list))
    site_names: dict = {}
    ot_names: dict = {}

    for asset in qs.iterator():
        site = _resolve_site(asset)
        site_pk = site.pk if site else 0
        site_names[site_pk] = site.name if site else "(No Site)"

        ot_pk = asset.owning_tenant_id or 0
        ot_names[(site_pk, ot_pk)] = asset.owning_tenant.name if asset.owning_tenant else "(No Owner)"

        mfr = (asset.device_type.manufacturer.name
               if asset.device_type and asset.device_type.manufacturer else "")
        model = asset.device_type.model if asset.device_type else ""
        state = asset.support_state or "unknown"
        reason = asset.support_reason
        contract_type = asset._contract_type
        contract_type_label = {"support-ea": "EA", "support-alc": "ALC"}.get(contract_type)

        rows[site_pk][ot_pk].append({
            "pk": asset.pk,
            "serial": asset.serial or "—",
            "asset_tag": asset.asset_tag or "—",
            "device_type_pk": asset.device_type_id,
            "device_type": f"{mfr} {model}".strip() if mfr else model,
            "support_state": state,
            "support_state_color": _state_color.get(state, "secondary"),
            "support_state_display": _state_label.get(state, state.capitalize()),
            "support_reason": reason,
            "support_reason_color": _reason_color.get(reason, "secondary") if reason else None,
            "support_reason_display": _reason_label.get(reason, reason.capitalize()) if reason else "—",
            "contract_type_label": contract_type_label,
            "contract_end_date": asset._contract_end_date,
        })

    sites = []
    for site_pk in sorted(rows.keys(), key=lambda x: site_names.get(x, "")):
        ot_list = []
        for ot_pk in sorted(rows[site_pk].keys(), key=lambda x: ot_names.get((site_pk, x), "")):
            assets = rows[site_pk][ot_pk]
            ot_list.append({
                "pk": ot_pk or None,
                "name": ot_names.get((site_pk, ot_pk), "(No Owner)"),
                "assets": assets,
                "total": len(assets),
            })
        sites.append({
            "pk": site_pk or None,
            "name": site_names.get(site_pk, "(No Site)"),
            "owning_tenants": ot_list,
            "total": sum(ot["total"] for ot in ot_list),
        })

    return {"sites": sites}


# ── By Contract Year ──────────────────────────────────────────────────────────

_EA_CUTOFF_YEAR = 2031


def _build_asset_coverage_by_year(site_ids=None, device_type_ids=None,
                                  owning_tenant_ids=None, manufacturer_ids=None,
                                  exclude_retired=True):
    from netbox_inventory.models.contracts import ContractAssignment

    today = now().date()

    qs = (
        ContractAssignment.objects.filter(
            asset__device_type__isnull=False,
            contract__contract_type__in=SUPPORT_CONTRACT_TYPES,
        )
        .annotate(_eff=Coalesce("end_date", "contract__end_date"))
        .filter(_eff__isnull=False)
        .select_related("contract", "asset__owning_tenant", "asset__device_type__manufacturer")
        .order_by("_eff", "contract_id")
    )

    if exclude_retired:
        qs = qs.exclude(asset__status__in=["retired", "disposed"])
    if site_ids:
        qs = qs.filter(
            Q(asset__device__site_id__in=site_ids) |
            Q(asset__device__isnull=True, asset__installed_site_override_id__in=site_ids)
        )
    if manufacturer_ids:
        qs = qs.filter(asset__device_type__manufacturer_id__in=manufacturer_ids)
    if device_type_ids:
        qs = qs.filter(asset__device_type_id__in=device_type_ids)
    if owning_tenant_ids:
        qs = qs.filter(asset__owning_tenant_id__in=owning_tenant_ids)

    # year → contract_pk → accumulation
    contracts_by_year: dict = defaultdict(dict)
    ot_names: dict = {}

    for ca in qs.iterator():
        end_date = ca._eff
        year = end_date.year
        contract_pk = ca.contract_id or f"anon_{year}"
        contract_type = ca.contract.contract_type

        mfr = (ca.asset.device_type.manufacturer.name
               if ca.asset.device_type and ca.asset.device_type.manufacturer else "")
        is_cisco = "cisco" in mfr.lower() if mfr else False

        ot_pk = ca.asset.owning_tenant_id or 0
        ot_names[ot_pk] = ca.asset.owning_tenant.name if ca.asset.owning_tenant else "(No Owner)"

        if contract_pk not in contracts_by_year[year]:
            contracts_by_year[year][contract_pk] = {
                "contract_pk": ca.contract_id,
                "contract_id": ca.contract.contract_id or "—",
                "contract_type": contract_type,
                "contract_type_label": "EA" if contract_type == "support-ea" else "ALC",
                "end_date": end_date,
                "asset_count": 0,
                "cisco_count": 0,
                "ot_counts": defaultdict(int),
            }

        entry = contracts_by_year[year][contract_pk]
        entry["asset_count"] += 1
        if is_cisco:
            entry["cisco_count"] += 1
        entry["ot_counts"][ot_pk] += 1

    years = []
    for year in sorted(contracts_by_year.keys()):
        if year < today.year:
            year_status = "danger"
        elif year == today.year:
            year_status = "warning"
        else:
            year_status = "success"

        year_contracts = []
        alc_total = ea_total = ea_eligible_total = 0

        for contract_pk, info in sorted(
            contracts_by_year[year].items(),
            key=lambda x: (x[1]["contract_type"], x[1]["end_date"]),
        ):
            is_alc = info["contract_type"] == "support-alc"
            is_ea_eligible = is_alc and year < _EA_CUTOFF_YEAR and info["cisco_count"] > 0

            ot_rows = [
                {"pk": ot_pk or None, "name": ot_names.get(ot_pk, "(No Owner)"), "count": cnt}
                for ot_pk, cnt in sorted(info["ot_counts"].items(), key=lambda x: ot_names.get(x[0], ""))
            ]

            if is_alc:
                alc_total += info["asset_count"]
            else:
                ea_total += info["asset_count"]
            if is_ea_eligible:
                ea_eligible_total += info["cisco_count"]

            year_contracts.append({
                "contract_pk": info["contract_pk"],
                "contract_id": info["contract_id"],
                "contract_type": info["contract_type"],
                "contract_type_label": info["contract_type_label"],
                "end_date": info["end_date"],
                "asset_count": info["asset_count"],
                "cisco_count": info["cisco_count"],
                "ea_eligible": is_ea_eligible,
                "ea_eligible_count": info["cisco_count"] if is_ea_eligible else 0,
                "owning_tenants": ot_rows,
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


# ── Coverage CSV exports ──────────────────────────────────────────────────────

def _asset_coverage_by_site_csv(data):
    response, writer = _csv_response("asset_coverage_by_site.csv")
    writer.writerow(["Site", "Owning Tenant", "Total", "Covered", "Uncovered",
                     "Excluded", "Unknown", "Coverage %"])
    for site in data["sites"]:
        for ot in site["owning_tenants"]:
            writer.writerow([
                site["name"], ot["name"], ot["total"],
                ot["covered"], ot["uncovered"], ot["excluded"], ot["unknown"],
                ot["eligible_pct"] if ot["eligible_pct"] is not None else "-",
            ])
    return response


def _asset_uncovered_csv(data):
    response, writer = _csv_response("asset_uncovered.csv")
    writer.writerow(["Site", "Owning Tenant", "Serial", "Asset Tag", "Device Type",
                     "Support State", "Reason", "Contract Type", "Contract End"])
    for site in data["sites"]:
        for ot in site["owning_tenants"]:
            for a in ot["assets"]:
                writer.writerow([
                    site["name"], ot["name"],
                    a["serial"], a["asset_tag"], a["device_type"],
                    a["support_state_display"], a["support_reason_display"],
                    a["contract_type_label"] or "", a["contract_end_date"] or "",
                ])
    return response


def _asset_coverage_by_year_csv(data):
    response, writer = _csv_response("asset_coverage_by_year.csv")
    writer.writerow(["Year", "Contract ID", "Type", "End Date", "Assets",
                     "Cisco Assets", "EA Eligible", "Owning Tenant", "Count"])
    for yd in data["years"]:
        for contract in yd["contracts"]:
            for ot in contract["owning_tenants"]:
                writer.writerow([
                    yd["year"], contract["contract_id"], contract["contract_type_label"],
                    contract["end_date"], contract["asset_count"], contract["cisco_count"],
                    "Yes" if contract["ea_eligible"] else "No",
                    ot["name"], ot["count"],
                ])
    return response


_ASSET_COVERAGE_CONFIG = {
    "by_site":   ("By Site",            _build_asset_coverage_by_site,  _asset_coverage_by_site_csv),
    "uncovered": ("Uncovered Assets",   _build_asset_uncovered,         _asset_uncovered_csv),
    "by_year":   ("By Contract Year",   _build_asset_coverage_by_year,  _asset_coverage_by_year_csv),
}


class AssetContractCoverageReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "netbox_inventory.view_asset"
    template_name = "netbox_insights/asset_contract_coverage_report.html"

    def get(self, request):
        from ..forms.reports import AssetReportFilterForm

        report_key = request.GET.get("report", "by_site")
        if report_key not in _ASSET_COVERAGE_CONFIG:
            report_key = "by_site"

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

        label, builder, csv_func = _ASSET_COVERAGE_CONFIG[report_key]
        data = builder(**filters) if submitted else {}

        if submitted and request.GET.get("format") == "csv":
            return csv_func(data)

        tab_urls = {}
        for key in _ASSET_COVERAGE_CONFIG:
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
            "report_key": report_key,
            "report_label": label,
            "tab_urls": tab_urls,
            "csv_url": "?" + csv_params.urlencode(),
            "submitted": submitted,
        })

import csv
from collections import defaultdict

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Q
from django.shortcuts import render
from django.views import View

from .asset_reports import _csv_response

__all__ = ('InstalledAtMismatchReportView',)


def _mismatch_resolve_site(asset):
    """Return the asset's effective installed site, mirroring Asset.installed_site property."""
    if asset.device_id and asset.device and asset.device.site_id:
        return asset.device.site
    if asset.installed_site_override_id:
        return asset.installed_site_override
    if asset.rack_id and asset.rack and asset.rack.site_id:
        return asset.rack.site
    if asset.storage_location_id and asset.storage_location and asset.storage_location.site_id:
        return asset.storage_location.site
    return None


def _mismatch_qs(site_ids=None, manufacturer_ids=None, exclude_retired=True):
    from netbox_inventory.models import Asset

    qs = (
        Asset.objects.filter(
            installed_at__isnull=False,
            installed_at__sites__isnull=False,
        )
        .select_related(
            'installed_at__manufacturer',
            'device__site',
            'installed_site_override',
            'rack__site',
            'storage_location__site',
            'owning_tenant',
            'device_type__manufacturer',
        )
        .prefetch_related('installed_at__sites')
        .distinct()
    )

    if exclude_retired:
        qs = qs.exclude(status__in=['retired', 'disposed'])
    if site_ids:
        qs = qs.filter(
            Q(device__site_id__in=site_ids) |
            Q(device__isnull=True, installed_site_override_id__in=site_ids) |
            Q(rack__site_id__in=site_ids) |
            Q(storage_location__site_id__in=site_ids)
        )
    if manufacturer_ids:
        qs = qs.filter(installed_at__manufacturer_id__in=manufacturer_ids)

    return qs


def _is_mismatch(asset):
    site = _mismatch_resolve_site(asset)
    if site is None:
        return False
    linked_pks = {s.pk for s in asset.installed_at.sites.all()}
    return site.pk not in linked_pks


def _asset_row(asset):
    site = _mismatch_resolve_site(asset)
    mfr = ''
    model = ''
    if asset.device_type_id and asset.device_type:
        mfr = asset.device_type.manufacturer.name if asset.device_type.manufacturer else ''
        model = asset.device_type.model or ''
    return {
        'pk': asset.pk,
        'serial': asset.serial or '—',
        'asset_tag': asset.asset_tag or '—',
        'name': asset.name or '',
        'device_type': f'{mfr} {model}'.strip() if mfr else model,
        'device_type_pk': asset.device_type_id,
        'owning_tenant_pk': asset.owning_tenant_id,
        'owning_tenant_name': asset.owning_tenant.name if asset.owning_tenant else '—',
        'current_site_pk': site.pk if site else None,
        'current_site_name': site.name if site else '(No Site)',
    }


# ── By Vendor Location ────────────────────────────────────────────────────────

def _build_mismatch_by_location(site_ids=None, manufacturer_ids=None, exclude_retired=True):
    qs = _mismatch_qs(
        site_ids=site_ids, manufacturer_ids=manufacturer_ids, exclude_retired=exclude_retired
    ).order_by(
        'installed_at__manufacturer__name',
        'installed_at__vendor_site_id',
        'device__site__name',
        'installed_site_override__name',
    )

    # loc_pk → list of asset rows
    rows: dict = defaultdict(list)
    loc_info: dict = {}

    for asset in qs.iterator(chunk_size=2000):
        if not _is_mismatch(asset):
            continue
        loc_pk = asset.installed_at_id
        if loc_pk not in loc_info:
            loc = asset.installed_at
            loc_info[loc_pk] = {
                'pk': loc_pk,
                'vendor_site_id': loc.vendor_site_id,
                'manufacturer': loc.manufacturer.name,
                'full_address': loc.full_address,
                'linked_sites': [s.name for s in loc.sites.all()],
            }
        rows[loc_pk].append(_asset_row(asset))

    locations = []
    for loc_pk in sorted(rows.keys(), key=lambda x: (loc_info[x]['manufacturer'], loc_info[x]['vendor_site_id'])):
        locations.append({
            **loc_info[loc_pk],
            'assets': rows[loc_pk],
            'total': len(rows[loc_pk]),
        })

    return {'locations': locations}


# ── By NetBox Site ─────────────────────────────────────────────────────────────

def _build_mismatch_by_site(site_ids=None, manufacturer_ids=None, exclude_retired=True):
    qs = _mismatch_qs(
        site_ids=site_ids, manufacturer_ids=manufacturer_ids, exclude_retired=exclude_retired
    ).order_by(
        'device__site__name',
        'installed_site_override__name',
        'installed_at__manufacturer__name',
        'installed_at__vendor_site_id',
    )

    # site_pk → list of asset rows with vendor location info
    rows: dict = defaultdict(list)
    site_names: dict = {}

    for asset in qs.iterator(chunk_size=2000):
        if not _is_mismatch(asset):
            continue
        site = _mismatch_resolve_site(asset)
        site_pk = site.pk if site else 0
        site_names[site_pk] = site.name if site else '(No Site)'

        row = _asset_row(asset)
        loc = asset.installed_at
        row['vendor_location_pk'] = loc.pk
        row['vendor_location_str'] = str(loc)
        row['vendor_location_linked_sites'] = [s.name for s in loc.sites.all()]
        rows[site_pk].append(row)

    sites = []
    for site_pk in sorted(rows.keys(), key=lambda x: site_names.get(x, '')):
        assets = rows[site_pk]
        sites.append({
            'pk': site_pk or None,
            'name': site_names[site_pk],
            'assets': assets,
            'total': len(assets),
        })

    return {'sites': sites}


# ── CSV exports ───────────────────────────────────────────────────────────────

def _mismatch_by_location_csv(data):
    response, writer = _csv_response('installed_at_mismatch_by_location.csv')
    writer.writerow(['Manufacturer', 'Vendor Site ID', 'Vendor Address', 'Linked NetBox Sites',
                     'Serial', 'Asset Tag', 'Name', 'Device Type', 'Current Site', 'Owning Tenant'])
    for loc in data.get('locations', []):
        linked = ', '.join(loc['linked_sites']) if loc['linked_sites'] else '(none)'
        for a in loc['assets']:
            writer.writerow([
                loc['manufacturer'], loc['vendor_site_id'], loc['full_address'], linked,
                a['serial'], a['asset_tag'], a['name'], a['device_type'],
                a['current_site_name'], a['owning_tenant_name'],
            ])
    return response


def _mismatch_by_site_csv(data):
    response, writer = _csv_response('installed_at_mismatch_by_site.csv')
    writer.writerow(['Current Site', 'Serial', 'Asset Tag', 'Name', 'Device Type',
                     'Owning Tenant', 'Vendor Location', 'Vendor Linked Sites'])
    for site in data.get('sites', []):
        for a in site['assets']:
            linked = ', '.join(a['vendor_location_linked_sites']) if a['vendor_location_linked_sites'] else '(none)'
            writer.writerow([
                site['name'], a['serial'], a['asset_tag'], a['name'], a['device_type'],
                a['owning_tenant_name'], a['vendor_location_str'], linked,
            ])
    return response


_MISMATCH_CONFIG = {
    'by_location': ('By Vendor Location', _build_mismatch_by_location, _mismatch_by_location_csv),
    'by_site':     ('By NetBox Site',     _build_mismatch_by_site,     _mismatch_by_site_csv),
}


class InstalledAtMismatchReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'netbox_inventory.view_asset'
    template_name = 'netbox_insights/installed_at_mismatch_report.html'

    def get(self, request):
        from ..forms.reports import InstalledAtMismatchFilterForm

        report_key = request.GET.get('report', 'by_location')
        if report_key not in _MISMATCH_CONFIG:
            report_key = 'by_location'

        form = InstalledAtMismatchFilterForm(request.GET or None)
        submitted = 'submitted' in request.GET

        filters = {}
        if form.is_valid():
            if sites := form.cleaned_data.get('site'):
                filters['site_ids'] = [s.pk for s in sites]
            if manufacturers := form.cleaned_data.get('manufacturer'):
                filters['manufacturer_ids'] = [m.pk for m in manufacturers]
            filters['exclude_retired'] = (
                form.cleaned_data.get('exclude_retired', True) if submitted else True
            )
        else:
            filters['exclude_retired'] = True

        label, builder, csv_func = _MISMATCH_CONFIG[report_key]
        data = builder(**filters) if submitted else {}

        if submitted and request.GET.get('format') == 'csv':
            return csv_func(data)

        tab_urls = {}
        for key in _MISMATCH_CONFIG:
            params = request.GET.copy()
            params['report'] = key
            params.pop('format', None)
            tab_urls[key] = '?' + params.urlencode()

        csv_params = request.GET.copy()
        csv_params['format'] = 'csv'

        return render(request, self.template_name, {
            **data,
            'form': form,
            'exclude_retired': filters['exclude_retired'],
            'report_key': report_key,
            'report_label': label,
            'tab_urls': tab_urls,
            'csv_url': '?' + csv_params.urlencode(),
            'submitted': submitted,
        })

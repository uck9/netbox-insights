from collections import defaultdict

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import render
from django.views import View

from .asset_reports import _apply_filters, _resolve_site
from .reports import _csv_response

__all__ = ('ToBeLocatedReportView',)


def _tbl_row(asset):
    site = _resolve_site(asset)
    mfr = asset.device_type.manufacturer.name if asset.device_type and asset.device_type.manufacturer else ''
    model = asset.device_type.model if asset.device_type else ''
    device = asset.device if asset.device_id else None
    return {
        'pk': asset.pk,
        'asset_tag': asset.asset_tag or '—',
        'serial': asset.serial or '—',
        'device_type': f'{mfr} {model}'.strip() if mfr else model,
        'device_type_pk': asset.device_type_id,
        'purchase_pk': asset.purchase_id,
        'purchase_description': (
            (asset.purchase.description or asset.purchase.name) if asset.purchase_id else '—'
        ),
        'owning_tenant': asset.owning_tenant.name if asset.owning_tenant else '—',
        'storage_location': asset.storage_location.name if asset.storage_location_id else '—',
        'vendor_location': str(asset.installed_at) if asset.installed_at_id else '—',
        'device_pk': device.pk if device else None,
        'device_name': device.name if device else None,
        'vendor_ship_date': asset.vendor_ship_date,
        'site_pk': site.pk if site else None,
    }


def _build_to_be_located(site_ids=None, device_type_ids=None, owning_tenant_ids=None, manufacturer_ids=None):
    from netbox_inventory.models import Asset

    qs = (
        Asset.objects.filter(status='to-be-located')
        .select_related(
            'device__site', 'installed_site_override', 'purchase', 'owning_tenant',
            'device_type__manufacturer', 'storage_location', 'installed_at', 'device',
        )
    )
    qs = _apply_filters(
        qs, site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=False,
    )
    qs = qs.order_by('device__site__name', 'installed_site_override__name', 'asset_tag')

    rows_by_site = defaultdict(list)
    site_names = {}
    attached_count = 0

    for asset in qs.iterator(chunk_size=2000):
        site = _resolve_site(asset)
        site_pk = site.pk if site else 0
        site_names[site_pk] = site.name if site else '(No Site)'

        row = _tbl_row(asset)
        if row['device_pk']:
            attached_count += 1
        rows_by_site[site_pk].append(row)

    sites = []
    for site_pk in sorted(rows_by_site.keys(), key=lambda x: site_names.get(x, '')):
        assets = rows_by_site[site_pk]
        sites.append({
            'pk': site_pk or None,
            'name': site_names[site_pk],
            'assets': assets,
            'total': len(assets),
        })

    return {
        'sites': sites,
        'total': sum(s['total'] for s in sites),
        'attached_count': attached_count,
    }


def _to_be_located_csv(data):
    response, writer = _csv_response('assets_to_be_located.csv')
    writer.writerow(['Site', 'Asset Tag', 'Serial', 'Device Type', 'Purchase Description', 'Owning Tenant',
                     'Storage Location', 'Vendor-Recorded Location', 'Attached Device', 'Vendor Ship Date'])
    for site in data.get('sites', []):
        for a in site['assets']:
            writer.writerow([
                site['name'], a['asset_tag'], a['serial'], a['device_type'], a['purchase_description'],
                a['owning_tenant'], a['storage_location'], a['vendor_location'],
                a['device_name'] or '—', a['vendor_ship_date'] or '',
            ])
    return response


class ToBeLocatedReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'netbox_inventory.view_asset'
    template_name = 'netbox_insights/to_be_located_report.html'

    def get(self, request):
        from ..forms.reports import ToBeLocatedFilterForm

        form = ToBeLocatedFilterForm(request.GET or None)
        submitted = 'submitted' in request.GET

        filters = {}
        if form.is_valid():
            if sites := form.cleaned_data.get('site'):
                filters['site_ids'] = [s.pk for s in sites]
            if manufacturers := form.cleaned_data.get('manufacturer'):
                filters['manufacturer_ids'] = [m.pk for m in manufacturers]
            if dts := form.cleaned_data.get('device_type'):
                filters['device_type_ids'] = [dt.pk for dt in dts]
            if owning_tenants := form.cleaned_data.get('owning_tenant'):
                filters['owning_tenant_ids'] = [t.pk for t in owning_tenants]

        data = _build_to_be_located(**filters) if submitted else {}

        if submitted and request.GET.get('format') == 'csv':
            return _to_be_located_csv(data)

        csv_params = request.GET.copy()
        csv_params['format'] = 'csv'

        return render(request, self.template_name, {
            **data,
            'form': form,
            'csv_url': '?' + csv_params.urlencode(),
            'submitted': submitted,
        })

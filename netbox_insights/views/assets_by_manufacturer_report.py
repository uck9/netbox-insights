from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import render
from django.utils.timezone import now
from django.views import View

from .asset_reports import _apply_filters, _asset_base_qs, _resolve_site
from .reports import _csv_response, _eox_status

__all__ = ('AssetsByManufacturerReportView',)


def _row(asset, today):
    site = _resolve_site(asset)
    device = asset.device if asset.device_id else None
    decom_date = asset.planned_decommission_date
    return {
        'pk': asset.pk,
        'asset_tag': asset.asset_tag or '—',
        'serial': asset.serial or '—',
        'owning_tenant': asset.owning_tenant.name if asset.owning_tenant else '—',
        'site': site.name if site else '(No Site)',
        'site_pk': site.pk if site else None,
        'device_pk': device.pk if device else None,
        'device_name': device.name if device else None,
        'status_display': asset.get_status_display(),
        'status_color': asset.get_status_color(),
        'planned_decommission_date': decom_date,
        'decom_past_due': bool(decom_date and decom_date < today),
    }


def _build_assets_by_manufacturer(site_ids=None, device_type_ids=None, owning_tenant_ids=None,
                                  manufacturer_ids=None, exclude_retired=True):
    today = now().date()

    qs = _asset_base_qs().select_related('device')
    qs = _apply_filters(
        qs, site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
    )
    qs = qs.order_by('device_type__manufacturer__name', 'device_type__model', 'asset_tag')

    manufacturers = []
    mfr = model = None
    for asset in qs.iterator(chunk_size=2000):
        dt = asset.device_type
        if mfr is None or mfr['pk'] != dt.manufacturer_id:
            mfr = {'pk': dt.manufacturer_id, 'name': dt.manufacturer.name, 'models': [], 'total': 0,
                   'decom_count': 0}
            manufacturers.append(mfr)
            model = None
        if model is None or model['pk'] != dt.pk:
            eox_date = asset.tracked_eox_date
            model = {
                'pk': dt.pk,
                'name': dt.model,
                'eox_date': eox_date,
                'eox_status': _eox_status(eox_date, today) if eox_date else 'secondary',
                'assets': [],
                'decom_count': 0,
            }
            mfr['models'].append(model)

        row = _row(asset, today)
        model['assets'].append(row)
        mfr['total'] += 1
        if row['planned_decommission_date']:
            model['decom_count'] += 1
            mfr['decom_count'] += 1

    for mfr in manufacturers:
        for model in mfr['models']:
            model['total'] = len(model['assets'])

    return {
        'manufacturers': manufacturers,
        'total': sum(m['total'] for m in manufacturers),
        'decom_count': sum(m['decom_count'] for m in manufacturers),
    }


def _assets_by_manufacturer_csv(data):
    response, writer = _csv_response('assets_by_manufacturer.csv')
    writer.writerow(['Manufacturer', 'Model', 'EoX Date', 'Asset Tag', 'Serial', 'Site',
                     'Owning Tenant', 'Status', 'Attached Device', 'Planned Decommission Date'])
    for mfr in data.get('manufacturers', []):
        for model in mfr['models']:
            for a in model['assets']:
                writer.writerow([
                    mfr['name'], model['name'], model['eox_date'] or '',
                    a['asset_tag'], a['serial'], a['site'], a['owning_tenant'],
                    a['status_display'], a['device_name'] or '—',
                    a['planned_decommission_date'] or '',
                ])
    return response


class AssetsByManufacturerReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'netbox_inventory.view_asset'
    template_name = 'netbox_insights/assets_by_manufacturer_report.html'

    def get(self, request):
        from ..forms.reports import AssetReportFilterForm

        form = AssetReportFilterForm(request.GET or None)
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
            filters['exclude_retired'] = (
                form.cleaned_data.get('exclude_retired', True) if submitted else True
            )
        else:
            filters['exclude_retired'] = True

        data = _build_assets_by_manufacturer(**filters) if submitted else {}

        if submitted and request.GET.get('format') == 'csv':
            return _assets_by_manufacturer_csv(data)

        csv_params = request.GET.copy()
        csv_params['format'] = 'csv'

        return render(request, self.template_name, {
            **data,
            'form': form,
            'exclude_retired': filters['exclude_retired'],
            'csv_url': '?' + csv_params.urlencode(),
            'submitted': submitted,
        })

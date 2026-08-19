from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import render
from django.utils.timezone import now
from django.views import View

from .asset_reports import _apply_filters, _resolve_site
from .reports import _csv_response

__all__ = ('PlannedDecommissionReportView',)


def _row(asset, today):
    site = _resolve_site(asset)
    mfr = asset.device_type.manufacturer.name if asset.device_type and asset.device_type.manufacturer else ''
    model = asset.device_type.model if asset.device_type else ''
    device = asset.device if asset.device_id else None
    decom_date = asset.planned_decommission_date
    return {
        'pk': asset.pk,
        'asset_tag': asset.asset_tag or '—',
        'serial': asset.serial or '—',
        'device_type': f'{mfr} {model}'.strip() if mfr else model,
        'device_type_pk': asset.device_type_id,
        'owning_tenant': asset.owning_tenant.name if asset.owning_tenant else '—',
        'owning_tenant_pk': asset.owning_tenant_id,
        'site': site.name if site else '(No Site)',
        'site_pk': site.pk if site else None,
        'device_pk': device.pk if device else None,
        'device_name': device.name if device else None,
        'status_display': asset.get_status_display(),
        'status_color': asset.get_status_color(),
        'planned_decommission_date': decom_date,
        'is_past_due': decom_date < today,
    }


def _build_planned_decommission(site_ids=None, device_type_ids=None, owning_tenant_ids=None,
                                 manufacturer_ids=None, exclude_retired=True):
    from netbox_inventory.models import Asset

    today = now().date()

    qs = (
        Asset.objects.filter(planned_decommission_date__isnull=False)
        .select_related(
            'device__site', 'installed_site_override', 'owning_tenant',
            'device_type__manufacturer', 'device',
        )
    )
    qs = _apply_filters(
        qs, site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
    )
    qs = qs.order_by('planned_decommission_date', 'asset_tag')

    assets = [_row(asset, today) for asset in qs.iterator(chunk_size=2000)]
    past_due_count = sum(1 for a in assets if a['is_past_due'])

    return {
        'assets': assets,
        'total': len(assets),
        'past_due_count': past_due_count,
    }


def _planned_decommission_csv(data):
    response, writer = _csv_response('assets_planned_decommission.csv')
    writer.writerow(['Planned Decommission Date', 'Past Due', 'Site', 'Asset Tag', 'Serial',
                     'Device Type', 'Owning Tenant', 'Status', 'Attached Device'])
    for a in data.get('assets', []):
        writer.writerow([
            a['planned_decommission_date'], 'Yes' if a['is_past_due'] else 'No',
            a['site'], a['asset_tag'], a['serial'], a['device_type'], a['owning_tenant'],
            a['status_display'], a['device_name'] or '—',
        ])
    return response


class PlannedDecommissionReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'netbox_inventory.view_asset'
    template_name = 'netbox_insights/planned_decommission_report.html'

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

        data = _build_planned_decommission(**filters) if submitted else {}

        if submitted and request.GET.get('format') == 'csv':
            return _planned_decommission_csv(data)

        csv_params = request.GET.copy()
        csv_params['format'] = 'csv'

        return render(request, self.template_name, {
            **data,
            'form': form,
            'exclude_retired': filters['exclude_retired'],
            'csv_url': '?' + csv_params.urlencode(),
            'submitted': submitted,
        })

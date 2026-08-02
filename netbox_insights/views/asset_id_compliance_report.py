import re

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import render
from django.views import View

from .asset_reports import _apply_filters, _resolve_site
from .reports import _coverage_status, _csv_response

__all__ = ('AssetIdComplianceReportView',)

# Standard convention: W followed by 7 digits, e.g. W1234567.
_STANDARD_TAG_RE = re.compile(r'^W\d{7}$')

# Legacy-imported tag prefixes that are also treated as compliant.
_LEGACY_PREFIXES = ('AL', 'PET', 'AI')

_CATEGORY_LABELS = {
    'pending_followup': 'Pending Follow-up (W-)',
    'non_standard': 'Non-Standard',
}
_CATEGORY_COLORS = {
    'pending_followup': 'warning',
    'non_standard': 'danger',
}


def _classify_asset_tag(tag):
    """Classify an asset_tag against the site's Asset ID conventions.

    - 'no_id' / 'vm_placeholder' are out of scope entirely (not counted toward compliance).
    - 'compliant_standard' / 'compliant_legacy' count as compliant.
    - 'pending_followup' / 'non_standard' count as non-compliant and are listed for follow-up.
    """
    tag = (tag or '').strip()
    if not tag:
        return 'no_id'
    if tag.startswith('VM-'):
        return 'vm_placeholder'
    if _STANDARD_TAG_RE.match(tag):
        return 'compliant_standard'
    if tag.upper().startswith(_LEGACY_PREFIXES):
        return 'compliant_legacy'
    if tag.startswith('W-'):
        return 'pending_followup'
    return 'non_standard'


def _purchase_description(purchase):
    return purchase.description or purchase.name


def _compliance_row(asset, category):
    site = _resolve_site(asset)
    device = asset.device if asset.device_id else None
    mfr = asset.device_type.manufacturer.name if asset.device_type and asset.device_type.manufacturer else ''
    model = asset.device_type.model if asset.device_type else ''
    return {
        'pk': asset.pk,
        'asset_tag': asset.asset_tag or '—',
        'category': category,
        'category_label': _CATEGORY_LABELS[category],
        'category_color': _CATEGORY_COLORS[category],
        'status_display': asset.get_status_display(),
        'status_color': asset.get_status_color(),
        'serial': asset.serial or '—',
        'device_type_pk': asset.device_type_id,
        'device_type': (f'{mfr} {model}'.strip() if mfr else model) or '—',
        'purchase_pk': asset.purchase_id,
        'purchase_description': _purchase_description(asset.purchase) if asset.purchase_id else '—',
        'site_pk': site.pk if site else None,
        'site_name': site.name if site else '—',
        'device_pk': device.pk if device else None,
        'device_name': device.name if device else None,
        'device_active': bool(device and device.status == 'active'),
        'device_status_display': device.get_status_display() if device else None,
        'device_status_color': device.get_status_color() if device else None,
    }


def _build_asset_id_compliance(site_ids=None, device_type_ids=None, owning_tenant_ids=None,
                                manufacturer_ids=None, exclude_retired=True):
    from netbox_inventory.models import Asset

    qs = Asset.objects.filter(module_type__isnull=True).select_related(
        'device__site', 'installed_site_override', 'purchase',
        'owning_tenant', 'device_type__manufacturer', 'device',
    )
    qs = _apply_filters(
        qs, site_ids=site_ids, device_type_ids=device_type_ids,
        owning_tenant_ids=owning_tenant_ids, manufacturer_ids=manufacturer_ids,
        exclude_retired=exclude_retired,
    )

    counts = {
        'compliant_standard': 0, 'compliant_legacy': 0,
        'pending_followup': 0, 'non_standard': 0,
        'vm_placeholder': 0, 'no_id': 0,
    }
    rows = []

    for asset in qs.iterator(chunk_size=2000):
        category = _classify_asset_tag(asset.asset_tag)
        counts[category] += 1
        if category in ('pending_followup', 'non_standard'):
            rows.append(_compliance_row(asset, category))

    compliant = counts['compliant_standard'] + counts['compliant_legacy']
    non_compliant = counts['pending_followup'] + counts['non_standard']
    in_scope = compliant + non_compliant
    compliance_pct, compliance_status = _coverage_status(compliant, in_scope)

    rows.sort(key=lambda r: (r['category'] != 'pending_followup', r['site_name'], r['asset_tag']))

    return {
        'counts': counts,
        'compliant': compliant,
        'non_compliant': non_compliant,
        'in_scope': in_scope,
        'compliance_pct': compliance_pct,
        'compliance_status': compliance_status,
        'excluded_vm': counts['vm_placeholder'],
        'excluded_no_id': counts['no_id'],
        'rows': rows,
    }


def _asset_id_compliance_csv(data):
    response, writer = _csv_response('asset_id_compliance.csv')
    writer.writerow(['Asset Tag', 'Category', 'Status', 'Device Type', 'Purchase Description', 'Site', 'Serial',
                     'Attached Device', 'Device Active'])
    for r in data.get('rows', []):
        writer.writerow([
            r['asset_tag'], r['category_label'], r['status_display'], r['device_type'],
            r['purchase_description'], r['site_name'], r['serial'],
            r['device_name'] or '—', 'Yes' if r['device_active'] else 'No',
        ])
    return response


class AssetIdComplianceReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'netbox_inventory.view_asset'
    template_name = 'netbox_insights/asset_id_compliance_report.html'

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

        data = _build_asset_id_compliance(**filters) if submitted else {}

        if submitted and request.GET.get('format') == 'csv':
            return _asset_id_compliance_csv(data)

        csv_params = request.GET.copy()
        csv_params['format'] = 'csv'

        return render(request, self.template_name, {
            **data,
            'form': form,
            'exclude_retired': filters['exclude_retired'],
            'csv_url': '?' + csv_params.urlencode(),
            'submitted': submitted,
        })

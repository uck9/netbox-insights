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


# ── Move Request ─────────────────────────────────────────────────────────────
#
# Lets an operator pick a vendor "from" address and a vendor "to" address, choose
# which of the devices currently recorded at the "from" address should move, and
# generate a plain-text block to paste into an email to the vendor (Cisco).
# Nothing is written back to NetBox or the vendor — it is a request generator.

def _move_candidate_rows(from_location):
    from netbox_inventory.models import Asset

    qs = (
        Asset.objects.filter(installed_at=from_location)
        .exclude(status__in=['retired', 'disposed'])
        .select_related(
            'device__site', 'device_type__manufacturer', 'installed_site_override',
            'rack__site', 'storage_location__site',
        )
        .order_by('device_type__part_number', 'serial')
    )

    rows = []
    for asset in qs.iterator(chunk_size=2000):
        site = _mismatch_resolve_site(asset)
        pid = ''
        if asset.device_type_id and asset.device_type:
            pid = asset.device_type.part_number or asset.device_type.model or ''
        name = asset.name or ''
        if not name and asset.device_id and asset.device:
            name = asset.device.name or ''
        rows.append({
            'pk': asset.pk,
            'serial': asset.serial or '',
            'product_id': pid,
            'name': name,
            'vendor_instance_id': asset.vendor_instance_id or '',
            'asset_tag': asset.asset_tag or '',
            'current_site_pk': site.pk if site else None,
            'current_site_name': site.name if site else '',
        })
    return rows


def _address_block(loc):
    lines = ['Site ID:  {}'.format(loc.vendor_site_id)]
    if loc.customer_name:
        lines.append(loc.customer_name)
    if loc.address:
        lines.append(loc.address)
    city_line = loc.city or ''
    if loc.state:
        city_line = '{}, {}'.format(city_line, loc.state) if city_line else loc.state
    if loc.postcode:
        city_line = '{} {}'.format(city_line, loc.postcode).strip()
    if city_line.strip(', '):
        lines.append(city_line)
    if loc.country:
        lines.append(loc.country)
    return '\n'.join(line for line in lines if line and line.strip())


def _indent(text, prefix='  '):
    return '\n'.join(prefix + line for line in text.split('\n'))


def _build_move_email(from_location, to_location, rows):
    n = len(rows)
    plural = 's' if n != 1 else ''

    idx_w = max(len('#'), len(str(n)))
    serial_w = max([len('Serial')] + [len(r['serial']) for r in rows])
    pid_w = max([len('Product ID')] + [len(r['product_id']) for r in rows])
    name_w = max([len('Device Name')] + [len(r['name']) for r in rows])
    vid_w = max([len('Vendor Instance ID')] + [len(r['vendor_instance_id']) for r in rows])

    header = '{:>{iw}}  {:<{sw}}  {:<{pw}}  {:<{nw}}  {:<{vw}}'.format(
        '#', 'Serial', 'Product ID', 'Device Name', 'Vendor Instance ID',
        iw=idx_w, sw=serial_w, pw=pid_w, nw=name_w, vw=vid_w,
    )
    sep = '-' * len(header)
    body = '\n'.join(
        '{:>{iw}}  {:<{sw}}  {:<{pw}}  {:<{nw}}  {:<{vw}}'.format(
            i, r['serial'], r['product_id'], r['name'], r['vendor_instance_id'],
            iw=idx_w, sw=serial_w, pw=pid_w, nw=name_w, vw=vid_w,
        )
        for i, r in enumerate(rows, 1)
    )

    return (
        'Subject: Installed Base Location Update Request - {n} device{p}\n\n'
        'Hello Cisco Team,\n\n'
        'Please update the installed-at location on the following device{p} in our\n'
        'installed base, moving {them} from the first address to the second.\n\n'
        'MOVE FROM\n{from_block}\n\n'
        'MOVE TO\n{to_block}\n\n'
        'DEVICES ({n})\n{header}\n{sep}\n{body}\n\n'
        'Please confirm once these records have been updated.\n\n'
        'Thank you.\n'
    ).format(
        n=n,
        p=plural,
        them='it' if n == 1 else 'them',
        from_block=_indent(_address_block(from_location)),
        to_block=_indent(_address_block(to_location)),
        header=_indent(header),
        sep=_indent(sep),
        body=_indent(body),
    )


def _move_request_csv(from_location, to_location, rows):
    response, writer = _csv_response('installed_at_move_request.csv')
    writer.writerow([
        'Serial', 'Product ID', 'Device Name', 'Vendor Instance ID', 'Current Site',
        'Move From Site ID', 'Move From Address', 'Move To Site ID', 'Move To Address',
    ])
    for r in rows:
        writer.writerow([
            r['serial'], r['product_id'], r['name'], r['vendor_instance_id'], r['current_site_name'],
            from_location.vendor_site_id, from_location.full_address,
            to_location.vendor_site_id, to_location.full_address,
        ])
    return response


class InstalledAtMismatchReportView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = 'netbox_inventory.view_asset'
    template_name = 'netbox_insights/installed_at_mismatch_report.html'

    def get(self, request):
        from ..forms.reports import InstalledAtMismatchFilterForm

        report_key = request.GET.get('report', 'by_location')
        if report_key not in _MISMATCH_CONFIG and report_key != 'move_request':
            report_key = 'by_location'

        tab_urls = {}
        for key in list(_MISMATCH_CONFIG) + ['move_request']:
            params = request.GET.copy()
            params['report'] = key
            params.pop('format', None)
            tab_urls[key] = '?' + params.urlencode()

        if report_key == 'move_request':
            return self._move_request(request, tab_urls)

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

    def _move_request(self, request, tab_urls):
        from ..forms.reports import InstalledAtMoveRequestForm

        form = InstalledAtMoveRequestForm(request.GET or None)
        from_location = to_location = None
        if form.is_valid():
            from_location = form.cleaned_data.get('from_location')
            to_location = form.cleaned_data.get('to_location')

        rows = _move_candidate_rows(from_location) if from_location else []
        selected_pks = set(request.GET.getlist('asset'))
        generate_clicked = 'generate' in request.GET

        if request.GET.get('format') == 'csv' and from_location and to_location:
            csv_rows = [r for r in rows if str(r['pk']) in selected_pks] if selected_pks else rows
            return _move_request_csv(from_location, to_location, csv_rows)

        selected_rows = [r for r in rows if str(r['pk']) in selected_pks]
        email_text = None
        if generate_clicked and from_location and to_location and selected_rows:
            email_text = _build_move_email(from_location, to_location, selected_rows)

        # Default every checkbox to ticked until the operator has made a choice
        preselect_all = not generate_clicked
        for r in rows:
            r['checked'] = preselect_all or (str(r['pk']) in selected_pks)

        csv_params = request.GET.copy()
        csv_params['format'] = 'csv'

        return render(request, self.template_name, {
            'move_form': form,
            'report_key': 'move_request',
            'report_label': 'Move Request',
            'tab_urls': tab_urls,
            'from_location': from_location,
            'to_location': to_location,
            'move_rows': rows,
            'selected_count': len(selected_rows),
            'email_text': email_text,
            'generate_clicked': generate_clicked,
            'submitted': bool(from_location),
            'csv_url': '?' + csv_params.urlencode(),
        })

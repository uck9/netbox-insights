import django_tables2 as tables

from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.urls import reverse
from dcim.models import Device
from netbox.tables import NetBoxTable, columns
from tenancy.models import Tenant


__all__ = (
    'DeviceInsightsTable',
)

CONTRACT_TYPE_LABELS = {
    "support-ea": "EA",
    "support-alc": "ALC",
}

class DeviceInsightsTable(NetBoxTable):
    actions = None
    name = tables.Column(
        linkify=True,
        verbose_name=_('Name')
    )
    device_type = tables.Column(
        accessor='device_type',
        linkify=True,
        verbose_name='Device Type'
    )
    status = columns.ChoiceFieldColumn(
        verbose_name=_('Status'),
    )
    serial = tables.Column(
        verbose_name=_("Serial Number")
    )
    manufacturer = tables.Column(
        accessor='device_type.manufacturer',
        linkify=True,
        verbose_name=_('Manufacturer')
    )
    tenant_group = tables.Column(
        accessor='tenant.group', 
        verbose_name='Tenant Group'
    )
    tenant = tables.Column(
        linkify=True
    )
    tracked_eox_date = tables.TemplateColumn(
        template_code="""
        {% load insights_filters %}
        <span {{ record.tracked_eox_date|date_badge_class }}>{{ record.tracked_eox_date }}</span>
        """,
        verbose_name="Tracked EoX Date"
    )
    hw_end_of_security = tables.TemplateColumn(
        template_code="""
        {% load insights_filters %}
        <span {{ record.hw_end_of_security|date_badge_class }}>{{ record.hw_end_of_security }}</span>
        """,
        verbose_name="HW End of Security"
    )
    hw_end_of_support = tables.TemplateColumn(
        template_code="""
        {% load insights_filters %}
        <span {{ record.hw_end_of_support|date_badge_class }}>{{ record.hw_end_of_support }}</span>
        """,
        verbose_name="HW End of Support"
    )

    support_contract_type = tables.Column(verbose_name="Support Contract Type")
    support_contract_id = tables.Column(
        verbose_name="Support Contract ID",
    )
    support_contract_end_date = tables.TemplateColumn(
        template_code="""
        {% load insights_filters %}
        <span {{ record.support_contract_end_date|date_badge_class }}>{{ record.support_contract_end_date }}</span>
        """,
        verbose_name="Support Contract End"
    )
    support_contract_sku = tables.Column(
        accessor="support_contract_sku_display",
        verbose_name="Support Contract SKU",
    )
    tracked_eox_basis = tables.Column(
        verbose_name=_("Tracked EoX Basis")
    )

    class Meta(NetBoxTable.Meta):
        model = Device
        fields = (
            'name',
            'site',
            'role',
            'device_type',
            'status',
            'tenant_group',
            'tenant',
            'manufacturer',
            'serial',
            'tracked_eox_date',
            'tracked_eox_basis',
            'hw_end_of_security',
            'hw_end_of_support',
            'support_contract_type',
            'support_contract_id',
            'support_contract_end_date',
            'support_contract_sku',
        )
        default_columns = (
            'name', 'site', 'status', 'manufacturer', 'device_type',
            'tracked_eox_date', 'support_contract_id', 
            'support_contract_sku', 'support_contract_end_date'
        )

    def render_support_contract_type(self, value):
        if not value:
            return ""
        return CONTRACT_TYPE_LABELS.get(value, value)
    
    def render_support_contract_id(self, value, record):
        if not value:
            return ""

        # If you annotated contract PK too, use that (best)
        contract_pk = getattr(record, "support_contract_pk", None)
        if contract_pk:
            url = reverse(
                "plugins:netbox_inventory:contract",
                kwargs={"pk": contract_pk},
            )
            return format_html('<a href="{}">{}</a>', url, value)

        # Fallback: no PK available
        return value

    def render_tracked_eox_basis(self, value):
        if not value:
            return ""
        return str(value).capitalize()

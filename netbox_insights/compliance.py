"""
Mid-migration bridge between netbox_compliance and the legacy device custom
fields it's replacing. Configured per-measure in PLUGINS_CONFIG so a device
can be surfaced from whichever source actually has data for it:

    'netbox_insights': {
        'compliance_measures': [
            {
                'measure': 'firmware-version',        # ComplianceMeasure.slug
                'fallback_cf': 'sw_status',            # legacy CF holding the value_map key
                'fallback_detail_cfs': {'running': 'sw_version'},  # optional
            },
        ],
    },

Once a measure has real ComplianceResult coverage for every device that
matters, drop its `fallback_cf` entry (or the whole config) -- the legacy CF
is never read again after that.
"""
from django.conf import settings

from netbox_compliance.choices import EffectiveStatusChoices
from netbox_compliance.models import ComplianceMeasure
from netbox_compliance.services import enum_credit_status, get_effective_measures

__all__ = ("build_compliance_map",)


def _configured_measures():
    return (
        getattr(settings, "PLUGINS_CONFIG", {})
        .get("netbox_insights", {})
        .get("compliance_measures", [])
    )


def _flatten_effective(effective):
    """Slug -> EffectiveMeasure row, across packages and direct assignments."""
    rows = [row for rows in effective["packages"].values() for row in rows]
    rows += effective["direct"]
    return {row.measure.slug: row for row in rows}


def _from_compliance_row(row):
    return {
        "value": row.value,
        "display_label": row.display_label,
        "display_color": row.display_color,
        "credit": row.credit,
        "status": row.status,
        "details": row.result.details if row.result else {},
        "source": "compliance",
    }


def _from_fallback_cf(device, measure, entry):
    raw_value = device.custom_field_data.get(entry.get("fallback_cf"))
    if raw_value is None:
        return {
            "value": None,
            "display_label": "—",
            "display_color": "grey",
            "credit": 0,
            "status": EffectiveStatusChoices.PENDING,
            "details": {},
            "source": "custom_field",
        }

    value_map_entry = measure.value_map.get(raw_value, {})
    details = {
        key: device.custom_field_data.get(cf_name)
        for key, cf_name in entry.get("fallback_detail_cfs", {}).items()
    }
    return {
        "value": raw_value,
        "display_label": value_map_entry.get("label", raw_value),
        "display_color": value_map_entry.get("color", "grey"),
        "credit": int(value_map_entry.get("credit", 0)),
        "status": enum_credit_status(value_map_entry) if value_map_entry else EffectiveStatusChoices.ERROR,
        "details": details,
        "source": "custom_field",
    }


def build_compliance_map(devices):
    """
    Batch-resolve the configured compliance measures for a page of Device
    instances. Returns {device.pk: {measure_slug: field_dict}}.

    For each configured measure, a device uses its real ComplianceResult if
    one has ever been posted (status != pending); otherwise it falls back to
    the legacy custom field named in `fallback_cf`, reinterpreted through the
    same measure's value_map so the shape of the two sources is identical.
    Devices are still resolved one at a time against netbox_compliance (no
    bulk API there yet) -- fine at typical page sizes, but scales linearly
    with page size if that ever changes.
    """
    devices = list(devices)
    configured = _configured_measures()
    if not devices or not configured:
        return {}

    measures_by_slug = {
        m.slug: m for m in ComplianceMeasure.objects.filter(slug__in=[e["measure"] for e in configured])
    }

    result = {}
    for device in devices:
        effective_by_slug = _flatten_effective(get_effective_measures(device))
        fields = {}
        for entry in configured:
            slug = entry["measure"]
            measure = measures_by_slug.get(slug)
            if measure is None:
                continue
            row = effective_by_slug.get(slug)
            if row is not None and row.status != EffectiveStatusChoices.PENDING:
                fields[slug] = _from_compliance_row(row)
            else:
                fields[slug] = _from_fallback_cf(device, measure, entry)
        result[device.pk] = fields

    return result

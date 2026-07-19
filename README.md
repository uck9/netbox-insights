# NetBox Insights Plugin

NetBox plugin for Insights.


* Free software: MIT
* Documentation: https://uck9.github.io/netbox-insights/


## Features

This plugin has been written to expose centralised data insights from NetBox.  It's a partner plugin for other plugins.

Other Plugins Requires
* Netbox-Inventory-Lifecycle

## Compatibility

| NetBox Version | Plugin Version |
|----------------|----------------|
|     4.5.*      |      0.1.0     |

## Installing

For adding to a NetBox Docker setup see
[the general instructions for using netbox-docker with plugins](https://github.com/netbox-community/netbox-docker/wiki/Using-Netbox-Plugins).

While this is still in development and not yet on pypi you can install with pip:

```bash
pip install git+https://github.com/uck9/netbox-insights
```

or by adding to your `local_requirements.txt` or `plugin_requirements.txt` (netbox-docker):

```bash
git+https://github.com/uck9/netbox-insights
```

Enable the plugin in `/opt/netbox/netbox/netbox/configuration.py`,
 or if you use netbox-docker, your `/configuration/plugins.py` file :

```python
PLUGINS = [
    'netbox-insights'
]

PLUGINS_CONFIG = {
    "netbox-insights": {},
}
```

## Configuration

`PLUGINS_CONFIG["netbox_insights"]` supports:

- **`device_cf_whitelist`** (list of custom field names): whitelists device custom fields to surface under the `custom_fields` key of the device insights API/table, e.g. `['backup_system', 'backup_notes']`. Callers can also override the set per-request with a `?cf=name1,name2` query param.

- **`compliance_measures`** (list of objects): surfaces `netbox_compliance` measures under the `compliance_fields` key of the device insights API, with a built-in fallback to a legacy custom field for devices that haven't been migrated onto the compliance plugin yet. Each entry:

  ```python
  {
      'measure': 'firmware-version',       # ComplianceMeasure.slug to surface
      'fallback_cf': 'sw_status',          # legacy CF holding the value_map key, read only
                                            # when the device has no assigned/posted result yet
      'fallback_detail_cfs': {'running': 'sw_version'},  # optional, maps value_map "details"
                                                            # keys to legacy CFs
  }
  ```

  For a given device, the measure's real `ComplianceResult` is used once the device is assigned the measure (via `PackageAssignment`/`MeasureAssignment`) and has a posted result; until then, `fallback_cf` (and `fallback_detail_cfs`) are read instead and reinterpreted through the same measure's `value_map`, so the shape of the response is identical regardless of source. Each entry in the response includes `"source": "compliance"` or `"source": "custom_field"` so migration progress can be tracked. Once a measure has full compliance coverage, drop its `fallback_cf` (or the whole entry) — the legacy custom field is never read again after that.

## Credits

Based on the NetBox plugin tutorial:

- [demo repository](https://github.com/netbox-community/netbox-plugin-demo)
- [tutorial](https://github.com/netbox-community/netbox-plugin-tutorial)

This package was created with [Cookiecutter](https://github.com/audreyr/cookiecutter) and the [`netbox-community/cookiecutter-netbox-plugin`](https://github.com/netbox-community/cookiecutter-netbox-plugin) project template.

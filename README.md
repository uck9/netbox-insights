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

## Credits

Based on the NetBox plugin tutorial:

- [demo repository](https://github.com/netbox-community/netbox-plugin-demo)
- [tutorial](https://github.com/netbox-community/netbox-plugin-tutorial)

This package was created with [Cookiecutter](https://github.com/audreyr/cookiecutter) and the [`netbox-community/cookiecutter-netbox-plugin`](https://github.com/netbox-community/cookiecutter-netbox-plugin) project template.

"""Top-level package for NetBox Insights Plugin."""

__author__ = """Nate Reeves"""
__email__ = "nathan.a.reeves@gmail.com"
__version__ = "0.1.0"


from netbox.plugins import PluginConfig


class InsightsConfig(PluginConfig):
    name = "netbox_insights"
    verbose_name = "NetBox Insights"
    description = "NetBox plugin for Insights."
    version = "version"
    base_url = "insights"


config = InsightsConfig

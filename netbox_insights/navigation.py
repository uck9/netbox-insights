from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem


menu = PluginMenu(
    label="Insights",
    groups=[
        ("Inventory", (
            PluginMenuItem(
                link="plugins:netbox_insights:deviceinsight_list",
                link_text="Device Insights",
            ),
        )),
        ("EoX Reports", (
            PluginMenuItem(
                link="plugins:netbox_insights:eox_summary_report",
                link_text="By Site",
            ),
            PluginMenuItem(
                link="plugins:netbox_insights:eox_by_device_type_report",
                link_text="By Device Type",
            ),
            PluginMenuItem(
                link="plugins:netbox_insights:eox_by_tenant_report",
                link_text="By Tenant",
            ),
            PluginMenuItem(
                link="plugins:netbox_insights:eox_by_year_report",
                link_text="By Year",
            ),
        )),
    ],
    icon_class="mdi mdi-chart-box",
)

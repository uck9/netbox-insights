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
        ("Reports", (
            PluginMenuItem(
                link="plugins:netbox_insights:eox_report",
                link_text="EoX Reports",
            ),
            PluginMenuItem(
                link="plugins:netbox_insights:contract_coverage_report",
                link_text="Contract Coverage",
            ),
        )),
    ],
    icon_class="mdi mdi-chart-box",
)

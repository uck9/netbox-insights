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
                link="plugins:netbox_insights:eox_report",
                link_text="EoX Reports",
            ),
        )),
    ],
    icon_class="mdi mdi-chart-box",
)

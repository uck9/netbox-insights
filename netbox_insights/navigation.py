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
                link_text="Device EoX Reports",
            ),
            PluginMenuItem(
                link="plugins:netbox_insights:contract_coverage_report",
                link_text="Device Contract Coverage",
            ),
            PluginMenuItem(
                link="plugins:netbox_insights:asset_eox_report",
                link_text="Asset EoX Reports",
            ),
            PluginMenuItem(
                link="plugins:netbox_insights:asset_contract_coverage_report",
                link_text="Asset Contract Coverage",
            ),
        )),
    ],
    icon_class="mdi mdi-chart-box",
)

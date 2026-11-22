from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem


VIEW_INSIGHTS = ["netbox_insights.view_insights"]


menu = PluginMenu(
    label="Insights",
    groups=[
        ("Inventory", (
            PluginMenuItem(
                link="plugins:netbox_insights:deviceinsight_list",
                link_text="Device Insights",
                permissions=VIEW_INSIGHTS,
            ),
        )),
        ("Lifecycle", (
            PluginMenuItem(
                link="plugins:netbox_insights:eox_report",
                link_text="Device EoX Reports",
                permissions=VIEW_INSIGHTS,
            ),
            PluginMenuItem(
                link="plugins:netbox_insights:asset_eox_report",
                link_text="Asset EoX Reports",
                permissions=VIEW_INSIGHTS,
            ),
        )),
        ("Budgeting", (
            PluginMenuItem(
                link="plugins:netbox_insights:license_budget_report",
                link_text="License Renewal Budget",
                permissions=VIEW_INSIGHTS,
            ),
            PluginMenuItem(
                link="plugins:netbox_insights:hardware_budget_report",
                link_text="Hardware Replacement Budget",
                permissions=VIEW_INSIGHTS,
            ),
        )),
        ("Support Contracts", (
            PluginMenuItem(
                link="plugins:netbox_insights:contract_coverage_report",
                link_text="Device Contract Coverage",
                permissions=VIEW_INSIGHTS,
            ),
            PluginMenuItem(
                link="plugins:netbox_insights:asset_contract_coverage_report",
                link_text="Asset Contract Coverage",
                permissions=VIEW_INSIGHTS,
            ),
            PluginMenuItem(
                link="plugins:netbox_insights:installed_at_mismatch_report",
                link_text="Installed-At Location",
                permissions=VIEW_INSIGHTS,
            ),
        )),
        ("Reports", (
            PluginMenuItem(
                link="plugins:netbox_insights:data_validation_report",
                link_text="Data Validation Report",
                permissions=VIEW_INSIGHTS,
            ),
            PluginMenuItem(
                link="plugins:netbox_insights:asset_id_compliance_report",
                link_text="Asset ID Compliance",
                permissions=VIEW_INSIGHTS,
            ),
            PluginMenuItem(
                link="plugins:netbox_insights:to_be_located_report",
                link_text="To Be Located Assets",
                permissions=VIEW_INSIGHTS,
            ),
            PluginMenuItem(
                link="plugins:netbox_insights:planned_decommission_report",
                link_text="Planned Decommission",
                permissions=VIEW_INSIGHTS,
            ),
            PluginMenuItem(
                link="plugins:netbox_insights:assets_by_manufacturer_report",
                link_text="Assets by Manufacturer",
                permissions=VIEW_INSIGHTS,
            ),
        )),
    ],
    icon_class="mdi mdi-chart-box",
)

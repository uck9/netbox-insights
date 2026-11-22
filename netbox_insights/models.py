from django.db import models


class InsightsPermissions(models.Model):
    """
    A model-less placeholder used only to register a single, plugin-wide
    permission that gates access to the Insights menu and all of its views.

    This model is intentionally unmanaged (no database table) since it exists
    solely to anchor the `netbox_insights.view_insights` permission.
    """

    class Meta:
        managed = False
        default_permissions = ()
        permissions = (
            ('view_insights', 'Can view Insights'),
        )

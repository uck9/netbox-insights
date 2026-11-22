from django.db import models


class Insights(models.Model):
    """
    A model-less placeholder used only to anchor a single, plugin-wide
    permission that gates access to the Insights menu and all of its views.

    This model is intentionally unmanaged (no database table) since it exists
    solely so NetBox has a content type to attach the standard "view"
    permission to. The resulting permission is `netbox_insights.view_insights`.
    """

    class Meta:
        managed = False
        default_permissions = ('view',)

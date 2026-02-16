from django.urls import path
from netbox.views.generic import ObjectChangeLogView


from . import models, views


urlpatterns = (
     path('devices/', views.DeviceInsightsListView.as_view(), name='deviceinsight_list'),
)

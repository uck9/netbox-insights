from netbox.api.routers import NetBoxRouter
from .views import DeviceInsightsViewSet

router = NetBoxRouter()
router.register("device-insights", DeviceInsightsViewSet, basename="deviceinsights")

urlpatterns = router.urls

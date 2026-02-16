from netbox.api.routers import NetBoxRouter
from .views import DeviceInsightsViewSet

router = NetBoxRouter()
router.register("devices", DeviceInsightsViewSet, basename="deviceinsights")

urlpatterns = router.urls

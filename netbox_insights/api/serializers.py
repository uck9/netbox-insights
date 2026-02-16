from django.conf import settings
from rest_framework import serializers

from dcim.api.serializers_.devicetypes import DeviceTypeSerializer
from dcim.api.serializers_.sites import SiteSerializer
from tenancy.api.serializers_.tenants import TenantSerializer
from dcim.models import Device
from netbox.api.serializers import NetBoxModelSerializer


__all__ = (
    "DeviceInsightsSerializer",
    "HardwareLifecycleDetailsSerializer",
)


class HardwareLifecycleDetailsSerializer(serializers.Serializer):
    is_supported = serializers.SerializerMethodField()
    days_to_vendor_eos = serializers.SerializerMethodField()
    calc_budget_year = serializers.SerializerMethodField()
    calc_replacement_year = serializers.SerializerMethodField()

    hw_end_of_sale = serializers.SerializerMethodField()
    hw_end_of_security = serializers.SerializerMethodField()
    hw_end_of_support = serializers.SerializerMethodField()

    tracked_eox_date = serializers.SerializerMethodField()
    tracked_eox_basis = serializers.SerializerMethodField()

    def get_lifecycle(self, obj):
        return self.context.get("lifecycle")

    def get_calc_budget_year(self, obj):
        lifecycle = self.get_lifecycle(obj)
        return getattr(lifecycle, "calc_budget_year", None) if lifecycle else None

    def get_calc_replacement_year(self, obj):
        lifecycle = self.get_lifecycle(obj)
        return getattr(lifecycle, "calc_replacement_year", None) if lifecycle else None

    def get_is_supported(self, obj):
        lifecycle = self.get_lifecycle(obj)

        if lifecycle is None:
            # No EoL record → default supported
            return True

        # If record exists, respect its value
        return getattr(lifecycle, "is_supported", True)

    def get_days_to_vendor_eos(self, obj):
        lifecycle = self.get_lifecycle(obj)
        return getattr(lifecycle, "days_to_vendor_eos", None) if lifecycle else None

    def _get_annotated_date(self, obj, attr: str):
        return getattr(obj, attr, None)

    def get_hw_end_of_sale(self, obj):
        return self._get_annotated_date(obj, "hw_end_of_sale")

    def get_hw_end_of_security(self, obj):
        return self._get_annotated_date(obj, "hw_end_of_security")

    def get_hw_end_of_support(self, obj):
        return self._get_annotated_date(obj, "hw_end_of_support")

    def get_tracked_eox_date(self, obj):
        return self._get_annotated_date(obj, "tracked_eox_date")

    def get_tracked_eox_basis(self, obj):
        basis = getattr(obj, "tracked_eox_basis", None)
        if not basis:
            return None
        return str(basis).replace("_", " ").replace("-", " ").title()


class DeviceInsightsSerializer(NetBoxModelSerializer):
    site = SiteSerializer(nested=True)
    device_type = DeviceTypeSerializer(nested=True)

    tenant = TenantSerializer(
        nested=True,
        required=False,
        allow_null=True,
        default=None,
    )

    device_role = serializers.CharField(source="role.name", read_only=True, allow_null=True)

    hw_lifecycle = serializers.SerializerMethodField()

    support_contracts = serializers.SerializerMethodField()

    custom_fields = serializers.SerializerMethodField()

    def get_custom_fields(self, obj: Device):
        request = self.context.get("request")

        if request and request.query_params.get("cf"):
            names = [n.strip() for n in request.query_params.get("cf", "").split(",") if n.strip()]
        else:
            names = (
                getattr(settings, "PLUGINS_CONFIG", {})
                .get("netbox_insights", {})
                .get("device_cf_whitelist", [])
            )

        data = getattr(obj, "custom_field_data", {}) or {}
        return {k: data.get(k) for k in names if k in data}

    def get_hw_lifecycle(self, obj):
        lifecycle_map = self.context.get("lifecycle_map", {}) or {}
        lifecycle = lifecycle_map.get(obj.device_type_id)

        serializer = HardwareLifecycleDetailsSerializer(
            obj,
            context={"lifecycle": lifecycle},
        )
        return serializer.data

    def get_support_contracts(self, obj):
        contract_id = getattr(obj, "support_contract_id", None)
        contract_type = getattr(obj, "support_contract_type", None)
        end_date = getattr(obj, "support_contract_end_date", None)
        sku = getattr(obj, "support_contract_sku", None)
        sku_desc = getattr(obj, "support_contract_sku_desc", None)

        if not any([contract_id, contract_type, end_date, sku]):
            return []

        type_labels = {
            "support-ea": "EA",
            "support-alc": "ALC",
        }

        return [
            {
                "contract_type": type_labels.get(contract_type, contract_type),
                "contract_id": contract_id,
                "contract_end_date": end_date,
                "contract_sku": sku,
                "contract_sku_description": sku_desc,
                "is_primary": True,
            }
        ]

    class Meta:
        model = Device
        fields = [
            "id",
            "name",
            "status",
            "site",
            "tenant",
            "device_role",
            "device_type",
            "serial",
            "hw_lifecycle",
            "support_contracts",
            "custom_fields",
        ]

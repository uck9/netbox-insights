from datetime import date

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
        assignments = getattr(obj, "support_contracts_list", [])
        today = date.today()

        type_labels = {"support-ea": "EA", "support-alc": "ALC"}

         # Pull support_state from assigned asset if present
        assigned_asset = getattr(obj, "assigned_asset", None)
        support_state = getattr(assigned_asset, "support_state", None)
        support_reason = getattr(assigned_asset, "support_reason", None)

        contracts = []
        for i, a in enumerate(assignments):
            end_date = a.end_date or (a.contract.end_date if a.contract else None)
            days_remaining = (end_date - today).days if end_date else None
            sku = a.sku
            contract_type_raw = a.contract.contract_type if a.contract else None
            contracts.append({
                "contract_id": a.contract.contract_id if a.contract else None,
                "contract_type": type_labels.get(contract_type_raw, contract_type_raw),
                "end_date": end_date,
                "days_remaining": days_remaining,
                "sku": sku.sku if sku else None,
                "sku_description": sku.description if sku else None,
                "is_primary": i == 0,
            })

        return {
            "has_active_contract": len(contracts) > 0,
            "support_status": support_state,
            "support_reason": support_reason,
            "contract_count": len(contracts),
            "contracts": contracts,
        }

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

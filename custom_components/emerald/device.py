"""Device-registry helpers shared by entity platforms."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from .api import ElectricityAdvisorInfo, HeatPumpInfo
from .const import DOMAIN


def hws_device_info(info: HeatPumpInfo) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, info.id)},
        manufacturer=info.brand or "Emerald",
        model=info.model or "Heat Pump Hot Water",
        name="Heat Pump Hot Water",
        sw_version=info.soft_version or None,
        hw_version=info.hw_version or None,
        serial_number=info.serial_number or None,
    )


def ea_device_info(info: ElectricityAdvisorInfo) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, info.id)},
        manufacturer="Emerald",
        model="Electricity Advisor",
        name="Electricity Advisor",
        serial_number=info.serial_number or None,
    )

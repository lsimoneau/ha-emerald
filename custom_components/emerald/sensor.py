from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CURRENCY_DOLLAR,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ElectricityAdvisorInfo, HeatPumpInfo
from .coordinator import (
    EaSnapshot,
    ElectricityAdvisorCoordinator,
    EmeraldConfigEntry,
    HwsCoordinator,
)
from .device import ea_device_info, hws_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EmeraldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    rt = entry.runtime_data
    entities: list[CoordinatorEntity] = []

    if rt.hws is not None:
        for info in rt.hws.infos.values():
            entities.append(HwsTempSensor(rt.hws, info))
            entities.append(HwsHourlyEnergySensor(rt.hws, info))

    if rt.ea is not None:
        for info in rt.ea.infos.values():
            entities.extend(
                [
                    EaPowerSensor(rt.ea, info),
                    EaEnergyTodaySensor(rt.ea, info),
                    EaCostTodaySensor(rt.ea, info),
                    EaLastSyncedSensor(rt.ea, info),
                ]
            )

    async_add_entities(entities)


# ---------- HWS sensors -----------------------------------------------------


class _HwsBase(CoordinatorEntity[HwsCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HwsCoordinator,
        info: HeatPumpInfo,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self._info = info
        self.entity_description = description
        self._attr_unique_id = f"{info.id}_{description.key}"
        self._attr_device_info = hws_device_info(info)

    @property
    def _raw(self) -> dict[str, Any] | None:
        return (
            self.coordinator.data.get(self._info.id)
            if self.coordinator.data
            else None
        )


class HwsTempSensor(_HwsBase):
    def __init__(self, coordinator: HwsCoordinator, info: HeatPumpInfo) -> None:
        super().__init__(
            coordinator,
            info,
            SensorEntityDescription(
                key="current_temperature",
                translation_key="current_temperature",
                device_class=SensorDeviceClass.TEMPERATURE,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            ),
        )

    @property
    def native_value(self) -> float | None:
        raw = self._raw
        if not raw:
            return None
        v = (raw.get("last_state") or {}).get("temp_current")
        return float(v) if v is not None else None


class HwsHourlyEnergySensor(_HwsBase):
    """Current-hour kWh, as reported by the heat pump."""

    def __init__(self, coordinator: HwsCoordinator, info: HeatPumpInfo) -> None:
        super().__init__(
            coordinator,
            info,
            SensorEntityDescription(
                key="current_hour_energy",
                translation_key="current_hour_energy",
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            ),
        )

    @property
    def native_value(self) -> float | None:
        raw = self._raw
        if not raw:
            return None
        blob = raw.get("consumption_data")
        if not blob:
            return None
        try:
            data = json.loads(blob)
        except (TypeError, ValueError):
            return None
        v = data.get("current_hour")
        return float(v) if v is not None else None


# ---------- Electricity Advisor sensors ------------------------------------


class _EaBase(
    CoordinatorEntity[ElectricityAdvisorCoordinator], SensorEntity
):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ElectricityAdvisorCoordinator,
        info: ElectricityAdvisorInfo,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self._info = info
        self.entity_description = description
        self._attr_unique_id = f"{info.id}_{description.key}"
        self._attr_device_info = ea_device_info(info)

    @property
    def _snap(self) -> EaSnapshot | None:
        return (
            self.coordinator.data.get(self._info.id)
            if self.coordinator.data
            else None
        )


class EaPowerSensor(_EaBase):
    def __init__(
        self,
        coordinator: ElectricityAdvisorCoordinator,
        info: ElectricityAdvisorInfo,
    ) -> None:
        super().__init__(
            coordinator,
            info,
            SensorEntityDescription(
                key="power",
                translation_key="power",
                device_class=SensorDeviceClass.POWER,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfPower.WATT,
            ),
        )

    @property
    def native_value(self) -> float | None:
        s = self._snap
        return s.latest_power_w if s else None


class EaEnergyTodaySensor(_EaBase):
    def __init__(
        self,
        coordinator: ElectricityAdvisorCoordinator,
        info: ElectricityAdvisorInfo,
    ) -> None:
        super().__init__(
            coordinator,
            info,
            SensorEntityDescription(
                key="energy_today",
                translation_key="energy_today",
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            ),
        )

    @property
    def native_value(self) -> float | None:
        s = self._snap
        return s.energy_today_kwh if s else None


class EaCostTodaySensor(_EaBase):
    def __init__(
        self,
        coordinator: ElectricityAdvisorCoordinator,
        info: ElectricityAdvisorInfo,
    ) -> None:
        super().__init__(
            coordinator,
            info,
            SensorEntityDescription(
                key="cost_today",
                translation_key="cost_today",
                device_class=SensorDeviceClass.MONETARY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                native_unit_of_measurement=CURRENCY_DOLLAR,
            ),
        )

    @property
    def native_value(self) -> float | None:
        s = self._snap
        return s.cost_today if s else None


class EaLastSyncedSensor(_EaBase):
    def __init__(
        self,
        coordinator: ElectricityAdvisorCoordinator,
        info: ElectricityAdvisorInfo,
    ) -> None:
        super().__init__(
            coordinator,
            info,
            SensorEntityDescription(
                key="last_synced",
                translation_key="last_synced",
                device_class=SensorDeviceClass.TIMESTAMP,
            ),
        )

    @property
    def native_value(self) -> datetime | None:
        s = self._snap
        return s.last_synced if s else None

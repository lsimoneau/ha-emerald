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
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ElectricityAdvisorInfo, HeatPumpInfo
from .coordinator import (
    ElectricityAdvisorCoordinator,
    EmeraldConfigEntry,
    HwsCoordinator,
)
from .device import ea_device_info, hws_device_info
from .ihd import IhdState


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
                    EaLastSeenSensor(rt.ea, info),
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


class _EaBase(CoordinatorEntity[ElectricityAdvisorCoordinator], SensorEntity):
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
    def _state(self) -> IhdState | None:
        return (
            self.coordinator.data.get(self._info.id)
            if self.coordinator.data
            else None
        )


class EaPowerSensor(_EaBase):
    """Instantaneous house draw, polled from the LiveLink."""

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
        s = self._state
        return float(s.power_w) if s and s.power_w is not None else None


class EaEnergyTodaySensor(_EaBase):
    """Energy used since local midnight, accumulated from 10-min flash bins."""

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
        s = self._state
        return s.energy_today_kwh if s else None


class EaLastSeenSensor(_EaBase):
    """Wall-clock timestamp of the most recent message from the LiveLink."""

    def __init__(
        self,
        coordinator: ElectricityAdvisorCoordinator,
        info: ElectricityAdvisorInfo,
    ) -> None:
        super().__init__(
            coordinator,
            info,
            SensorEntityDescription(
                key="last_seen",
                translation_key="last_seen",
                device_class=SensorDeviceClass.TIMESTAMP,
            ),
        )

    @property
    def native_value(self) -> datetime | None:
        s = self._state
        return s.last_seen if s else None

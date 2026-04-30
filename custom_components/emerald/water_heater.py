from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    STATE_ECO,
    STATE_HEAT_PUMP,
    STATE_OFF,
    STATE_PERFORMANCE,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import HeatPumpInfo
from .coordinator import EmeraldConfigEntry, HwsCoordinator
from .device import hws_device_info
from .hws import HWS_MODE_BOOST, HWS_MODE_NORMAL, HWS_MODE_QUIET

# Map HA state ↔ vendor mode int. STATE_OFF is handled via switch=0.
# Emerald "normal" is the standard heat-pump operating mode; "quiet" runs at
# reduced fan/compressor speed. STATE_HEAT_PUMP = the natural HP mode (normal),
# STATE_ECO = quieter/lower-power mode.
_STATE_TO_MODE = {
    STATE_PERFORMANCE: HWS_MODE_BOOST,
    STATE_HEAT_PUMP: HWS_MODE_NORMAL,
    STATE_ECO: HWS_MODE_QUIET,
}
_MODE_TO_STATE = {v: k for k, v in _STATE_TO_MODE.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EmeraldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coord = entry.runtime_data.hws
    if coord is None:
        return
    async_add_entities(
        EmeraldHwsEntity(coord, info) for info in coord.infos.values()
    )


class EmeraldHwsEntity(CoordinatorEntity[HwsCoordinator], WaterHeaterEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = WaterHeaterEntityFeature.OPERATION_MODE
    _attr_operation_list = [
        STATE_OFF,
        STATE_ECO,
        STATE_PERFORMANCE,
        STATE_HEAT_PUMP,
    ]

    def __init__(self, coordinator: HwsCoordinator, info: HeatPumpInfo) -> None:
        super().__init__(coordinator)
        self._info = info
        self._attr_unique_id = info.id
        self._attr_device_info = hws_device_info(info)

    @property
    def _state(self) -> dict[str, Any] | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self._info.id)

    @property
    def _last(self) -> dict[str, Any]:
        s = self._state
        return s.get("last_state") if s else {} or {}

    @property
    def current_temperature(self) -> float | None:
        v = self._last.get("temp_current")
        return float(v) if v is not None else None

    @property
    def target_temperature(self) -> float | None:
        v = self._last.get("temp_set")
        return float(v) if v is not None else None

    @property
    def current_operation(self) -> str | None:
        switch = self._last.get("switch")
        if switch in (0, "off"):
            return STATE_OFF
        mode = self._last.get("mode")
        return _MODE_TO_STATE.get(mode)

    @property
    def available(self) -> bool:
        return super().available and self._state is not None

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        bridge = self.coordinator.bridge
        if operation_mode == STATE_OFF:
            await bridge.async_set_switch(self._info.id, on=False)
            return
        mode = _STATE_TO_MODE.get(operation_mode)
        if mode is None:
            raise ValueError(f"unsupported operation mode: {operation_mode}")
        # Ensure unit is on before setting mode.
        if self._last.get("switch") in (0, "off"):
            await bridge.async_set_switch(self._info.id, on=True)
        await bridge.async_set_mode(self._info.id, mode)

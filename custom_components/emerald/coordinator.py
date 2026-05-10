from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import (
    ElectricityAdvisorInfo,
    EmeraldRestClient,
    HeatPumpInfo,
)
from .const import DOMAIN
from .hws import HwsBridge
from .ihd import IhdBridge, IhdState

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class EmeraldRuntimeData:
    rest: EmeraldRestClient
    hws: HwsCoordinator | None
    ea: ElectricityAdvisorCoordinator | None


type EmeraldConfigEntry = ConfigEntry[EmeraldRuntimeData]


class HwsCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Push-driven: data is fed by the MQTT thread via the bridge."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: EmeraldConfigEntry,
        bridge: HwsBridge,
        infos: list[HeatPumpInfo],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_hws",
            update_interval=None,  # push-only
            config_entry=entry,
        )
        self.bridge = bridge
        self.infos: dict[str, HeatPumpInfo] = {hp.id: hp for hp in infos}
        bridge.set_update_handler(self._refresh_from_bridge)

    async def async_start(self) -> None:
        await self.bridge.async_connect()
        self._refresh_from_bridge()

    async def async_stop(self) -> None:
        await self.bridge.async_disconnect()

    @callback
    def _refresh_from_bridge(self) -> None:
        snapshot = {
            hws_id: state
            for hws_id in self.infos
            if (state := self.bridge.get_state(hws_id)) is not None
        }
        self.async_set_updated_data(snapshot)


class ElectricityAdvisorCoordinator(DataUpdateCoordinator[dict[str, IhdState]]):
    """Push-driven: data is fed by the IHD MQTT thread via the bridge."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: EmeraldConfigEntry,
        bridge: IhdBridge,
        infos: list[ElectricityAdvisorInfo],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_ea",
            update_interval=None,  # push-only
            config_entry=entry,
        )
        self.bridge = bridge
        self.infos: dict[str, ElectricityAdvisorInfo] = {ea.id: ea for ea in infos}
        bridge.set_update_handler(self._refresh_from_bridge)

    async def async_start(self) -> None:
        await self.bridge.async_start()
        self._refresh_from_bridge()

    async def async_stop(self) -> None:
        await self.bridge.async_stop()

    @callback
    def _refresh_from_bridge(self) -> None:
        snapshot = {
            ea_id: state
            for ea_id in self.infos
            if (state := self.bridge.get_state(ea_id)) is not None
        }
        self.async_set_updated_data(snapshot)

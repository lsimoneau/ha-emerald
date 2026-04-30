from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ElectricityAdvisorInfo,
    EmeraldApiError,
    EmeraldAuthError,
    EmeraldRestClient,
    HeatPumpInfo,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .hws import HwsBridge

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


class ElectricityAdvisorCoordinator(
    DataUpdateCoordinator[dict[str, "EaSnapshot"]]
):
    """Polls today's flashes-data per EA device."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: EmeraldConfigEntry,
        rest: EmeraldRestClient,
        infos: list[ElectricityAdvisorInfo],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_ea",
            update_interval=DEFAULT_SCAN_INTERVAL,
            config_entry=entry,
        )
        self.rest = rest
        self.infos: dict[str, ElectricityAdvisorInfo] = {
            ea.id: ea for ea in infos
        }

    async def _async_update_data(self) -> dict[str, EaSnapshot]:
        today = datetime.now().date()
        out: dict[str, EaSnapshot] = {}
        for device_id in self.infos:
            try:
                payload = await self.rest.async_get_flashes(device_id, today)
            except EmeraldAuthError as err:
                raise UpdateFailed(f"auth: {err}") from err
            except EmeraldApiError as err:
                raise UpdateFailed(str(err)) from err
            out[device_id] = EaSnapshot.from_payload(payload)
        return out


@dataclass(slots=True)
class EaSnapshot:
    """Distilled view over one device's flashes-data response."""

    last_synced: datetime | None
    energy_today_kwh: float
    cost_today: float
    latest_bin_kwh: float | None
    latest_bin_time: str | None  # "HH:MM"
    average_daily_spend: float | None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> EaSnapshot:
        synced_ms = payload.get("synced_timestamp")
        last_synced = (
            datetime.fromtimestamp(synced_ms / 1000, tz=UTC) if synced_ms else None
        )

        days = payload.get("daily_consumptions") or []
        today_day = days[0] if days else {}
        energy = float(today_day.get("total_kwh_of_day") or 0.0)
        cost = float(today_day.get("total_cost_of_day") or 0.0)

        bins = today_day.get("ten_minute_consumptions") or []
        latest = next(
            (b for b in reversed(bins) if (b.get("number_of_flashes") or 0) > 0),
            None,
        )

        return cls(
            last_synced=last_synced,
            energy_today_kwh=energy,
            cost_today=cost,
            latest_bin_kwh=float(latest["kwh"]) if latest else None,
            latest_bin_time=latest.get("time_string") if latest else None,
            average_daily_spend=payload.get("average_daily_spend"),
        )

    @property
    def latest_power_w(self) -> float | None:
        """Convert latest 10-min kWh to average W over that window."""
        if self.latest_bin_kwh is None:
            return None
        return self.latest_bin_kwh * 1000.0 * 6.0  # kWh * 1000 / (10/60) h

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EmeraldApiError, EmeraldAuthError, EmeraldRestClient
from .const import CONF_PASSWORD, CONF_USERNAME
from .coordinator import (
    ElectricityAdvisorCoordinator,
    EmeraldConfigEntry,
    EmeraldRuntimeData,
    HwsCoordinator,
)
from .hws import HwsBridge

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.WATER_HEATER]


async def async_setup_entry(
    hass: HomeAssistant, entry: EmeraldConfigEntry
) -> bool:
    rest = EmeraldRestClient(
        session=async_get_clientsession(hass),
        email=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
    )

    try:
        await rest.async_login()
        discovery = await rest.async_discover()
    except EmeraldAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except EmeraldApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    hws_coord: HwsCoordinator | None = None
    if discovery.heat_pumps:
        bridge = HwsBridge(
            hass, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
        )
        hws_coord = HwsCoordinator(hass, entry, bridge, discovery.heat_pumps)
        try:
            await hws_coord.async_start()
        except Exception as err:
            raise ConfigEntryNotReady(f"HWS connect failed: {err}") from err

    ea_coord: ElectricityAdvisorCoordinator | None = None
    if discovery.electricity_advisors:
        ea_coord = ElectricityAdvisorCoordinator(
            hass, entry, rest, discovery.electricity_advisors
        )
        await ea_coord.async_config_entry_first_refresh()

    entry.runtime_data = EmeraldRuntimeData(
        rest=rest, hws=hws_coord, ea=ea_coord
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: EmeraldConfigEntry
) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and entry.runtime_data.hws is not None:
        await entry.runtime_data.hws.async_stop()
    return unloaded

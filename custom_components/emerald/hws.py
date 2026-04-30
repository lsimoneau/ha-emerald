"""HA-friendly wrapper around the synchronous `emerald_hws` library.

The vendor library uses background threads for MQTT callbacks, AWS IoT
reconnect, and health checks. Everything that crosses into HA's event loop
is bounced via `hass.loop.call_soon_threadsafe(...)`.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from emerald_hws.emeraldhws import EmeraldHWS
from homeassistant.core import HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)


# Vendor uses 0=boost, 1=normal, 2=quiet — mirror that here so callers
# don't need to know the magic numbers.
HWS_MODE_BOOST = 0
HWS_MODE_NORMAL = 1
HWS_MODE_QUIET = 2


class HwsBridge:
    """Owns the EmeraldHWS instance and marshals events into the event loop."""

    def __init__(
        self,
        hass: HomeAssistant,
        email: str,
        password: str,
    ) -> None:
        self._hass = hass
        self._client = EmeraldHWS(
            email,
            password,
            update_callback=self._on_thread_callback,
        )
        self._on_update: Callable[[], None] | None = None

    def set_update_handler(self, handler: Callable[[], None]) -> None:
        """Register the loop-side callback invoked after each state change."""
        self._on_update = handler

    async def async_connect(self) -> None:
        await self._hass.async_add_executor_job(self._client.connect)

    async def async_disconnect(self) -> None:
        await self._hass.async_add_executor_job(self._client.disconnect)

    def list_ids(self) -> list[str]:
        return self._client.listHWS()

    def get_state(self, hws_id: str) -> dict[str, Any] | None:
        return self._client.getFullStatus(hws_id)

    async def async_set_switch(self, hws_id: str, on: bool) -> None:
        fn = self._client.turnOn if on else self._client.turnOff
        await self._hass.async_add_executor_job(fn, hws_id)

    async def async_set_mode(self, hws_id: str, mode: int) -> None:
        fn_by_mode = {
            HWS_MODE_BOOST: self._client.setBoostMode,
            HWS_MODE_NORMAL: self._client.setNormalMode,
            HWS_MODE_QUIET: self._client.setQuietMode,
        }
        fn = fn_by_mode.get(mode)
        if fn is None:
            raise ValueError(f"unknown HWS mode: {mode}")
        await self._hass.async_add_executor_job(fn, hws_id)

    def _on_thread_callback(self) -> None:
        """Invoked from emerald_hws's MQTT callback thread."""
        self._hass.loop.call_soon_threadsafe(self._dispatch)

    @callback
    def _dispatch(self) -> None:
        if self._on_update is not None:
            self._on_update()

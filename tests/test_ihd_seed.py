"""Cover the REST-seed and 10-min watermark behaviour in `IhdBridge`.

The integration backfills today's energy use from the cloud REST endpoint at
startup and then accumulates further bins via MQTT. These tests target the
two correctness pieces of that handover: the seed itself, and the watermark
that prevents bins from being counted twice when the LiveLink later uploads
a backlog or MQTT redelivers a message.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.util import dt as dt_util

from custom_components.emerald.api import ElectricityAdvisorInfo
from custom_components.emerald.ihd import IhdBridge


def _info(ea_id: str = "EA1", gateway: str = "GW1") -> ElectricityAdvisorInfo:
    return ElectricityAdvisorInfo(
        id=ea_id,
        property_id="P1",
        serial_number="LL000",
        mac_address="00:11:22:33:44:55",
        gateway_id=gateway,
        name="EA",
        nmi=None,
        impulse_rate=1000,
    )


def _bridge(rest: AsyncMock) -> IhdBridge:
    hass = MagicMock()
    return IhdBridge(hass=hass, rest=rest, infos=[_info()], client_id="ha-emerald-t")


def _publish_packet(
    end_time: datetime, flashes: int, sub: str = "EIAdv-X"
) -> SimpleNamespace:
    payload = [
        {"command": "ihd_10min", "device_id": "GW1"},
        {
            "sub_device_id": sub,
            "flashes": flashes,
            "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
        },
    ]
    pkt = SimpleNamespace(payload=json.dumps(payload).encode())
    return SimpleNamespace(publish_packet=pkt)


def _floor_10min(dt: datetime) -> datetime:
    return dt.replace(minute=dt.minute - dt.minute % 10, second=0, microsecond=0)


@pytest.fixture
def now_local():
    """Return HA-local 'now' — tests use this rather than a hard-coded date so
    they don't depend on whatever timezone the HA test harness is using."""
    return dt_util.now()


async def test_seed_populates_running_total(now_local) -> None:
    rest = AsyncMock()
    rest.async_get_today_kwh.return_value = 5.4321
    bridge = _bridge(rest)

    await bridge._async_seed_today()

    state = bridge.get_state("EA1")
    assert state.today == now_local.date()
    assert state.energy_today_kwh == pytest.approx(5.4321)
    assert state.counted_through == _floor_10min(now_local)
    rest.async_get_today_kwh.assert_awaited_once()


async def test_seed_handles_empty_response(now_local) -> None:
    rest = AsyncMock()
    rest.async_get_today_kwh.return_value = None
    bridge = _bridge(rest)

    await bridge._async_seed_today()

    state = bridge.get_state("EA1")
    assert state.energy_today_kwh == 0.0
    assert state.today == now_local.date()
    assert state.counted_through is not None


async def test_backlog_bin_skipped_after_seed(now_local) -> None:
    rest = AsyncMock()
    rest.async_get_today_kwh.return_value = 5.0
    bridge = _bridge(rest)
    await bridge._async_seed_today()
    bridge._states["EA1"].sub_device_id = "EIAdv-X"

    # A bin whose end is two hours before the watermark — the LiveLink
    # uploaded it after we woke it up; we must not add it on top of REST.
    backlog_end = _floor_10min(now_local) - timedelta(hours=2)
    bridge._on_publish_received(_publish_packet(backlog_end, flashes=60))

    assert bridge.get_state("EA1").energy_today_kwh == pytest.approx(5.0)


async def test_fresh_bin_after_seed_accumulates(now_local) -> None:
    rest = AsyncMock()
    rest.async_get_today_kwh.return_value = 5.0
    bridge = _bridge(rest)
    await bridge._async_seed_today()
    bridge._states["EA1"].sub_device_id = "EIAdv-X"

    fresh_end = _floor_10min(now_local) + timedelta(minutes=10)
    bridge._on_publish_received(_publish_packet(fresh_end, flashes=100))

    assert bridge.get_state("EA1").energy_today_kwh == pytest.approx(5.1)


async def test_duplicate_bin_redelivery_idempotent(now_local) -> None:
    rest = AsyncMock()
    rest.async_get_today_kwh.return_value = 0.0
    bridge = _bridge(rest)
    await bridge._async_seed_today()
    bridge._states["EA1"].sub_device_id = "EIAdv-X"

    fresh_end = _floor_10min(now_local) + timedelta(minutes=10)
    msg = _publish_packet(fresh_end, flashes=100)
    bridge._on_publish_received(msg)
    bridge._on_publish_received(msg)  # QoS 1 redelivery

    assert bridge.get_state("EA1").energy_today_kwh == pytest.approx(0.1)


async def test_date_rollover_resets_total_and_watermark(now_local) -> None:
    rest = AsyncMock()
    rest.async_get_today_kwh.return_value = 12.0
    bridge = _bridge(rest)
    await bridge._async_seed_today()
    bridge._states["EA1"].sub_device_id = "EIAdv-X"

    next_day_end = (now_local + timedelta(days=1)).replace(
        hour=0, minute=10, second=0, microsecond=0
    )
    bridge._on_publish_received(_publish_packet(next_day_end, flashes=50))

    state = bridge.get_state("EA1")
    assert state.today == (now_local + timedelta(days=1)).date()
    assert state.energy_today_kwh == pytest.approx(0.05)
    assert state.counted_through is not None
    assert state.counted_through.date() == (now_local + timedelta(days=1)).date()

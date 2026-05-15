"""Cover the staleness-driven reconnect in `IhdBridge`.

After we lost a full day of cumulative-energy data when the AWS IoT MQTT
session went zombie (publishes silently queued, no lifecycle event ever
fired), the bridge gained a freshness watermark + a poll-tick liveness
check that tears the client down and rebuilds it when no inbound traffic
has arrived for `IHD_STALE_RECONNECT_AFTER`.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.emerald.api import ElectricityAdvisorInfo
from custom_components.emerald.const import IHD_STALE_RECONNECT_AFTER
from custom_components.emerald.ihd import IhdBridge


def _info() -> ElectricityAdvisorInfo:
    return ElectricityAdvisorInfo(
        id="EA1",
        property_id="P1",
        serial_number="LL000",
        mac_address="00:11:22:33:44:55",
        gateway_id="GW1",
        name="EA",
        nmi=None,
        impulse_rate=1000,
    )


def _bridge() -> IhdBridge:
    hass = MagicMock()
    # async_add_executor_job needs to actually run the function so we can
    # observe its side effects; a real HA test harness would do this for us.
    async def _run(fn, *args):
        return fn(*args)

    hass.async_add_executor_job = AsyncMock(side_effect=_run)
    return IhdBridge(
        hass=hass, rest=AsyncMock(), infos=[_info()], client_id="ha-emerald-t"
    )


def _empty_packet() -> SimpleNamespace:
    return SimpleNamespace(
        publish_packet=SimpleNamespace(payload=json.dumps([{}, {}]).encode())
    )


def test_inbound_refreshes_watermark() -> None:
    bridge = _bridge()
    assert bridge._last_inbound_monotonic is None

    bridge._on_publish_received(_empty_packet())

    assert bridge._last_inbound_monotonic is not None


def test_stale_when_no_traffic_yet() -> None:
    bridge = _bridge()
    assert bridge._is_stale() is True


def test_not_stale_when_traffic_recent() -> None:
    bridge = _bridge()
    bridge._client = MagicMock()
    bridge._on_publish_received(_empty_packet())

    assert bridge._is_stale() is False


def test_stale_when_traffic_old(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _bridge()
    bridge._client = MagicMock()
    bridge._on_publish_received(_empty_packet())
    # Wind the clock forward past the threshold.
    monkeypatch.setattr(
        "custom_components.emerald.ihd.time.monotonic",
        lambda: bridge._last_inbound_monotonic
        + IHD_STALE_RECONNECT_AFTER.total_seconds()
        + 1,
    )

    assert bridge._is_stale() is True


def test_reconnect_in_progress_suppresses_staleness() -> None:
    bridge = _bridge()
    bridge._reconnect_in_progress = True

    # Even with no traffic ever, an in-flight reconnect must not be
    # interpreted as another reason to reconnect.
    assert bridge._is_stale() is False


async def test_force_reconnect_runs_disconnect_then_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _bridge()
    bridge._client = MagicMock()
    bridge._disconnect_blocking = MagicMock()
    bridge._connect_blocking = MagicMock()
    bridge._publish_get_gw_info_all = MagicMock()
    # Skip the real backoff so the test runs fast.
    monkeypatch.setattr(
        "custom_components.emerald.ihd.asyncio.sleep", AsyncMock()
    )

    await bridge._async_force_reconnect()

    bridge._disconnect_blocking.assert_called_once()
    bridge._connect_blocking.assert_called_once()
    bridge._publish_get_gw_info_all.assert_called_once()
    assert bridge._reconnect_in_progress is False


async def test_force_reconnect_backs_off_between_disconnect_and_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The throttle-avoidance pause must run between teardown and rebuild."""
    bridge = _bridge()
    bridge._client = MagicMock()
    call_order: list[str] = []
    bridge._disconnect_blocking = MagicMock(
        side_effect=lambda: call_order.append("disconnect")
    )
    bridge._connect_blocking = MagicMock(
        side_effect=lambda: call_order.append("connect")
    )
    bridge._publish_get_gw_info_all = MagicMock()

    async def _record_sleep(_):
        call_order.append("sleep")

    monkeypatch.setattr(
        "custom_components.emerald.ihd.asyncio.sleep", _record_sleep
    )

    await bridge._async_force_reconnect()

    assert call_order == ["disconnect", "sleep", "connect"]


async def test_force_reconnect_clears_flag_on_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _bridge()
    bridge._client = MagicMock()
    bridge._disconnect_blocking = MagicMock()
    bridge._connect_blocking = MagicMock(side_effect=RuntimeError("nope"))
    bridge._publish_get_gw_info_all = MagicMock()
    monkeypatch.setattr(
        "custom_components.emerald.ihd.asyncio.sleep", AsyncMock()
    )

    await bridge._async_force_reconnect()

    bridge._publish_get_gw_info_all.assert_not_called()
    # Flag must come back down so the next tick can try again.
    assert bridge._reconnect_in_progress is False


async def test_poll_tick_triggers_reconnect_when_stale() -> None:
    bridge = _bridge()
    bridge._async_force_reconnect = AsyncMock()
    # _is_stale returns True because _client is None.

    await bridge._async_poll_tick(None)

    bridge._async_force_reconnect.assert_awaited_once()


async def test_poll_tick_polls_normally_when_fresh() -> None:
    bridge = _bridge()
    bridge._client = MagicMock()
    bridge._on_publish_received(_empty_packet())
    bridge._async_force_reconnect = AsyncMock()
    bridge._publish_cur_consump = MagicMock()

    await bridge._async_poll_tick(None)

    bridge._async_force_reconnect.assert_not_called()
    bridge._publish_cur_consump.assert_called_once_with("EA1")

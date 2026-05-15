"""MQTT bridge for the LiveLink (Electricity Advisor / IHD).

The Emerald cloud uses AWS IoT Core MQTT. The LiveLink publishes:
  * `ep/ihd/from_dev/{gw_id}` — gw→cloud: 10-minute energy bins (autonomous,
    on bin close), and responses to cloud-issued commands.
  * `ep/ihd/from_gw/{gw_id}` — gw→app: responses to app-issued commands.

We publish to:
  * `ep/ihd/to_gw/{gw_id}` — cloud→gw or app→gw commands.

The LiveLink only stays "warm" while it is being talked to; if no client
polls `cur_consump`, autonomous 10-min pushes also stop. We therefore poll
`cur_consump` on a fixed cadence both to expose instantaneous power and as
a keep-alive.

Threading: awscrt drives MQTT callbacks from its own thread. Anything that
touches HA state crosses back via `hass.loop.call_soon_threadsafe`.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import boto3
from awscrt import auth, io, mqtt5
from awsiot import mqtt5_client_builder
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .api import (
    ElectricityAdvisorInfo,
    EmeraldApiError,
    EmeraldAuthError,
    EmeraldRestClient,
)
from .const import (
    COGNITO_IDENTITY_POOL_ID,
    IHD_POLL_INTERVAL,
    IHD_STALE_RECONNECT_AFTER,
    MQTT_HOST,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class IhdState:
    """Per-EA-device runtime state."""

    power_w: int | None = None
    energy_today_kwh: float = 0.0
    today: date | None = None
    last_seen: datetime | None = None
    # Learned from incoming MQTT messages — needed to construct the
    # `sub_device_id` field that publishes to `ep/ihd/to_gw/{gw}` require.
    gateway_hw_id: str | None = None
    gateway_serial: str | None = None
    sub_device_id: str | None = None
    # Latest 10-min bin (for diagnostics / short-term graphs).
    latest_bin_kwh: float | None = None
    latest_bin_end: datetime | None = None
    # Watermark: bins with end_time <= this are skipped. Set by the REST seed
    # (so backlog bins the LiveLink subsequently uploads aren't double-counted)
    # and by each accepted bin (so QoS-1 redeliveries are idempotent). Cleared
    # on date rollover.
    counted_through: datetime | None = None


@dataclass
class _GatewayCtx:
    """Per-LiveLink state (one EA = one gateway in v1)."""

    gateway_id: str
    ea_ids: list[str] = field(default_factory=list)


class IhdBridge:
    """Owns one AWS IoT MQTT5 connection and routes IHD traffic."""

    def __init__(
        self,
        hass: HomeAssistant,
        rest: EmeraldRestClient,
        infos: list[ElectricityAdvisorInfo],
    ) -> None:
        self._hass = hass
        self._rest = rest
        self._infos: dict[str, ElectricityAdvisorInfo] = {ea.id: ea for ea in infos}
        self._states: dict[str, IhdState] = {ea.id: IhdState() for ea in infos}
        # Multiple EAs could share one gateway; group accordingly.
        self._gateways: dict[str, _GatewayCtx] = {}
        for ea in infos:
            ctx = self._gateways.setdefault(ea.gateway_id, _GatewayCtx(ea.gateway_id))
            ctx.ea_ids.append(ea.id)
        self._client: mqtt5.Client | None = None
        self._connected = threading.Event()
        self._on_update: Callable[[], None] | None = None
        self._poll_unsub: Callable[[], None] | None = None
        # Monotonic timestamp of the last inbound MQTT message — the canonical
        # liveness signal. We've observed the awscrt client go zombie (publishes
        # silently queue, no lifecycle event fires) after a credential renewal
        # failure; staleness here is the only reliable way to detect it.
        self._last_inbound_monotonic: float | None = None
        self._reconnect_in_progress = False

    # ---- public API used by the coordinator -------------------------------

    def set_update_handler(self, handler: Callable[[], None]) -> None:
        self._on_update = handler

    def get_state(self, ea_id: str) -> IhdState | None:
        return self._states.get(ea_id)

    async def async_start(self) -> None:
        # Seed today's running totals from REST *before* MQTT connects, so any
        # backlog bins the LiveLink uploads after we wake it up are skipped via
        # the `counted_through` watermark rather than double-counted.
        await self._async_seed_today()
        await self._hass.async_add_executor_job(self._connect_blocking)
        # Ask each gateway for its hw_id / serial so we can build sub_device_ids.
        await self._hass.async_add_executor_job(self._publish_get_gw_info_all)
        self._poll_unsub = async_track_time_interval(
            self._hass, self._async_poll_tick, IHD_POLL_INTERVAL
        )

    async def _async_seed_today(self) -> None:
        """Pre-fill today's running total from the REST flashes-data endpoint."""
        now_local = dt_util.now()
        today = now_local.date()
        # Floor to the most recent 10-min boundary; bins ending at or before
        # this we treat as already counted in the REST total.
        floor = now_local.replace(
            minute=now_local.minute - now_local.minute % 10,
            second=0,
            microsecond=0,
        )
        for ea_id, info in self._infos.items():
            try:
                kwh = await self._rest.async_get_today_kwh(info.id, today)
            except (EmeraldApiError, EmeraldAuthError) as err:
                _LOGGER.warning(
                    "ihd: REST seed failed for %s (%s) — energy_today starts at 0",
                    ea_id,
                    err,
                )
                continue
            st = self._states[ea_id]
            st.today = today
            st.energy_today_kwh = round(kwh or 0.0, 4)
            st.counted_through = floor
            _LOGGER.info(
                "ihd: seeded %s today=%s kwh=%.4f through=%s",
                ea_id,
                today,
                st.energy_today_kwh,
                floor.isoformat(),
            )

    async def async_stop(self) -> None:
        if self._poll_unsub is not None:
            self._poll_unsub()
            self._poll_unsub = None
        await self._hass.async_add_executor_job(self._disconnect_blocking)

    # ---- connection -------------------------------------------------------

    def _connect_blocking(self) -> None:
        region = MQTT_HOST.split(".")[2]
        cognito = boto3.client("cognito-identity", region_name=region)
        identity = cognito.get_id(IdentityPoolId=COGNITO_IDENTITY_POOL_ID)["IdentityId"]
        creds = auth.AwsCredentialsProvider.new_cognito(
            endpoint=f"cognito-identity.{region}.amazonaws.com",
            identity=identity,
            tls_ctx=io.ClientTlsContext(io.TlsContextOptions()),
        )
        self._connected.clear()
        client = mqtt5_client_builder.websockets_with_default_aws_signing(
            endpoint=MQTT_HOST,
            region=region,
            credentials_provider=creds,
            on_lifecycle_connection_success=lambda _data: self._connected.set(),
            on_lifecycle_connection_failure=self._on_connection_failure,
            on_lifecycle_disconnection=self._on_lifecycle_disconnection,
            on_publish_received=self._on_publish_received,
        )
        client.start()
        if not self._connected.wait(timeout=30):
            # Tear the half-built client down before raising — otherwise its
            # background threads leak and the next reconnect attempt has to
            # contend with them.
            try:
                client.stop()
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError("IHD MQTT did not connect within 30s")
        self._client = client
        # Give the fresh connection a full staleness window before the next
        # liveness check fires, so subscribe / get_gw_info round-trips have
        # time to land.
        self._last_inbound_monotonic = time.monotonic()

        for gw_id in self._gateways:
            for direction in ("from_dev", "from_gw"):
                client.subscribe(
                    subscribe_packet=mqtt5.SubscribePacket(
                        subscriptions=[
                            mqtt5.Subscription(
                                topic_filter=f"ep/ihd/{direction}/{gw_id}",
                                qos=mqtt5.QoS.AT_LEAST_ONCE,
                            ),
                        ],
                    ),
                ).result(20)

    def _disconnect_blocking(self) -> None:
        if self._client is None:
            return
        try:
            self._client.stop()
        except Exception as exc:  # noqa: BLE001 — best-effort shutdown
            _LOGGER.debug("ihd: stop raised: %s", exc)
        self._client = None
        self._connected.clear()

    def _on_connection_failure(self, data: Any) -> None:
        _LOGGER.warning("ihd: MQTT connection failure: %s", data)

    def _on_lifecycle_disconnection(self, data: Any) -> None:
        # Flip the flag and log; the next poll tick's staleness check is what
        # actually drives recovery — we don't trust this callback alone, since
        # the zombie-client case we're hardening against is precisely the one
        # where it doesn't fire.
        self._connected.clear()
        _LOGGER.warning("ihd: MQTT disconnected: %s", data)

    # ---- publish helpers (called from executor) ---------------------------

    def _publish_get_gw_info_all(self) -> None:
        for gw_id in self._gateways:
            self._publish(
                gw_id,
                namespace="config",
                command="get_gw_info",
                body={},
            )

    def _publish_cur_consump(self, ea_id: str) -> None:
        info = self._infos[ea_id]
        sub = self._states[ea_id].sub_device_id
        if sub is None:
            return  # haven't learned hw_id yet — skip until next tick
        self._publish(
            info.gateway_id,
            namespace="config",
            command="ihd_get_param",
            body={"sub_device_id": sub, "key": "cur_consump"},
        )

    def _publish(
        self,
        gateway_id: str,
        *,
        namespace: str,
        command: str,
        body: dict[str, Any],
    ) -> None:
        if self._client is None:
            return
        # The gateway accepts publishes that omit hw_id/serial — it identifies
        # itself by the topic. We include only what we have on hand.
        ea = next(
            (e for e in self._infos.values() if e.gateway_id == gateway_id),
            None,
        )
        header: dict[str, Any] = {
            "msg_id": str(random.randint(10000, 99999)),
            "namespace": namespace,
            "command": command,
            "direction": "cloud2gw",
            "device_id": gateway_id,
        }
        if ea is not None:
            header["property_id"] = ea.property_id
        # If we've learned the gateway's hw/serial, include them — closer to
        # what the official app sends.
        for ea_id in self._gateways[gateway_id].ea_ids:
            st = self._states[ea_id]
            if st.gateway_hw_id and st.gateway_serial:
                header["hw_id"] = st.gateway_hw_id
                header["serial_number"] = st.gateway_serial
                break
        msg = [header, body]
        try:
            self._client.publish(
                mqtt5.PublishPacket(
                    topic=f"ep/ihd/to_gw/{gateway_id}",
                    payload=json.dumps(msg).encode(),
                    qos=mqtt5.QoS.AT_LEAST_ONCE,
                )
            ).result(20)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("ihd: publish %s to %s failed: %s", command, gateway_id, exc)

    # ---- inbound messages (called from MQTT thread) ------------------------

    def _on_publish_received(self, data: Any) -> None:
        # Refresh the liveness watermark on *any* inbound traffic, before we
        # try to decode — the byte stream proves the link is alive whether or
        # not we can parse what came in.
        self._last_inbound_monotonic = time.monotonic()
        try:
            pkt = data.publish_packet
            payload = json.loads(pkt.payload.decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("ihd: undecodable message: %s", exc)
            return
        if not (isinstance(payload, list) and len(payload) >= 2):
            return
        header, body = payload[0], payload[1]
        if not isinstance(header, dict) or not isinstance(body, dict):
            return
        gateway_id = header.get("device_id")
        if gateway_id not in self._gateways:
            return
        command = header.get("command")
        # Cache hw_id / serial for any inbound message — useful for publishes.
        hw_id = header.get("hw_id")
        serial = header.get("serial_number")
        sub_id = body.get("sub_device_id") if isinstance(body, dict) else None

        changed = False
        for ea_id in self._gateways[gateway_id].ea_ids:
            st = self._states[ea_id]
            info = self._infos[ea_id]
            if hw_id and st.gateway_hw_id != hw_id:
                st.gateway_hw_id = hw_id
                changed = True
            if serial and st.gateway_serial != serial:
                st.gateway_serial = serial
                changed = True
            if sub_id and st.sub_device_id != sub_id:
                # Trust ihd_10min / cur_consump messages over our reconstruction.
                st.sub_device_id = sub_id
                changed = True
            elif st.sub_device_id is None and st.gateway_hw_id:
                built = _build_sub_device_id(st.gateway_hw_id, info.mac_address)
                if built:
                    st.sub_device_id = built
                    changed = True

        if command == "ihd_get_param" and body.get("key") == "cur_consump":
            for ea_id in self._gateways[gateway_id].ea_ids:
                st = self._states[ea_id]
                if not sub_id or st.sub_device_id != sub_id:
                    continue
                value = body.get("value")
                if isinstance(value, (int, float)):
                    st.power_w = int(value)
                    st.last_seen = dt_util.utcnow()
                    changed = True
        elif command == "ihd_10min":
            self._apply_ten_minute(gateway_id, body)
            changed = True

        if changed:
            self._hass.loop.call_soon_threadsafe(self._dispatch)

    def _apply_ten_minute(self, gateway_id: str, body: dict[str, Any]) -> None:
        flashes = body.get("flashes")
        sub_id = body.get("sub_device_id")
        end_str = body.get("end_time")
        if not isinstance(flashes, (int, float)) or not isinstance(sub_id, str):
            return
        try:
            end_dt = (
                datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")
                if isinstance(end_str, str)
                else None
            )
        except ValueError:
            end_dt = None
        # Treat the bin's end time as local — the cloud reports local time.
        end_local = dt_util.as_local(end_dt) if end_dt else dt_util.now()
        bin_date = end_local.date()

        for ea_id in self._gateways[gateway_id].ea_ids:
            st = self._states[ea_id]
            if st.sub_device_id and st.sub_device_id != sub_id:
                continue
            info = self._infos[ea_id]
            kwh = _flashes_to_kwh(int(flashes), info.impulse_rate)
            if st.today != bin_date:
                # Rolled over — reset the running total and the watermark so
                # the daily energy sensor reflects only this date.
                st.today = bin_date
                st.energy_today_kwh = 0.0
                st.counted_through = None
            elif st.counted_through and end_local <= st.counted_through:
                # Already counted via REST seed or a prior MQTT delivery; skip
                # to avoid double-counting backlog uploads or QoS-1 retries.
                continue
            st.energy_today_kwh = round(st.energy_today_kwh + kwh, 4)
            st.counted_through = end_local
            st.latest_bin_kwh = round(kwh, 4)
            st.latest_bin_end = (
                dt_util.as_utc(end_local) if end_dt else dt_util.utcnow()
            )
            st.last_seen = dt_util.utcnow()

    @callback
    def _dispatch(self) -> None:
        if self._on_update is not None:
            self._on_update()

    # ---- polling tick (HA event loop) -------------------------------------

    async def _async_poll_tick(self, _now: datetime) -> None:
        if self._is_stale():
            await self._async_force_reconnect()
            return  # next tick resumes normal polling on the fresh connection
        for ea_id in self._infos:
            await self._hass.async_add_executor_job(self._publish_cur_consump, ea_id)

    def _is_stale(self) -> bool:
        if self._reconnect_in_progress:
            return False
        if self._client is None or self._last_inbound_monotonic is None:
            return True
        elapsed = time.monotonic() - self._last_inbound_monotonic
        return elapsed > IHD_STALE_RECONNECT_AFTER.total_seconds()

    async def _async_force_reconnect(self) -> None:
        if self._reconnect_in_progress:
            return
        self._reconnect_in_progress = True
        try:
            elapsed: int | None = (
                None
                if self._last_inbound_monotonic is None
                else int(time.monotonic() - self._last_inbound_monotonic)
            )
            _LOGGER.warning(
                "ihd: no inbound MQTT traffic for %ss; tearing down and reconnecting",
                elapsed,
            )
            await self._hass.async_add_executor_job(self._disconnect_blocking)
            try:
                await self._hass.async_add_executor_job(self._connect_blocking)
            except Exception as exc:  # noqa: BLE001 — retry on the next tick
                _LOGGER.warning(
                    "ihd: reconnect failed (%s) — will retry next tick", exc
                )
                return
            await self._hass.async_add_executor_job(self._publish_get_gw_info_all)
            _LOGGER.info("ihd: reconnected")
        finally:
            self._reconnect_in_progress = False


def _flashes_to_kwh(flashes: int, impulse_rate: int | None) -> float:
    """Convert a flashes count to kWh.

    The API exposes `impulse_rate` with type "Wh/imp" but the field actually
    encodes impulses per kWh (e.g. 1000 = 1000 imp/kWh = 1 Wh/imp). For an
    unknown rate we assume the typical Australian 1000 imp/kWh.
    """
    rate = impulse_rate or 1000
    if rate <= 0:
        rate = 1000
    return flashes / rate


def _build_sub_device_id(gateway_hw_id: str, ea_mac: str) -> str | None:
    """Construct an `EIAdv-{LL_HW}-{EA_MAC}` identifier matching the wire format.

    Both halves are 12 hex chars rendered as three uppercase groups of four
    separated by colons (e.g. `0CDC:7EDA:DEA8`).
    """
    ll = _normalise_mac(gateway_hw_id)
    ea = _normalise_mac(ea_mac)
    if ll is None or ea is None:
        return None
    return f"EIAdv-{ll}-{ea}"


def _normalise_mac(raw: str | None) -> str | None:
    if not raw:
        return None
    hex_only = "".join(c for c in raw if c.isalnum()).upper()
    if len(hex_only) != 12 or any(c not in "0123456789ABCDEF" for c in hex_only):
        return None
    return f"{hex_only[0:4]}:{hex_only[4:8]}:{hex_only[8:12]}"

"""Async REST client for the Emerald cloud API.

Endpoints in scope:
  - POST /api/v1/customer/sign-in                          (login, 24h JWT)
  - POST /api/v1/customer/token-refresh                    (refresh JWT)
  - GET  /api/v1/customer/property/list                    (discovery)
  - GET  /api/v1/customer/device/get-by-date/flashes-data  (EA daily total —
                                                            used only to seed
                                                            today's running
                                                            total at startup)

Live runtime state (heat pump and Electricity Advisor / LiveLink) arrives via
MQTT. Heat-pump MQTT lives in the vendored `emerald_hws` library; LiveLink
MQTT (topics `ep/ihd/...`) is handled in `ihd.py`. The flashes-data endpoint
is consulted once per integration setup to backfill the day's energy use that
happened before the process was running.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://api.emerald-ems.com.au/api/v1"
DEFAULT_HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "user-agent": "ha-emerald/0.1 (HomeAssistant)",
}


class EmeraldAuthError(Exception):
    """Credentials rejected (HTTP 401 or non-200 response code)."""


class EmeraldApiError(Exception):
    """Network failure, 5xx, or malformed payload."""


@dataclass(slots=True)
class HeatPumpInfo:
    """Static metadata for a heat pump (controllable state arrives via MQTT)."""

    id: str
    property_id: str
    serial_number: str
    mac_address: str
    brand: str
    model: str
    hw_version: str
    soft_version: str


@dataclass(slots=True)
class ElectricityAdvisorInfo:
    """Static metadata for an Electricity Advisor (live data via MQTT push)."""

    id: str
    property_id: str
    serial_number: str
    mac_address: str
    gateway_id: str  # LiveLink UUID — used as MQTT device_id in topic ep/ihd/+/{gateway_id}
    name: str
    nmi: str | None
    impulse_rate: int | None  # field is labelled "Wh/imp" but is actually imp/kWh


@dataclass(slots=True)
class Discovery:
    heat_pumps: list[HeatPumpInfo]
    electricity_advisors: list[ElectricityAdvisorInfo]


class EmeraldRestClient:
    """Stateful async client. Holds the JWT in memory; re-logs on 401."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._token: str | None = None

    async def async_login(self) -> None:
        payload = {
            "app_version": "2.5.3",
            "device_name": "HomeAssistant",
            "device_os_version": "linux",
            "device_type": "iOS",
            "email": self._email,
            "password": self._password,
        }
        body = await self._post("/customer/sign-in", payload, authed=False)
        token = body.get("token")
        if not token:
            raise EmeraldAuthError("login response missing token")
        self._token = token

    async def async_discover(self) -> Discovery:
        body = await self._get("/customer/property/list")
        info = body.get("info") or {}
        properties = list(info.get("property", [])) + list(
            info.get("shared_property", [])
        )

        heat_pumps: list[HeatPumpInfo] = []
        advisors: list[ElectricityAdvisorInfo] = []
        for prop in properties:
            for hp in prop.get("heat_pump", []) or []:
                heat_pumps.append(
                    HeatPumpInfo(
                        id=hp["id"],
                        property_id=hp.get("property_id", prop["id"]),
                        serial_number=hp.get("serial_number", ""),
                        mac_address=hp.get("mac_address", ""),
                        brand=hp.get("brand", "Emerald"),
                        model=hp.get("model", ""),
                        hw_version=hp.get("hw_version", ""),
                        soft_version=hp.get("soft_version", ""),
                    )
                )
            for dev in prop.get("devices", []) or []:
                if dev.get("device_category") != "Electricity Advisor":
                    continue
                gateway_id = dev.get("gateway_conn_id")
                if not gateway_id:
                    # No gateway means no MQTT path; skip rather than half-register.
                    continue
                advisors.append(
                    ElectricityAdvisorInfo(
                        id=dev["id"],
                        property_id=dev.get("property_id", prop["id"]),
                        serial_number=dev.get("serial_number", ""),
                        mac_address=dev.get("device_mac_address", ""),
                        gateway_id=gateway_id,
                        name=dev.get("device_name", "Electricity Advisor"),
                        nmi=dev.get("nmi") or dev.get("NMI"),
                        impulse_rate=dev.get("impulse_rate"),
                    )
                )
        return Discovery(heat_pumps=heat_pumps, electricity_advisors=advisors)

    async def async_get_today_kwh(
        self, device_id: str, day: date
    ) -> float | None:
        """Return total_kwh_of_day for an EA on `day`, or None if the cloud
        has nothing for that date yet.

        Used at integration startup to seed `IhdState.energy_today_kwh` so
        the daily energy sensor reflects the day so far rather than only
        what we observe via MQTT after this process started.
        """
        iso = day.isoformat()
        body = await self._get(
            "/customer/device/get-by-date/flashes-data",
            params={"device_id": device_id, "start_date": iso, "end_date": iso},
        )
        info = body.get("info") or {}
        for d in info.get("daily_consumptions") or []:
            if d.get("date_string") == iso:
                total = d.get("total_kwh_of_day")
                return float(total) if total is not None else None
        return None

    async def _get(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        return await self._request("GET", path, params=params, authed=True)

    async def _post(
        self, path: str, json: dict[str, Any], *, authed: bool
    ) -> dict[str, Any]:
        return await self._request("POST", path, json=json, authed=authed)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        authed: bool,
        _retry: bool = True,
    ) -> dict[str, Any]:
        if authed and self._token is None:
            await self.async_login()

        headers = dict(DEFAULT_HEADERS)
        if authed and self._token:
            headers["authorization"] = f"Bearer {self._token}"

        try:
            async with self._session.request(
                method,
                f"{API_BASE}{path}",
                json=json,
                params=params,
                headers=headers,
            ) as resp:
                if resp.status == 401 and authed and _retry:
                    self._token = None
                    return await self._request(
                        method,
                        path,
                        json=json,
                        params=params,
                        authed=authed,
                        _retry=False,
                    )
                if resp.status >= 500:
                    raise EmeraldApiError(f"server error {resp.status}")
                body = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise EmeraldApiError(str(err)) from err

        code = body.get("code")
        if code == 401 or (authed and code == 403):
            raise EmeraldAuthError(body.get("message", "unauthorized"))
        if code != 200:
            raise EmeraldApiError(f"api code={code}: {body.get('message')}")
        return body

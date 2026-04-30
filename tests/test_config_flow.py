from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.emerald.api import EmeraldApiError, EmeraldAuthError
from custom_components.emerald.const import CONF_PASSWORD, CONF_USERNAME, DOMAIN

USER_INPUT = {
    CONF_USERNAME: "alice@example.com",
    CONF_PASSWORD: "hunter2",
}


async def test_user_flow_success(hass: HomeAssistant, mock_rest_client) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == USER_INPUT[CONF_USERNAME]
    assert result["data"] == USER_INPUT


async def test_user_flow_invalid_auth(
    hass: HomeAssistant, mock_rest_client
) -> None:
    mock_rest_client.async_login.side_effect = EmeraldAuthError("bad password")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(
    hass: HomeAssistant, mock_rest_client
) -> None:
    mock_rest_client.async_login.side_effect = EmeraldApiError("network down")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_duplicate(
    hass: HomeAssistant, mock_rest_client
) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=USER_INPUT[CONF_USERNAME].lower(),
        data=USER_INPUT,
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"

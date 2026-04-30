from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    yield


@pytest.fixture
def mock_rest_client() -> Generator[AsyncMock]:
    """Patch EmeraldRestClient where the config flow imports it."""
    with patch(
        "custom_components.emerald.config_flow.EmeraldRestClient", autospec=True
    ) as mock_class:
        instance = mock_class.return_value
        instance.async_login = AsyncMock(return_value=None)
        yield instance

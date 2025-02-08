"""Test client connector."""

import ipaddress
from unittest.mock import AsyncMock, patch

from snitun.client.connector import Connector
from snitun.multiplexer.core import Multiplexer

IP_ADDR = ipaddress.ip_address("8.8.8.8")
BAD_ADDR = ipaddress.ip_address("8.8.1.1")


async def test_connector_error_callback(
    multiplexer_client: Multiplexer,
    multiplexer_server: Multiplexer,
) -> None:
    """Test connector endpoint error callback."""
    callback = AsyncMock()
    connector = Connector("127.0.0.1", "8822", False, callback)

    channel = await multiplexer_server.create_channel(IP_ADDR)

    callback.assert_not_called()

    with patch("asyncio.open_connection", side_effect=OSError("Lorem ipsum...")):
        await connector.handler(multiplexer_client, channel)

    callback.assert_called_once()

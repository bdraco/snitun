"""Test client connector."""

import asyncio
from contextlib import suppress
import ipaddress
import ssl
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import ClientRequest, ClientTimeout
from aiohttp.client_proto import ResponseHandler
from aiohttp.connector import BaseConnector

if TYPE_CHECKING:
    from aiohttp.tracing import Trace

from snitun.client.connector import ChannelTransport, Connector
from snitun.multiplexer.core import Multiplexer
from snitun.utils.asyncio import create_eager_task

IP_ADDR = ipaddress.ip_address("8.8.8.8")
BAD_ADDR = ipaddress.ip_address("8.8.1.1")


class ResponseHandlerWithTransportReader(ResponseHandler):
    """Response handler with transport reader."""

    def __init__(
        self,
        channel_transport: ChannelTransport,
    ) -> None:
        super().__init__(loop=asyncio.get_running_loop())
        self._channel_transport = channel_transport
        self._transport_reader_task = create_eager_task(
            channel_transport.start(),
            name="TransportReaderTask",
        )

    def close(self) -> None:
        """Close connection."""
        super().close()
        self._channel_transport.close()
        self._transport_reader_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            self._transport_reader_task.exception()


class ChannelConnector(BaseConnector):
    """Channel connector."""

    def __init__(
        self,
        multiplexer_server: Multiplexer,
        ssl_context: ssl.SSLContext,
    ) -> None:
        """Initialize connector."""
        super().__init__()
        self._multiplexer_server = multiplexer_server
        self._ssl_context = ssl_context

    async def _create_connection(
        self,
        req: ClientRequest,
        traces: list["Trace"],
        timeout: "ClientTimeout",
    ) -> ResponseHandler:
        """Create connection."""
        channel = await self._multiplexer_server.create_channel(IP_ADDR)
        transport = ChannelTransport(channel)
        protocol = ResponseHandlerWithTransportReader(channel_transport=transport)
        new_transport = await self._loop.start_tls(
            transport,
            protocol,
            self._ssl_context,
            server_side=False,
        )
        protocol.connection_made(new_transport)
        return protocol


async def test_connector_non_existent_url(
    multiplexer_client: Multiplexer,
    multiplexer_server: Multiplexer,
    connector: Connector,
    client_ssl_context: ssl.SSLContext,
) -> None:
    """End to end test that connector can fetch a non-existent URL."""
    multiplexer_client._new_connections = connector.handler
    connector = ChannelConnector(multiplexer_server, client_ssl_context)
    session = aiohttp.ClientSession(connector=connector)
    response = await session.get("https://localhost:4242/does-not-exist")
    assert response.status == 404
    await session.close()


async def test_connector_valid_url(
    multiplexer_client: Multiplexer,
    multiplexer_server: Multiplexer,
    connector: Connector,
    client_ssl_context: ssl.SSLContext,
) -> None:
    """End to end test that connector can fetch a non-existent URL."""
    multiplexer_client._new_connections = connector.handler
    connector = ChannelConnector(multiplexer_server, client_ssl_context)
    session = aiohttp.ClientSession(connector=connector)
    response = await session.get("https://localhost:4242/")
    assert response.status == 200
    content = await response.read()
    assert content == b"Hello world"
    await session.close()

"""Test client connector."""

import asyncio
from contextlib import suppress
import ipaddress
import ssl
from typing import Any

import aiohttp

from snitun.client.connector import ChannelTransport, Connector
from snitun.multiplexer.core import Multiplexer
from snitun.utils.aiohttp_client import SniTunClientAioHttp
from snitun.utils.asyncio import create_eager_task

IP_ADDR = ipaddress.ip_address("8.8.8.8")
BAD_ADDR = ipaddress.ip_address("8.8.1.1")

from functools import partial

from aiohttp import ClientRequest, ClientTimeout
from aiohttp.client_proto import ResponseHandler
from aiohttp.connector import BaseConnector
from aiohttp.tracing import Trace


class ResponseHandlerWithTransportReader(ResponseHandler):
    """Response handler with transport reader."""

    def __init__(
        self,
        *args: Any,
        channel_transport: ChannelTransport,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args,loop=asyncio.get_running_loop(), **kwargs)
        self._transport_reader_task = create_eager_task(
            channel_transport.start(),
            name="TransportReaderTask",
        )

    def close(self) -> None:
        """Close connection."""
        super().close()
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
        await self._loop.start_tls(
            transport,
            protocol,
            self._ssl_context,
            server_side=False,
            ssl_handshake_timeout=0.1,
            ssl_shutdown_timeout=0.1,
        )
        return protocol


async def test_init_connector(
    multiplexer_client: Multiplexer,
    multiplexer_server: Multiplexer,
    snitun_client_aiohttp: SniTunClientAioHttp,
    connector: Connector,
    client_ssl_context: ssl.SSLContext,
) -> None:
    """Test and init a connector."""
    multiplexer_client._new_connections = connector.handler

    connector = ChannelConnector(multiplexer_server, client_ssl_context)
    session = aiohttp.ClientSession(connector=connector)
    response = await session.get("https://localhost:4242")
    assert response.status == 200
    await asyncio.sleep(1)

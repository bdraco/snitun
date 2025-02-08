"""Connector to end resource."""

from __future__ import annotations

import asyncio
import asyncio.sslproto
from collections.abc import Callable, Coroutine
from contextlib import suppress
import ipaddress
import logging
from ssl import SSLContext, SSLError
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiohttp.web import RequestHandler

from ..exceptions import MultiplexerTransportClose, MultiplexerTransportError
from ..multiplexer.channel import MultiplexerChannel
from ..multiplexer.core import Multiplexer
from ..multiplexer.transport import ChannelTransport
from ..utils.asyncio import create_eager_task

_LOGGER = logging.getLogger(__name__)


async def _cancel_transport_reader_task(
    transport_reader_task: asyncio.Task[None],
) -> None:
    """Cancel the transport reader task."""
    transport_reader_task.cancel()
    try:
        await transport_reader_task
    except asyncio.CancelledError:
        # Don't swallow cancellation
        if (
            sys.version_info >= (3, 11)
            and (current_task := asyncio.current_task())
            and current_task.cancelling()
        ):
            raise
    except MultiplexerTransportClose:
        pass
    except Exception:
        _LOGGER.exception("Error in transport_reader_task")


class Connector:
    """Connector to end resource."""

    def __init__(
        self,
        protocol_factory: Callable[[], RequestHandler],
        ssl_context: SSLContext,
        whitelist: bool = False,
        endpoint_connection_error_callback: Coroutine[Any, Any, None] | None = None,
    ) -> None:
        """Initialize Connector."""
        self._loop = asyncio.get_running_loop()
        self._whitelist: set[ipaddress.IPv4Address] = set()
        self._whitelist_enabled = whitelist
        self._endpoint_connection_error_callback = endpoint_connection_error_callback
        self._protocol_factory = protocol_factory
        self._ssl_context = ssl_context

    @property
    def whitelist(self) -> set:
        """Allow to block requests per IP Return None or access to a set."""
        return self._whitelist

    def _whitelist_policy(self, ip_address: ipaddress.IPv4Address) -> bool:
        """Return True if the ip address can access to endpoint."""
        return not self._whitelist_enabled or ip_address in self._whitelist

    async def handler(
        self,
        multiplexer: Multiplexer,
        channel: MultiplexerChannel,
    ) -> None:
        """Handle new connection from SNIProxy."""
        _LOGGER.debug("New connection from %s", channel.ip_address)

        # Check policy
        if not self._whitelist_policy(channel.ip_address):
            _LOGGER.warning("Block request from %s per policy", channel.ip_address)
            await multiplexer.delete_channel(channel)
            return

        transport = ChannelTransport(channel)
        # The request_handler is the aiohttp RequestHandler
        # that is generated from the protocol_factory that
        # was passed in the constructor.
        request_handler = self._protocol_factory()
        _LOGGER.debug("Request handler created for %s", channel.id)
        transport_reader_task = create_eager_task(
            transport.start(),
            name="TransportReaderTask",
            loop=self._loop,
        )
        _LOGGER.debug("Started transport reader task for %s", channel.id)

        # Upgrade the transport to TLS
        try:
            new_transport = await self._loop.start_tls(
                transport,
                request_handler,
                self._ssl_context,
                server_side=True,
            )
        except (OSError, SSLError) as ex:
            # This can can be just about any error, but mostly likely it's a TLS error
            # or the connection gets dropped in the middle of the handshake
            _LOGGER.debug(
                "Cannot start TLS for %s (%s): %s",
                channel.ip_address,
                channel.id,
                ex,
            )
            with suppress(MultiplexerTransportError):
                await multiplexer.delete_channel(channel)
            await _cancel_transport_reader_task(transport_reader_task)
            return

        # Now that we have the connection upgraded to TLS, we can
        # start the request handler and serve the connection.
        _LOGGER.info("Connected peer: %s (%s)", channel.ip_address, channel.id)
        try:
            request_handler.connection_made(new_transport)
            await transport_reader_task
        except (MultiplexerTransportError, OSError, RuntimeError) as ex:
            _LOGGER.debug(
                "Transport error for %s (%s)",
                channel.ip_address,
                channel.id,
            )
            with suppress(MultiplexerTransportError):
                await multiplexer.delete_channel(channel)
            request_handler.connection_lost(ex)
        else:
            _LOGGER.debug(
                "Peer close connection for %s (%s)",
                channel.ip_address,
                channel.id,
            )
            request_handler.connection_lost(None)
        finally:
            new_transport.close()

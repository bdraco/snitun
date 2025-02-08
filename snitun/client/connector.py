"""Connector to end resource."""

from __future__ import annotations

import asyncio
from asyncio import BufferedProtocol, Transport
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
from ..utils.asyncio import create_eager_task

_LOGGER = logging.getLogger(__name__)


class ChannelTransport(Transport):
    """An asyncio.Transport implementation for multiplexer channel."""

    _start_tls_compatible = True

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        channel: MultiplexerChannel,
    ) -> None:
        """Initialize ChannelTransport."""
        self._channel = channel
        self._loop = loop
        self._protocol: asyncio.sslproto.SSLProtocol | None = None
        self._pause_future: asyncio.Future[None] | None = None
        super().__init__(extra={"peername": (str(channel.ip_address), 0)})

    def get_protocol(self) -> asyncio.Protocol:
        """Return the protocol."""
        return self._protocol

    def set_protocol(self, protocol: asyncio.Protocol) -> None:
        """Set the protocol."""
        if not isinstance(protocol, BufferedProtocol):
            raise TypeError("Protocol must be a BufferedProtocol")
        self._protocol = protocol

    def is_closing(self) -> bool:
        """Return True if the transport is closing or closed."""
        return self._channel.closing

    def close(self) -> None:
        """Close the underlying channel."""
        self._channel.close()
        self._release_pause_future()

    def write(self, data: bytes) -> None:
        """Write data to the channel."""
        if not self._channel.closing:
            self._channel.write_no_wait(data)

    async def start(self) -> None:
        """Start reading from the channel.

        Here we read the SSL data from the channel and pass it to the protocol.

        As a future improvement, it would be a bit more efficient to
        have the channel call this as a callback from channel.message_transport.
        """
        while True:
            if self._pause_future:
                await self._pause_future

            try:
                from_peer = await self._channel.read()
            except MultiplexerTransportClose as exc:
                self._force_close(exc)
                raise
            except (SystemExit, KeyboardInterrupt):
                raise
            except BaseException as exc:
                self._fatal_error(exc, "Fatal error: channel.read() call failed.")
                raise

            # This is nearly the same approach as
            # asyncio._SelectorSocketTransport._read_ready__get_buffer
            # https://github.com/python/cpython/blob/365cf5fc23835fa6dc8608396109085f31d2d5f0/Lib/asyncio/selector_events.py#L972
            peer_payload_len = len(from_peer)
            try:
                buf = self._protocol.get_buffer(-1)
            except (SystemExit, KeyboardInterrupt):
                raise
            except BaseException as exc:
                self._fatal_error(
                    exc,
                    "Fatal error: protocol.get_buffer() call failed.",
                )
                raise

            if not (available_len := len(buf)):
                exc = RuntimeError("get_buffer() returned an empty buffer")
                self._fatal_error(
                    exc,
                    "Fatal error: get_buffer() returned an empty buffer",
                )
                raise exc

            if available_len < peer_payload_len:
                exc = RuntimeError(
                    "Available buffer is %s, need %s",
                    available_len,
                    peer_payload_len,
                )
                self._fatal_error(
                    exc,
                    f"Fatal error: out of buffer need {peer_payload_len}"
                    f" bytes but only have {available_len} bytes",
                )
                raise exc

            try:
                buf[:peer_payload_len] = from_peer
            except (SystemExit, KeyboardInterrupt):
                raise
            except BaseException as exc:
                self._fatal_error(exc, "Fatal error consuming buffer from peer.")
                raise

            try:
                self._protocol.buffer_updated(peer_payload_len)
            except (SystemExit, KeyboardInterrupt):
                raise
            except BaseException as exc:
                self._fatal_error(
                    exc,
                    "Fatal error: protocol.buffer_updated() call failed.",
                )
                raise

    def _force_close(self, exc: Exception) -> None:
        """Force close the transport."""
        self._channel.close()
        self._release_pause_future()
        if self._protocol is not None:
            self._loop.call_soon(self._protocol.connection_lost, exc)

    def _fatal_error(self, exc: Exception, message: str) -> None:
        """Handle a fatal error."""
        self._loop.call_exception_handler(
            {
                "message": message,
                "exception": exc,
                "transport": self,
                "protocol": self._protocol,
            },
        )
        self._force_close(exc)

    def is_reading(self) -> bool:
        """Return True if the transport is receiving."""
        return self._pause_future is not None

    def pause_reading(self) -> None:
        """Pause the receiving end.

        No data will be passed to the protocol's data_received()
        method until resume_reading() is called.
        """
        if self._pause_future is not None:
            return
        self._pause_future = self._loop.create_future()

    def resume_reading(self) -> None:
        """Resume the receiving end.

        Data received will once again be passed to the protocol's
        data_received() method.
        """
        self._release_pause_future()

    def _release_pause_future(self) -> None:
        """Release the pause future, if it exists.

        This will ensure that start can continue processing data.
        """
        if self._pause_future is not None and not self._pause_future.done():
            self._pause_future.set_result(None)
        self._pause_future = None


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
        self._whitelist = set()
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
        if self._whitelist_enabled:
            return ip_address in self._whitelist
        return True

    async def _start_tls(
        self,
        transport: ChannelTransport,
        request_handler: RequestHandler,
        multiplexer: Multiplexer,
        channel: MultiplexerChannel,
        transport_reader_task: asyncio.Task[None],
    ) -> asyncio.Transport | None:
        """Start TLS on the transport."""
        try:
            return await self._loop.start_tls(
                transport,
                request_handler,
                self._ssl_context,
                server_side=True,
            )
        except (OSError, SSLError):
            # This can can be just about any error, but mostly likely it's a TLS error
            # or the connection gets dropped in the middle of the handshake
            _LOGGER.debug("Can't start TLS for %s", channel.id, exc_info=True)
            transport_reader_task.cancel()
            await multiplexer.delete_channel(channel)
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
            return None

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

        transport = ChannelTransport(self._loop, channel)
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
        if not (
            new_transport := await self._start_tls(
                transport,
                request_handler,
                multiplexer,
                channel,
                transport_reader_task,
            )
        ):
            return

        request_handler.connection_made(new_transport)
        _LOGGER.info("Connected peer: %s", new_transport.get_extra_info("peername"))
        try:
            await transport_reader_task
        except (MultiplexerTransportError, OSError, RuntimeError) as ex:
            _LOGGER.debug("Transport closed by endpoint for %s", channel.id)
            with suppress(MultiplexerTransportError):
                await multiplexer.delete_channel(channel)
            request_handler.connection_lost(ex)
        except MultiplexerTransportClose as ex:
            _LOGGER.debug("Peer close connection for %s", channel.id)
            request_handler.connection_lost(ex)
        else:
            request_handler.connection_lost(None)
        finally:
            new_transport.close()

"""Connector to end resource."""

from __future__ import annotations

import asyncio
from asyncio import BufferedProtocol, Transport
import asyncio.sslproto
import logging

from ..exceptions import MultiplexerTransportClose
from ..multiplexer.channel import MultiplexerChannel

_LOGGER = logging.getLogger(__name__)


class ChannelTransport(Transport):
    """An asyncio.Transport implementation for multiplexer channel."""

    _start_tls_compatible = True

    def __init__(self, channel: MultiplexerChannel) -> None:
        """Initialize ChannelTransport."""
        self._channel = channel
        self._loop = asyncio.get_running_loop()
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
                return  # normal close
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
        if self._pause_future is None:
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

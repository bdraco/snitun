"""Connector to end resource."""
from __future__ import annotations

import asyncio
from asyncio import Transport
from collections.abc import Callable, Coroutine
from contextlib import suppress
import ipaddress
import logging
from ssl import SSLContext
from typing import Any

from aiohttp.web import RequestHandler

from ..exceptions import MultiplexerTransportClose, MultiplexerTransportError
from ..multiplexer.channel import MultiplexerChannel
from ..multiplexer.core import Multiplexer

_LOGGER = logging.getLogger(__name__)



class ChannelTransport(Transport):

    _start_tls_compatible = True

    def __init__(self, loop: asyncio.AbstractEventLoop, channel: MultiplexerChannel) -> None:
        self._ip_address = channel.ip_address
        self._channel = channel
        self._loop = loop
        self._protocol = None
        self._paused = False
        self._pause_future = None
        super().__init__(extra={"peername": (str(self._ip_address), 0)})

    def get_protocol(self) -> asyncio.Protocol:
        _LOGGER.warning("Get protocol: %s", self._protocol)
        return self._protocol

    def set_protocol(self, protocol: asyncio.Protocol) -> None:
        _LOGGER.warning("Set protocol: %s", protocol)
        self._protocol = protocol

    def is_closing(self) -> bool:
        return False

    def close(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        #_LOGGER.warning("Write data: %s", data)
        self._channel.write_sync(data)

    async def start(self):
        _LOGGER.warning("Start transport")
        try:
            # Process stream from multiplexer
            while True:
                if self._pause_future:
                    _LOGGER.warning("Pause future")
                    await self._pause_future
                    _LOGGER.warning("Pause future done")
                from_peer = await self._channel.read()
                peer_payload_len = len(from_peer)
                try:
                    buf = self._protocol.get_buffer(peer_payload_len)
                    if not (available_len := len(buf)):
                        raise RuntimeError("get_buffer() returned an empty buffer")
                except (SystemExit, KeyboardInterrupt):
                    raise
                except BaseException as exc:
                    self._fatal_error(
                        exc, "Fatal error: protocol.get_buffer() call failed.")
                    return

                if available_len < peer_payload_len:
                    self._fatal_error(
                        ValueError(), "Fatal error: out of buffer")
                    return

#                print(['available', available_len])
#                print(['to_write', to_write])
#                print(['buf', buf, buf[:]])
#                print(['from_peer', from_peer, from_peer[:to_write]])
                try:
                    buf[:peer_payload_len] = from_peer #[:peer_payload_len]
                except (BlockingIOError, InterruptedError):
                    return
                except (SystemExit, KeyboardInterrupt):
                    raise
                except BaseException as exc:
                    self._fatal_error(exc, "Fatal read error on socket transport")
                    return

                try:
                    self._protocol.buffer_updated(peer_payload_len)
                except (SystemExit, KeyboardInterrupt):
                    raise
                except BaseException as exc:
                    self._fatal_error(
                        exc, "Fatal error: protocol.buffer_updated() call failed.")
        except (MultiplexerTransportError, OSError, RuntimeError, Exception):
            _LOGGER.exception("Transport closed by endpoint for %s", self._channel.id)

    def _force_close(self, exc):
        _LOGGER.exception("Force close")
        self._channel.close()
        self._loop.call_soon(self._protocol.connection_lost, exc)

    def _fatal_error(self, exc, message):
        _LOGGER.error(message)
        _LOGGER.exception(exc)
        self._channel.close()
        self._loop.call_soon(self._protocol.connection_lost,exc)

    def is_reading(self):
        """Return True if the transport is receiving."""
        _LOGGER.warning("Is reading")
        return self._pause_future is not None

    def pause_reading(self):
        """Pause the receiving end.

        No data will be passed to the protocol's data_received()
        method until resume_reading() is called.
        """
        _LOGGER.error("Pause reading")
        self._pause_future = self._loop.create_future()

    def resume_reading(self):
        """Resume the receiving end.

        Data received will once again be passed to the protocol's
        data_received() method.
        """
        _LOGGER.error("Resume reading")
        if not self._pause_future.done():
            self._pause_future.set_result(None)
        self._pause_future = None


class Connector:
    """Connector to end resource."""

    def __init__(
        self,
        end_host: str,
        end_port: int | None=None,
        whitelist: bool=False,
        endpoint_connection_error_callback: Coroutine[Any, Any, None] | None = None,
        protocol_factory: Any = None,
        ssl_context: SSLContext = None,
    ) -> None:
        """Initialize Connector."""
        self._loop = asyncio.get_event_loop()
        self._end_host = end_host
        self._end_port = end_port or 443
        self._whitelist = set()
        self._whitelist_enabled = whitelist
        self._endpoint_connection_error_callback = endpoint_connection_error_callback
        self._protocol_factory: Callable[[], RequestHandler] = protocol_factory
        self._ssl_context: SSLContext = ssl_context

    @property
    def whitelist(self) -> set:
        """Allow to block requests per IP Return None or access to a set."""
        return self._whitelist

    def _whitelist_policy(self, ip_address: ipaddress.IPv4Address) -> bool:
        """Return True if the ip address can access to endpoint."""
        if self._whitelist_enabled:
            return ip_address in self._whitelist
        return True

    async def handler(
        self, multiplexer: Multiplexer, channel: MultiplexerChannel,
    ) -> None:
        """Handle new connection from SNIProxy."""
        _LOGGER.debug(
            "Receive from %s a request for %s", channel.ip_address, self._end_host,
        )

        # Check policy
        if not self._whitelist_policy(channel.ip_address):
            _LOGGER.warning("Block request from %s per policy", channel.ip_address)
            await multiplexer.delete_channel(channel)
            return

        transport = ChannelTransport(self._loop, channel)
        loop = asyncio.get_running_loop()
        request_handler = self._protocol_factory()
        reader = asyncio.create_task(transport.start())
        # Open connection to endpoint
        try:
            new_transport = await loop.start_tls(
                transport,
                request_handler,
                self._ssl_context,
                server_side=True,
            )
        except OSError:
            _LOGGER.error(
                "Can't connect to endpoint %s:%s", self._end_host, self._end_port,
            )
            await multiplexer.delete_channel(channel)
            if self._endpoint_connection_error_callback:
                await self._endpoint_connection_error_callback()
            return

        request_handler.connection_made(new_transport)
        _LOGGER.warning("Connected peer: %s", new_transport.get_extra_info("peername"))

        try:
            await reader

        except (MultiplexerTransportError, OSError, RuntimeError):
            _LOGGER.exception("Transport closed by endpoint for %s", channel.id)
            with suppress(MultiplexerTransportError):
                await multiplexer.delete_channel(channel)

        except MultiplexerTransportClose:
            _LOGGER.debug("Peer close connection for %s", channel.id)

        finally:
            new_transport.close()

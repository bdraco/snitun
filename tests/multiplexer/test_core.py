"""Tests for core multiplexer handler."""
import asyncio
from unittest.mock import patch

import pytest

from snitun.exceptions import MultiplexerTransportError
from snitun.multiplexer.core import Multiplexer
from snitun.multiplexer.message import CHANNEL_FLOW_PING


async def test_init_multiplexer_server(test_server, test_client):
    """Test to create a new Multiplexer from server socket."""
    client = test_server[0]

    multiplexer = Multiplexer(client.reader, client.writer)

    assert not multiplexer.wait().done()
    await multiplexer.shutdown()
    client.close.set()


async def test_init_multiplexer_client(test_client):
    """Test to create a new Multiplexer from client socket."""
    multiplexer = Multiplexer(test_client.reader, test_client.writer)

    assert not multiplexer.wait().done()
    await multiplexer.shutdown()


async def test_multiplexer_server_close(multiplexer_server, multiplexer_client):
    """Test a close from server peers."""
    assert not multiplexer_server.wait().done()
    assert not multiplexer_client.wait().done()

    await multiplexer_server.shutdown()
    await asyncio.sleep(0.1)

    assert multiplexer_server.wait().done()
    assert multiplexer_client.wait().done()


async def test_multiplexer_client_close(multiplexer_server, multiplexer_client):
    """Test a close from client peers."""
    assert not multiplexer_server.wait().done()
    assert not multiplexer_client.wait().done()

    await multiplexer_client.shutdown()
    await asyncio.sleep(0.1)

    assert multiplexer_server.wait().done()
    assert multiplexer_client.wait().done()


async def test_multiplexer_ping(test_server, multiplexer_client):
    """Test a ping between peers."""
    client = test_server[0]
    multiplexer_client.ping()

    await asyncio.sleep(0.1)

    data = await client.reader.read(60)
    assert data[16] == CHANNEL_FLOW_PING
    assert int.from_bytes(data[17:21], 'big') == 0


async def test_multiplexer_cant_init_channel(multiplexer_client,
                                             multiplexer_server):
    """Test that without new channel callback can't create new channels."""
    assert not multiplexer_client._channels
    assert not multiplexer_server._channels

    # Disable new channels
    multiplexer_server._new_connections = None

    await multiplexer_client.create_channel()
    await asyncio.sleep(0.1)

    assert multiplexer_client._channels
    assert not multiplexer_server._channels


async def test_multiplexer_init_channel(multiplexer_client, multiplexer_server):
    """Test that new channels are created."""
    assert not multiplexer_client._channels
    assert not multiplexer_server._channels

    channel = await multiplexer_client.create_channel()
    await asyncio.sleep(0.1)

    assert multiplexer_client._channels
    assert multiplexer_server._channels

    assert multiplexer_client._channels[channel.uuid]
    assert multiplexer_server._channels[channel.uuid]


async def test_multiplexer_init_channel_full(multiplexer_client, raise_timeout):
    """Test that new channels are created but peer error is available."""
    assert not multiplexer_client._channels

    with pytest.raises(MultiplexerTransportError):
        channel = await multiplexer_client.create_channel()
    await asyncio.sleep(0.1)

    assert not multiplexer_client._channels


async def test_multiplexer_close_channel(multiplexer_client,
                                         multiplexer_server):
    """Test that channels are nice removed."""
    assert not multiplexer_client._channels
    assert not multiplexer_server._channels

    channel = await multiplexer_client.create_channel()
    await asyncio.sleep(0.1)

    assert multiplexer_client._channels
    assert multiplexer_server._channels

    assert multiplexer_client._channels[channel.uuid]
    assert multiplexer_server._channels[channel.uuid]

    await multiplexer_client.delete_channel(channel)
    await asyncio.sleep(0.1)

    assert not multiplexer_client._channels
    assert not multiplexer_server._channels


async def test_multiplexer_close_channel_full(multiplexer_client):
    """Test that channels are nice removed but peer error is available."""
    assert not multiplexer_client._channels

    channel = await multiplexer_client.create_channel()
    await asyncio.sleep(0.1)

    assert multiplexer_client._channels

    with patch('async_timeout.timeout', side_effect=asyncio.TimeoutError()):
        with pytest.raises(MultiplexerTransportError):
            channel = await multiplexer_client.delete_channel(channel)
    await asyncio.sleep(0.1)

    assert not multiplexer_client._channels


async def test_multiplexer_data_channel(multiplexer_client, multiplexer_server):
    """Test that new channels are created."""
    assert not multiplexer_client._channels
    assert not multiplexer_server._channels

    channel_client = await multiplexer_client.create_channel()
    await asyncio.sleep(0.1)

    channel_server = multiplexer_server._channels.get(channel_client.uuid)

    assert channel_client
    assert channel_server

    await channel_client.write(b"test 1")
    await asyncio.sleep(0.1)
    data = await channel_server.read()
    assert data == b"test 1"

    await channel_server.write(b"test 2")
    await asyncio.sleep(0.1)
    data = await channel_client.read()
    assert data == b"test 2"
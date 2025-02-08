"""Test Client Peer connections."""

import asyncio
from datetime import datetime, timedelta, timezone
import ipaddress
import os

import pytest

from snitun.client.client_peer import ClientPeer
from snitun.client.connector import Connector
from snitun.exceptions import SniTunConnectionError
from snitun.server.listener_peer import PeerListener
from snitun.server.peer_manager import PeerManager

from ..server.const_fernet import create_peer_config

IP_ADDR = ipaddress.ip_address("8.8.8.8")


async def test_init_client_peer(
    peer_listener: PeerListener,
    peer_manager: PeerManager,
) -> None:
    """Test setup of ClientPeer."""
    client = ClientPeer("127.0.0.1", "8893")
    connector = Connector("127.0.0.1", "8822")

    assert not client.is_connected
    assert not peer_manager.peer_available("localhost")

    valid = datetime.now(tz=timezone.utc) + timedelta(days=1)
    aes_key = os.urandom(32)
    aes_iv = os.urandom(16)
    hostname = "localhost"
    fernet_token = create_peer_config(valid.timestamp(), hostname, aes_key, aes_iv)

    await client.start(connector, fernet_token, aes_key, aes_iv)
    await asyncio.sleep(0.1)
    assert peer_manager.peer_available("localhost")
    assert client.is_connected
    assert client._multiplexer._throttling is None

    await client.stop()
    await asyncio.sleep(0.1)
    assert not client.is_connected
    assert not peer_manager.peer_available("localhost")


async def test_init_client_peer_with_alias(
    peer_listener: PeerListener,
    peer_manager: PeerManager,
) -> None:
    """Test setup of ClientPeer with custom tomain."""
    client = ClientPeer("127.0.0.1", "8893")
    connector = Connector("127.0.0.1", "8822")

    assert not client.is_connected
    assert not peer_manager.peer_available("localhost")
    assert not peer_manager.peer_available("localhost.custom")

    valid = datetime.now(tz=timezone.utc) + timedelta(days=1)
    aes_key = os.urandom(32)
    aes_iv = os.urandom(16)
    hostname = "localhost"
    fernet_token = create_peer_config(
        valid.timestamp(),
        hostname,
        aes_key,
        aes_iv,
        alias=["localhost.custom"],
    )

    await client.start(connector, fernet_token, aes_key, aes_iv)
    await asyncio.sleep(0.1)
    assert peer_manager.peer_available("localhost")
    assert peer_manager.peer_available("localhost.custom")
    assert client.is_connected
    assert client._multiplexer._throttling is None

    await client.stop()
    await asyncio.sleep(0.1)
    assert not client.is_connected
    assert not peer_manager.peer_available("localhost")
    assert not peer_manager.peer_available("localhost.custom")


async def test_init_client_peer_invalid_token(
    peer_listener: PeerListener,
    peer_manager: PeerManager,
) -> None:
    """Test setup of ClientPeer."""
    client = ClientPeer("127.0.0.1", "8893")
    connector = Connector("127.0.0.1", "8822")

    assert not peer_manager.peer_available("localhost")

    valid = datetime.now(tz=timezone.utc) + timedelta(days=-1)
    aes_key = os.urandom(32)
    aes_iv = os.urandom(16)
    hostname = "localhost"
    fernet_token = create_peer_config(valid.timestamp(), hostname, aes_key, aes_iv)

    with pytest.raises(SniTunConnectionError):
        await client.start(connector, fernet_token, aes_key, aes_iv)
    await asyncio.sleep(0.1)
    assert not peer_manager.peer_available("localhost")


async def test_init_client_peer_wait(
    peer_listener: PeerListener,
    peer_manager: PeerManager,
) -> None:
    """Test setup of ClientPeer."""
    client = ClientPeer("127.0.0.1", "8893")
    connector = Connector("127.0.0.1", "8822")

    assert not client.is_connected
    assert not peer_manager.peer_available("localhost")

    valid = datetime.now(tz=timezone.utc) + timedelta(days=1)
    aes_key = os.urandom(32)
    aes_iv = os.urandom(16)
    hostname = "localhost"
    fernet_token = create_peer_config(valid.timestamp(), hostname, aes_key, aes_iv)

    await client.start(connector, fernet_token, aes_key, aes_iv)
    await asyncio.sleep(0.1)
    assert peer_manager.peer_available("localhost")
    assert client.is_connected

    assert not client.wait().done()

    await client.stop()
    await asyncio.sleep(0.1)
    assert not client.is_connected
    assert not peer_manager.peer_available("localhost")

    with pytest.raises(RuntimeError):
        assert client.wait().done()


async def test_init_client_peer_throttling(
    peer_listener: PeerListener,
    peer_manager: PeerManager,
) -> None:
    """Test setup of ClientPeer."""
    client = ClientPeer("127.0.0.1", "8893")
    connector = Connector("127.0.0.1", "8822")

    assert not client.is_connected
    assert not peer_manager.peer_available("localhost")

    valid = datetime.now(tz=timezone.utc) + timedelta(days=1)
    aes_key = os.urandom(32)
    aes_iv = os.urandom(16)
    hostname = "localhost"
    fernet_token = create_peer_config(valid.timestamp(), hostname, aes_key, aes_iv)

    await client.start(connector, fernet_token, aes_key, aes_iv, throttling=500)
    await asyncio.sleep(0.1)
    assert peer_manager.peer_available("localhost")
    assert client.is_connected
    assert client._multiplexer._throttling == 0.002

    await client.stop()
    await asyncio.sleep(0.1)
    assert not client.is_connected
    assert not peer_manager.peer_available("localhost")

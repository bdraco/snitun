"""Encrypt or Decrypt multiplexer transport data."""

import logging

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..exceptions import MultiplexerTransportDecrypt

_LOGGER = logging.getLogger(__name__)


class CryptoTransport:
    """Encrypt/Decrypt Transport flow."""

    __slots__ = [
        "_cipher",
        "_d_counter",
        "_decrypt_stream",
        "_decryptor",
        "_e_counter",
        "_encrypt_stream",
        "_encryptor",
    ]

    def __init__(self, key: bytes, iv: bytes) -> None:
        """Initialize crypto data."""
        self._cipher = Cipher(
            algorithms.AES(key),
            modes.CBC(iv),
            backend=default_backend(),
        )
        self._encryptor = self._cipher.encryptor()
        self._decryptor = self._cipher.decryptor()
        self._e_counter = 0
        self._d_counter = 0

    def encrypt(self, data: bytes) -> bytes:
        """Encrypt data from transport."""
        enc = self._encryptor.update(data)
        self._e_counter += 1
        _LOGGER.debug("%s: E(%d): %s -> %s", id(self), self._e_counter, data, enc)
        return enc

    def decrypt(self, data: bytes) -> bytes:
        """Decrypt data from transport."""
        self._d_counter += 1
        try:
            dec = self._decryptor.update(data)
        except InvalidTag:
            raise MultiplexerTransportDecrypt from None
        _LOGGER.debug("%s: D(%d): %s -> %s", id(self), self._d_counter, data, dec)
        return dec

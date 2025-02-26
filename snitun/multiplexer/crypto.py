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
        "_decrypt_stream",
        "_decryptor",
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

    def encrypt(self, data: bytes) -> bytes:
        """Encrypt data from transport."""
        return self._encryptor.update(bytes(data))

    def decrypt(self, data: bytes) -> bytes:
        """Decrypt data from transport."""
        try:
            return self._decryptor.update(bytes(data))
        except InvalidTag:
            raise MultiplexerTransportDecrypt from None

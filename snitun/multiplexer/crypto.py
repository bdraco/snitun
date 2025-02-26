"""Encrypt or Decrypt multiplexer transport data."""

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..exceptions import MultiplexerTransportDecrypt


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
        self._decrypt_stream = []
        self._encrypt_stream = []

    def encrypt(self, data: bytes) -> bytes:
        """Encrypt data from transport."""
        self._encrypt_stream.append(data)
        return self._encryptor.update(data)

    def decrypt(self, data: bytes) -> bytes:
        """Decrypt data from transport."""
        try:
            x = self._decryptor.update(data)
        except InvalidTag:
            raise MultiplexerTransportDecrypt from None
        self._decrypt_stream.append(x)
        return x

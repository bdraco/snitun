"""Const value for Fernet tests."""

import json
from typing import List, Optional

from cryptography.fernet import Fernet, MultiFernet

FERNET_TOKENS = [
    "XIKL24X0Fu83UmPLmWkXOBvvqsLq41tz2LljwafDyZw=",
    "ep1FyYA6epwbFxrtEJ2dii5BGvTx5-xU1oUCrF61qMA=",
]


def create_peer_config(
    valid: int,
    hostname: str,
    aes_key: bytes,
    aes_iv: bytes,
    alias: Optional[List[str]] = None,
) -> bytes:
    """Create a fernet token."""
    fernet = MultiFernet([Fernet(key) for key in FERNET_TOKENS])

    return fernet.encrypt(
        json.dumps(
            {
                "valid": valid,
                "hostname": hostname,
                "alias": alias or [],
                "aes_key": aes_key.hex(),
                "aes_iv": aes_iv.hex(),
            }
        ).encode()
    )

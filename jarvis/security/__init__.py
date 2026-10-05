"""Security and Credential Management for Jarvis.

Exports Windows Credential Manager and DPAPI vault primitives, and the
``.env`` credential catalogue the browser interface edits.
"""

from . import api_keys
from .vault import (
    CredentialVault,
    delete_secret,
    dpapi_decrypt,
    dpapi_encrypt,
    get_credential_vault,
    get_secret,
    list_secrets,
    set_secret,
)

__all__ = [
    "api_keys",
    "CredentialVault",
    "get_credential_vault",
    "get_secret",
    "set_secret",
    "delete_secret",
    "list_secrets",
    "dpapi_encrypt",
    "dpapi_decrypt",
]

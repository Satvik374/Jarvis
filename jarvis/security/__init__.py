"""Security and Credential Management for Jarvis.

Exports Windows Credential Manager and DPAPI vault primitives.
"""

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
    "CredentialVault",
    "get_credential_vault",
    "get_secret",
    "set_secret",
    "delete_secret",
    "list_secrets",
    "dpapi_encrypt",
    "dpapi_decrypt",
]

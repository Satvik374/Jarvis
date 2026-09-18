"""Microsoft Azure AI Foundry & Entra ID Authentication Helper.

Provides resilient credential resolution for Azure AI Projects and Azure OpenAI:
- DefaultAzureCredential (supports env vars, managed identity, Azure CLI, Azure Dev CLI)
- InteractiveBrowserCredential fallback (opens browser for one-click Microsoft sign-in)
- Direct API key support via AzureKeyCredential
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

from ..utils import logging as log


class SmartAzureCredential:
    """Resilient TokenCredential that uses DefaultAzureCredential with an
    automatic fallback to InteractiveBrowserCredential when running in an
    interactive user session."""

    def __init__(self, tenant_id: Optional[str] = None):
        from azure.identity import DefaultAzureCredential, TokenCachePersistenceOptions
        self.tenant_id = tenant_id or os.environ.get("AZURE_TENANT_ID") or None
        
        # Configure persistent token cache
        self._cache_options = None
        try:
            self._cache_options = TokenCachePersistenceOptions(name="jarvis_azure_token_cache")
        except Exception:
            pass

        # Configure DefaultAzureCredential
        dac_kwargs = {}
        if self.tenant_id:
            dac_kwargs["tenant_id"] = self.tenant_id
        if self._cache_options:
            dac_kwargs["cache_persistence_options"] = self._cache_options
        self._default_cred = DefaultAzureCredential(**dac_kwargs)
        self._interactive_cred = None
        self._cached_token = None
        self._cached_expires = 0

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        # Return valid cached token if available
        if self._cached_token and time.time() < (self._cached_expires - 60):
            return self._cached_token

        # 1. Attempt DefaultAzureCredential first
        try:
            token = self._default_cred.get_token(*scopes, **kwargs)
            self._cached_token = token
            self._cached_expires = getattr(token, "expires_on", time.time() + 3600)
            return token
        except Exception as dac_exc:
            log.debug(f"DefaultAzureCredential attempt: {dac_exc}")

        # 2. Fall back to InteractiveBrowserCredential
        try:
            from azure.identity import InteractiveBrowserCredential
            if self._interactive_cred is None:
                ibc_kwargs = {}
                if self.tenant_id:
                    ibc_kwargs["tenant_id"] = self.tenant_id
                if self._cache_options:
                    ibc_kwargs["cache_persistence_options"] = self._cache_options
                self._interactive_cred = InteractiveBrowserCredential(**ibc_kwargs)

            log.info("Requesting Microsoft Azure authentication via browser...")
            token = self._interactive_cred.get_token(*scopes, **kwargs)
            self._cached_token = token
            self._cached_expires = getattr(token, "expires_on", time.time() + 3600)
            return token
        except Exception as ibc_exc:
            raise RuntimeError(
                "Failed to authenticate with Microsoft Azure.\n"
                "Please configure Azure environment credentials (AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID),\n"
                "run 'az login', or run 'python run.py --azure-login' to sign in via your browser.\n"
                f"Details: {ibc_exc}"
            ) from ibc_exc


def get_azure_credential(tenant_id: Optional[str] = None) -> SmartAzureCredential:
    """Return an active SmartAzureCredential instance."""
    return SmartAzureCredential(tenant_id=tenant_id)


def login(tenant_id: Optional[str] = None) -> bool:
    """Trigger an interactive browser sign-in to Microsoft Azure and cache the token."""
    from azure.identity import InteractiveBrowserCredential, TokenCachePersistenceOptions

    t_id = tenant_id or os.environ.get("AZURE_TENANT_ID") or None
    kwargs = {"tenant_id": t_id} if t_id else {}
    try:
        kwargs["cache_persistence_options"] = TokenCachePersistenceOptions(name="jarvis_azure_token_cache")
    except Exception:
        pass
    log.info("Opening browser for Microsoft Azure sign-in...")
    try:
        cred = InteractiveBrowserCredential(**kwargs)
        token = cred.get_token("https://ai.azure.com/.default")
        if token and getattr(token, "token", None):
            log.ok("Successfully authenticated to Microsoft Azure AI Foundry!")
            return True
        log.warn("Azure sign-in completed but received an empty token.")
        return False
    except Exception as exc:
        log.error(f"Microsoft Azure sign-in failed: {exc}")
        return False


def check_auth_status(tenant_id: Optional[str] = None) -> dict[str, Any]:
    """Inspect active Azure credential status without blocking on interactive prompts."""
    import logging as py_logging
    py_logging.getLogger("azure").setLevel(py_logging.CRITICAL)
    py_logging.getLogger("azure.identity").setLevel(py_logging.CRITICAL)

    from azure.identity import DefaultAzureCredential

    t_id = tenant_id or os.environ.get("AZURE_TENANT_ID") or None
    kwargs = {"tenant_id": t_id} if t_id else {}
    cred = DefaultAzureCredential(**kwargs)
    try:
        token = cred.get_token("https://ai.azure.com/.default")
        exp = getattr(token, "expires_on", 0)
        return {
            "authenticated": True,
            "source": "DefaultAzureCredential",
            "expires_on": exp,
            "tenant_id": t_id or "default",
        }
    except Exception as exc:
        return {
            "authenticated": False,
            "error": str(exc),
            "tenant_id": t_id,
        }

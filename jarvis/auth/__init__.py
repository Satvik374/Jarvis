"""Jarvis authentication package."""

__all__ = [
    "login",
    "logout",
    "get_valid_token",
    "refresh_tokens",
    "load_tokens",
    "save_tokens",
    "decode_id_token",
    "extract_chatgpt_account_id",
    "CLIENT_ID",
    "ISSUER",
    "REDIRECT_URI",
    "PORT",
]


def __getattr__(name: str):
    if name in __all__:
        from . import codex_oauth
        return getattr(codex_oauth, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

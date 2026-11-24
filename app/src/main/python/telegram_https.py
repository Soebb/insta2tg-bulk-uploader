"""Telethon HTTPS transport for Telegram MTProto data centers.

Use Telegram's documented HTTPS form of the standard HTTP transport:
https://<dc-name>.web.telegram.org:443/api

The inherited ConnectionHttp codec supplies the required HTTP/1.1 framing
(Content-Length, keep-alive, Host, and application/x-www-form-urlencoded).
Only the socket transport is changed to TLS; no browser-specific route or
custom HTTP framing is used.
"""
from telethon.network import ConnectionHttp

DC_HOSTS = {
    1: "pluto.web.telegram.org",
    2: "venus.web.telegram.org",
    3: "aurora.web.telegram.org",
    4: "vesta.web.telegram.org",
    5: "flora.web.telegram.org",
}


class TelegramHttpsConnection(ConnectionHttp):
    """ConnectionHttp using Telegram's official DC hostname over TLS."""

    def __init__(
        self,
        ip,
        port,
        dc_id,
        *,
        loggers,
        proxy=None,
        local_addr=None,
    ):
        host = DC_HOSTS.get(dc_id)
        if not host:
            raise RuntimeError(
                f"HTTPS transport has no official hostname for Telegram DC {dc_id}; "
                "refusing to fall back to a raw IP/TCP connection."
            )
        super().__init__(
            host,
            443,
            dc_id,
            loggers=loggers,
            proxy=proxy,
            local_addr=local_addr,
        )

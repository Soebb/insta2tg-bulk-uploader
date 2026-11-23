"""Telethon HTTPS-only transport for Telegram data centers.

ConnectionHttp negotiates TLS on port 443. This adapter pins each supported
Telegram DC to its official hostname for both TLS SNI and the HTTP Host header.
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
    """ConnectionHttp with TLS and the official hostname for each Telegram DC."""

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
                f"HTTPS-only transport has no official hostname for Telegram DC {dc_id}; "
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

"""Telethon HTTPS transport for Telegram MTProto data centers.

Use Telegram's documented HTTPS form of the standard HTTP transport:
https://<dc-name>.web.telegram.org:443/api

The inherited ConnectionHttp codec supplies the required HTTP/1.1 framing.
ConnectionHttp automatically enables TLS when the port is 443. The connect
override below adds bounded DNS/TLS diagnostics without changing the codec.
"""
import asyncio
import logging
import socket

from telethon.network import ConnectionHttp

LOG = logging.getLogger("instagram_bulk_android")

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

    async def connect(self, timeout=None, ssl=None):
        # TelegramClient passes its connection timeout here. Keep a finite
        # fallback in case this connection is ever used directly.
        timeout = timeout or 30
        LOG.info(
            "Telegram HTTPS transport: resolving %s:%s (timeout=%ss).",
            self._ip,
            self._port,
            timeout,
        )
        try:
            addresses = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(
                    self._ip,
                    self._port,
                    type=socket.SOCK_STREAM,
                ),
                timeout=timeout,
            )
        except Exception:
            LOG.exception(
                "Telegram HTTPS transport: DNS resolution failed for %s.",
                self._ip,
            )
            raise

        resolved = sorted({
            item[4][0]
            for item in addresses
            if item and item[4]
        })
        LOG.info(
            "Telegram HTTPS transport: DNS resolved %s to %s; starting TLS connection.",
            self._ip,
            ", ".join(resolved) or "(no addresses)",
        )
        try:
            # ConnectionHttp sets ssl=True automatically because port == 443.
            await super().connect(timeout=timeout)
        except Exception:
            LOG.exception(
                "Telegram HTTPS transport: TCP/TLS connection failed for %s:%s.",
                self._ip,
                self._port,
            )
            raise
        LOG.info(
            "Telegram HTTPS transport: TLS connection established to %s:%s.",
            self._ip,
            self._port,
        )

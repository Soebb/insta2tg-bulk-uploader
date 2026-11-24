"""Telethon HTTPS-only transport for Telegram data centers.

This experimental adapter uses Telegram Web's HTTPS MTProto endpoint (/apiw1)
while keeping Telethon's existing HTTP framing unchanged. Only the request path
is varied in this experiment.
"""
import asyncio
import logging

from telethon.network import ConnectionHttp
from telethon.network.connection.http import HttpPacketCodec

TELEGRAM_HTTPS_PATH = "/apiw1"

DC_HOSTS = {
    1: "pluto.web.telegram.org",
    2: "venus.web.telegram.org",
    3: "aurora.web.telegram.org",
    4: "vesta.web.telegram.org",
    5: "flora.web.telegram.org",
}


class TelegramHttpsPacketCodec(HttpPacketCodec):
    """Use Telegram Web's HTTPS path and retain response diagnostics."""

    def encode_packet(self, data):
        packet = super().encode_packet(data)
        original_request_line = b"POST /api HTTP/1.1\r\n"
        if not packet.startswith(original_request_line):
            raise RuntimeError(
                "Unexpected Telethon HTTP packet format; refusing to send "
                "a request with an unverified HTTPS path."
            )
        return (
            b"POST " + TELEGRAM_HTTPS_PATH.encode("ascii") + b" HTTP/1.1\r\n"
            + packet[len(original_request_line):]
        )

    async def read_packet(self, reader):
        try:
            return await super().read_packet(reader)
        except asyncio.IncompleteReadError as exc:
            partial = exc.partial or b""
            first_line = partial.split(b"\r\n", 1)[0][:300]
            logging.getLogger("instagram_bulk_android").error(
                "Telegram HTTPS endpoint closed an HTTP response early; "
                "partial response line=%r, partial_bytes=%d",
                first_line,
                len(partial),
            )
            raise


class TelegramHttpsConnection(ConnectionHttp):
    """ConnectionHttp with TLS and the official hostname for each Telegram DC."""

    packet_codec = TelegramHttpsPacketCodec

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

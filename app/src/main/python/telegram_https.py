"""Telethon HTTPS-only transport for Telegram data centers.

This experimental adapter mirrors Telegram's current HTTP Web route framing:
POST /apiw1, a bare Host authority, keep-alive, and binary MTProto content.
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
    """Match Telegram Web's documented-in-code HTTP request framing."""

    def encode_packet(self, data):
        packet = super().encode_packet(data)
        request_line = b"POST /api HTTP/1.1\r\n"
        content_type = b"Content-Type: application/x-www-form-urlencoded\r\n"
        keep_alive = b"Keep-Alive: timeout=100000, max=10000000\r\n"
        host_with_port = (
            b"Host: " + str(self._conn._ip).encode("ascii") + b":443\r\n"
        )
        if not packet.startswith(request_line):
            raise RuntimeError(
                "Unexpected Telethon HTTP packet format; refusing to send "
                "a request with an unverified HTTPS path."
            )
        if packet.count(content_type) != 1 or packet.count(host_with_port) != 1:
            raise RuntimeError(
                "Unexpected Telethon HTTP headers; refusing to send an "
                "unverified Telegram Web request."
            )

        packet = (
            b"POST " + TELEGRAM_HTTPS_PATH.encode("ascii") + b" HTTP/1.1\r\n"
            + packet[len(request_line):]
        )
        packet = packet.replace(host_with_port, b"Host: " + str(self._conn._ip).encode("ascii") + b"\r\n", 1)
        packet = packet.replace(content_type, b"Content-Type: application/octet-stream\r\n", 1)
        packet = packet.replace(keep_alive, b"", 1)
        return packet

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

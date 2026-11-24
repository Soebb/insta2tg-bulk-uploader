"""Telethon HTTPS transport for Telegram MTProto data centers.

Use Telegram's documented HTTPS form of the standard HTTP transport:
https://<dc-name>.web.telegram.org:443/api

The request framing remains Telethon's HttpPacketCodec implementation.
This subclass only validates and parses the HTTP response before exposing its
body to the MTProto layer.
"""
import asyncio
import logging
import socket

from telethon.network import ConnectionHttp
from telethon.network.connection.http import HttpPacketCodec

LOG = logging.getLogger("instagram_bulk_android")

DC_HOSTS = {
    1: "pluto.web.telegram.org",
    2: "venus.web.telegram.org",
    3: "aurora.web.telegram.org",
    4: "vesta.web.telegram.org",
    5: "flora.web.telegram.org",
}


class TelegramHttpPacketCodec(HttpPacketCodec):
    """HTTP codec with request/response diagnostics for the app log."""

    def __init__(self, connection):
        super().__init__(connection)
        self._diagnostic_request_id = 0

    def encode_packet(self, data):
        self._diagnostic_request_id += 1
        request_id = self._diagnostic_request_id
        packet = super().encode_packet(data)
        # Log only the clear-text HTTP headers. The MTProto payload is encrypted
        # and must never be written to the application log.
        header_block = packet.split(b"\\r\\n\\r\\n", 1)[0]
        LOG.info(
            "HTTPS codec request #%d: encoded HTTP headers (%d bytes): %s",
            request_id,
            len(header_block),
            header_block.decode("ascii", "replace").replace("\\r\\n", " | "),
        )
        LOG.info(
            "HTTPS codec request #%d: encrypted MTProto body size=%d bytes.",
            request_id,
            len(data),
        )
        return packet

    async def read_packet(self, reader):
        request_id = self._diagnostic_request_id
        LOG.info("HTTPS codec request #%d: waiting for HTTP response status line.", request_id)
        try:
            status_line = await reader.readline()
            LOG.info(
                "HTTPS codec request #%d: received status line (%d bytes): %r",
                request_id, len(status_line), status_line[:512],
            )
            if not status_line.endswith(b"\\n"):
                raise asyncio.IncompleteReadError(status_line, None)

            try:
                status_parts = status_line.decode("ascii").strip().split(None, 2)
                if len(status_parts) < 2 or not status_parts[0].startswith("HTTP/"):
                    raise ValueError("invalid HTTP status line")
                status_code = int(status_parts[1])
            except (UnicodeDecodeError, ValueError) as exc:
                raise ConnectionError(
                    f"Telegram HTTPS transport received an invalid HTTP status line: "
                    f"{status_line!r}"
                ) from exc

            headers = {}
            header_count = 0
            while True:
                line = await reader.readline()
                if not line.endswith(b"\\n"):
                    raise asyncio.IncompleteReadError(line, None)

                if line in (b"\\r\\n", b"\\n"):
                    break

                try:
                    name, value = line.decode("iso-8859-1").split(":", 1)
                except ValueError as exc:
                    raise ConnectionError(
                        f"Telegram HTTPS transport received a malformed HTTP header: "
                        f"{line!r}"
                    ) from exc

                name = name.strip().lower()
                value = value.strip()
                if not name:
                    raise ConnectionError("Telegram HTTPS transport received an empty HTTP header name.")

                header_count += 1
                if name in headers and name == "content-length":
                    if headers[name] != value:
                        raise ConnectionError(
                            "Telegram HTTPS transport received conflicting Content-Length headers."
                        )
                else:
                    headers[name] = value

            reason = status_parts[2] if len(status_parts) > 2 else ""
            LOG.info(
                "HTTPS codec request #%d: HTTP status=%d reason=%r; parsed %d headers: %s",
                request_id, status_code, reason, header_count,
                "; ".join("%s=%r" % pair for pair in sorted(headers.items())) or "(none)",
            )

            if status_code != 200:
                raise ConnectionError(
                    f"Telegram HTTPS transport returned HTTP {status_code} {reason}".strip()
                )

            if "transfer-encoding" in headers:
                raise ConnectionError(
                    "Telegram HTTPS transport returned Transfer-Encoding; "
                    "a Content-Length response is required."
                )

            content_length = headers.get("content-length")
            if content_length is None:
                raise ConnectionError(
                    "Telegram HTTPS transport response is missing Content-Length."
                )

            try:
                length = int(content_length, 10)
                if length < 0:
                    raise ValueError("negative Content-Length")
            except ValueError as exc:
                raise ConnectionError(
                    f"Telegram HTTPS transport returned an invalid Content-Length: "
                    f"{content_length!r}"
                ) from exc

            LOG.info(
                "HTTPS codec request #%d: reading exactly %d response body bytes.",
                request_id, length,
            )
            body = await reader.readexactly(length)
            LOG.info(
                "HTTPS codec request #%d: response body received successfully (%d bytes).",
                request_id, len(body),
            )
            return body
        except Exception:
            LOG.exception(
                "HTTPS codec request #%d: failed while reading/parsing HTTP response.",
                request_id,
            )
            raise

class TelegramHttpsConnection(ConnectionHttp):
    """ConnectionHttp using Telegram's official DC hostname over TLS."""

    packet_codec = TelegramHttpPacketCodec

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

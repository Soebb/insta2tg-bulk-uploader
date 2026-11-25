"""Telethon HTTPS transport for Telegram MTProto data centers.

Use Telegram's documented HTTPS form of the standard HTTP transport:
https://<dc-name>.web.telegram.org:443/api

The request framing remains Telethon's HttpPacketCodec implementation.
This subclass only validates and parses the HTTP response before exposing its
body to the MTProto layer.
"""
import asyncio
import hashlib
import itertools
import logging
import socket
from collections import deque

from telethon.network import ConnectionHttp
from telethon.network.connection.http import HttpPacketCodec

LOG = logging.getLogger("instagram_bulk_android")

# Process-wide IDs let us correlate packets across Telethon reconnects.
_REQUEST_IDS = itertools.count(1)
_HOST_VARIANT_COUNTERS = {}

# Telegram documents both the standard DC hostname and the "-1" variant.
# Each reconnect tests one combination, in order:
#   1) -1 hostname + Host with :443
#   2) -1 hostname + Host without port
#   3) standard hostname + Host with :443
#   4) standard hostname + Host without port
DC_HOSTS = {
    1: "pluto-1.web.telegram.org",
    2: "venus-1.web.telegram.org",
    3: "aurora-1.web.telegram.org",
    4: "vesta-1.web.telegram.org",
    5: "flora-1.web.telegram.org",
}


class TelegramHttpPacketCodec(HttpPacketCodec):
    """HTTP codec with request/response diagnostics for the app log."""

    def __init__(self, connection):
        super().__init__(connection)
        self._pending_request_ids = deque()

    def encode_packet(self, data):
        request_id = next(_REQUEST_IDS)
        self._pending_request_ids.append(request_id)
        packet = super().encode_packet(data)
        if getattr(self._conn, "_omit_host_port", False):
            header, separator, body = packet.partition(b"\r\n\r\n")
            if separator:
                lines = header.split(b"\r\n")
                host = ("Host: " + self._conn._ip).encode("ascii")
                lines = [
                    host if line.lower().startswith(b"host:") else line
                    for line in lines
                ]
                packet = b"\r\n".join(lines) + separator + body
        # Log only the clear-text HTTP headers. The MTProto payload is encrypted
        # and must never be written to the application log.
        header_block = packet.split(b"\r\n\r\n", 1)[0]
        LOG.info(
            "HTTPS codec request #%d: encoded HTTP headers (%d bytes): %s",
            request_id,
            len(header_block),
            header_block.decode("ascii", "replace").replace("\r\n", " | "),
        )
        LOG.info(
            "HTTPS codec request #%d: encrypted MTProto body size=%d bytes.",
            request_id,
            len(data),
        )
        return packet

    async def read_packet(self, reader):
        response_id = (
            self._pending_request_ids.popleft()
            if self._pending_request_ids
            else next(_REQUEST_IDS)
        )
        LOG.info(
            "HTTPS codec response for request #%d: waiting for HTTP status line.",
            response_id,
        )
        try:
            status_line = await reader.readline()
            LOG.info(
                "HTTPS codec response for request #%d: received status line (%d bytes): %r",
                response_id, len(status_line), status_line[:512],
            )
            if not status_line.endswith(b"\n"):
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
                if not line.endswith(b"\n"):
                    raise asyncio.IncompleteReadError(line, None)

                if line in (b"\r\n", b"\n"):
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
                "HTTPS codec response for request #%d: HTTP status=%d reason=%r; parsed %d headers: %s",
                response_id, status_code, reason, header_count,
                "; ".join("%s=%r" % pair for pair in sorted(headers.items())) or "(none)",
            )

            if status_code != 200:
                # Telegram transport errors are conveyed by the HTTP status.
                # Read and log a small bounded preview only to identify whether
                # this is Telegram's endpoint or an intermediary-generated page.
                error_body = b""
                error_length = headers.get("content-length")
                if error_length is not None:
                    try:
                        error_size = int(error_length, 10)
                        if 0 <= error_size <= 65536:
                            error_body = await reader.readexactly(error_size)
                    except Exception:
                        LOG.exception(
                            "HTTPS codec response for request #%d: could not read HTTP error body.",
                            response_id,
                        )
                        raise
                if error_body:
                    LOG.warning(
                        "HTTPS codec response for request #%d: HTTP error body preview (%d bytes): %r",
                        response_id, len(error_body), error_body[:2048],
                    )
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
                "HTTPS codec response for request #%d: reading exactly %d response body bytes.",
                response_id, length,
            )
            body = await reader.readexactly(length)
            LOG.info(
                "HTTPS codec response for request #%d: response body received successfully (%d bytes).",
                response_id, len(body),
            )
            return body
        except Exception:
            LOG.exception(
                "HTTPS codec response for request #%d: failed while reading/parsing HTTP response.",
                response_id,
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
        self._diagnostic_dc_id = dc_id
        self._base_host = host
        LOG.info(
            "Telegram HTTPS upload connection selected: dc_id=%s initial_hostname=%s port=443; "
            "diagnostic host matrix enabled.",
            dc_id,
            host,
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
        # Rotate one diagnostic variant on every reconnect. This tests the
        # official -1 and standard DC hostnames, with and without :443 in Host.
        timeout = timeout or 30
        standard_host = self._base_host.replace("-1.web.telegram.org", ".web.telegram.org")
        variants = (
            (self._base_host, False, "port-443"),
            (self._base_host, True, "no-port"),
            (standard_host, False, "port-443"),
            (standard_host, True, "no-port"),
        )
        counter = _HOST_VARIANT_COUNTERS.setdefault(
            self._diagnostic_dc_id, itertools.count()
        )
        variant_number = next(counter) % len(variants)
        host, omit_host_port, host_header_mode = variants[variant_number]
        self._ip = host
        self._omit_host_port = omit_host_port
        LOG.info(
            "Telegram HTTPS diagnostic variant %d/4: dc_id=%s hostname=%s Host-header=%s.",
            variant_number + 1,
            self._diagnostic_dc_id,
            host,
            host_header_mode,
        )
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
        writer = getattr(self, "_writer", None)
        peer = None
        ssl_object = None
        if writer is not None:
            try:
                peer = writer.get_extra_info("peername")
                ssl_object = writer.get_extra_info("ssl_object")
            except Exception:
                LOG.exception("Telegram HTTPS transport: could not inspect TLS connection metadata.")

        if ssl_object is not None:
            try:
                certificate_der = ssl_object.getpeercert(binary_form=True)
                certificate_info = ssl_object.getpeercert() or {}
                certificate_sha256 = (
                    hashlib.sha256(certificate_der).hexdigest()
                    if certificate_der else None
                )
                dns_names = [
                    value for kind, value in certificate_info.get("subjectAltName", ())
                    if kind == "DNS"
                ]
                LOG.info(
                    "Telegram HTTPS TLS details: peer=%s; version=%s; cipher=%s; "
                    "certificate_sha256=%s; subject=%s; issuer=%s; dns_names=%s.",
                    peer,
                    ssl_object.version(),
                    ssl_object.cipher(),
                    certificate_sha256,
                    certificate_info.get("subject"),
                    certificate_info.get("issuer"),
                    dns_names[:30],
                )
            except Exception:
                LOG.exception("Telegram HTTPS transport: could not read peer certificate details.")
        else:
            LOG.warning(
                "Telegram HTTPS transport: TLS connected but ssl_object metadata is unavailable; peer=%s.",
                peer,
            )

        LOG.info(
            "Telegram HTTPS transport: TLS connection established to %s:%s; peer=%s.",
            self._ip,
            self._port,
            peer,
        )

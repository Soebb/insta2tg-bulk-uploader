"""On-device Instagram downloader engine.

The Android service calls start(files_dir, settings_json). All progress is written
to downloader.log so the UI can display diagnostics without requiring ADB.
"""
import asyncio
import hashlib
import http.client
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import shutil
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import telethon
from telethon import TelegramClient
from telethon.sessions import StringSession
from telegram_https import TelegramHttpsConnection

try:
    import yt_dlp
except Exception:
    yt_dlp = None

ROOT = None
LOG = logging.getLogger("instagram_bulk_android")
PAUSED = threading.Event()
PAUSED.set()
STOP = threading.Event()
ACTIVE = False
CONFIG = {}


class ConsecutiveDuplicateFilter(logging.Filter):
    """Suppress only immediately repeated identical records."""
    def __init__(self):
        super().__init__()
        self.previous = None

    def filter(self, record):
        key = (record.levelno, record.getMessage())
        if key == self.previous:
            return False
        self.previous = key
        return True


def setup_logging(root):
    global LOG
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / "downloader.log"
    logger = logging.getLogger("instagram_bulk_android")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    file_handler = RotatingFileHandler(str(log_path), maxBytes=1024 * 1024, backupCount=2, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(ConsecutiveDuplicateFilter())
    logger.addHandler(file_handler)
    LOG = logger


def log_exception(message):
    LOG.exception(message)


def db_open():
    db = sqlite3.connect(str(ROOT / "progress.sqlite3"), timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS posts(
        url TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    db.execute("""CREATE TABLE IF NOT EXISTS chunks(
        url TEXT NOT NULL, chunk_no INTEGER NOT NULL, signature TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(url,chunk_no))""")
    db.commit()
    return db


def telegram_request(method, fields=None, files=None):
    """Stream multipart uploads to Telegram and log safe HTTP/API diagnostics."""
    token = CONFIG["BOT_TOKEN"]
    fields = fields or {}
    files = files or {}
    request_id = "%s-%s" % (time.strftime("%H%M%S"), time.time_ns() % 1_000_000_000)
    endpoint_host = "api.telegram.org"
    endpoint_path = "/bot<redacted>/%s" % method
    file_details = []
    for key, file_path in files.items():
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError("Upload file does not exist: %s" % path.name)
        file_details.append((key, path, path.stat().st_size))

    LOG.info(
        "Bot API request #%s stage 1/7: preparing HTTPS POST host=%s path=%s; fields=%s; files=%s.",
        request_id, endpoint_host, endpoint_path,
        sorted(fields.keys()),
        [{"field": key, "name": path.name, "size_bytes": size}
         for key, path, size in file_details],
    )
    try:
        addresses = sorted({
            result[4][0] for result in socket.getaddrinfo(
                endpoint_host, 443, type=socket.SOCK_STREAM
            )
        })
        LOG.info("Bot API request #%s stage 2/7: DNS %s resolved to %s.",
                 request_id, endpoint_host, addresses)
    except Exception as exc:
        LOG.warning("Bot API request #%s stage 2/7: DNS lookup diagnostic failed (%s).",
                    request_id, type(exc).__name__)

    boundary = "----AndroidBulkBoundary" + hashlib.sha1(
        ("%s-%s" % (time.time_ns(), threading.get_ident())).encode()
    ).hexdigest()
    boundary_bytes = boundary.encode("ascii")

    field_parts = []
    for key, value in fields.items():
        part = (
            b"--" + boundary_bytes + b"\r\n"
            + ('Content-Disposition: form-data; name="%s"\r\n\r\n' % key).encode("utf-8")
            + str(value).encode("utf-8") + b"\r\n"
        )
        field_parts.append(part)

    file_parts = []
    for key, path, _size in file_details:
        header = (
            b"--" + boundary_bytes + b"\r\n"
            + ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n' %
               (key, path.name.replace('"', "_"))).encode("utf-8")
            + b"Content-Type: application/octet-stream\r\n\r\n"
        )
        file_parts.append((header, path))
    closing = b"--" + boundary_bytes + b"--\r\n"
    content_length = sum(map(len, field_parts)) + len(closing)
    content_length += sum(len(header) + path.stat().st_size + 2 for header, path in file_parts)
    total_bytes = content_length
    LOG.info(
        "Bot API request #%s stage 3/7: multipart request prepared; content_length=%d bytes; "
        "file_count=%d; file_bytes=%d.",
        request_id, content_length, len(file_parts),
        sum(size for _key, _path, size in file_details),
    )

    for attempt in range(1, 4):
        connection = None
        sent_bytes = 0
        request_started = time.monotonic()
        try:
            LOG.info("Bot API request #%s stage 4/7: opening TLS connection (attempt %d/3).",
                     request_id, attempt)
            connection = http.client.HTTPSConnection(endpoint_host, timeout=180)
            connection.putrequest("POST", "/bot%s/%s" % (token, method))
            connection.putheader("Content-Type", "multipart/form-data; boundary=%s" % boundary)
            connection.putheader("Content-Length", str(content_length))
            connection.putheader("Connection", "close")
            connection.endheaders()
            tls_elapsed = time.monotonic() - request_started
            peer = None
            if connection.sock is not None:
                try:
                    peer = connection.sock.getpeername()
                except OSError:
                    pass
            LOG.info(
                "Bot API request #%s stage 5/7: TLS connected; peer=%s; "
                "request headers sent; elapsed=%.3fs.",
                request_id, peer, tls_elapsed,
            )

            def send_piece(data):
                nonlocal sent_bytes
                connection.send(data)
                sent_bytes += len(data)

            body_started = time.monotonic()
            for part in field_parts:
                send_piece(part)
            for header, path in file_parts:
                send_piece(header)
                with path.open("rb") as stream:
                    while True:
                        chunk = stream.read(256 * 1024)
                        if not chunk:
                            break
                        send_piece(chunk)
                send_piece(b"\r\n")
            send_piece(closing)
            body_elapsed = time.monotonic() - body_started
            LOG.info(
                "Bot API request #%s stage 6/7: request body transmitted; sent=%d/%d bytes; "
                "upload_time=%.3fs; average_rate=%.1f KiB/s.",
                request_id, sent_bytes, total_bytes, body_elapsed,
                (sent_bytes / 1024.0 / body_elapsed) if body_elapsed > 0 else 0.0,
            )

            if connection.sock is not None:
                connection.sock.settimeout(900)
                LOG.info(
                    "Bot API request #%s: waiting up to 900 seconds for the HTTP/API response.",
                    request_id,
                )
            response = connection.getresponse()
            raw = response.read()
            response_elapsed = time.monotonic() - request_started
            response_headers = {
                key.lower(): value for key, value in response.getheaders()
                if key.lower() in ("content-type", "content-length", "date", "server",
                                   "connection", "retry-after")
            }
            LOG.info(
                "Bot API request #%s stage 7/7: HTTP response received; status=%s reason=%r; "
                "headers=%s; body_bytes=%d; total_elapsed=%.3fs.",
                request_id, response.status, response.reason,
                response_headers, len(raw), response_elapsed,
            )
            try:
                payload = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                preview = raw[:500].decode("utf-8", "replace")
                LOG.warning(
                    "Bot API request #%s: response was not valid JSON; safe body preview=%r.",
                    request_id, preview,
                )
                payload = {"ok": False, "description":
                           "Telegram returned an unreadable response: %s" % preview}

            result = payload.get("result")
            result_summary = None
            if isinstance(result, dict):
                result_summary = {
                    "type": "object",
                    "keys": sorted(result.keys()),
                    "message_id": result.get("message_id"),
                    "date": result.get("date"),
                }
                for media_key in ("video", "document", "photo", "audio", "animation"):
                    media_value = result.get(media_key)
                    if isinstance(media_value, dict):
                        result_summary[media_key] = {
                            key: media_value.get(key)
                            for key in ("file_size", "width", "height", "duration")
                            if media_value.get(key) is not None
                        }
                        break
            elif isinstance(result, list):
                result_summary = {"type": "list", "items": len(result)}
            elif result is not None:
                result_summary = {"type": type(result).__name__}
            LOG.info(
                "Bot API request #%s: parsed Telegram JSON; ok=%r; error_code=%r; "
                "description=%r; parameters=%s; result_summary=%s.",
                request_id, payload.get("ok"), payload.get("error_code"),
                payload.get("description"), payload.get("parameters"),
                result_summary,
            )

            if response.status >= 500 and attempt < 3:
                LOG.warning("Bot API request #%s: HTTP %s for %s; retrying attempt %d/3.",
                            request_id, response.status, method, attempt + 1)
                time.sleep(attempt * 3)
                continue
            if response.status >= 400:
                raise RuntimeError("Telegram HTTP %s: %s" %
                                   (response.status, payload.get("description", payload)))
            if not payload.get("ok"):
                params = payload.get("parameters") or {}
                if params.get("retry_after") and attempt < 3:
                    delay = min(int(params["retry_after"]), 30)
                    LOG.warning(
                        "Bot API request #%s: Telegram rate limit; retry_after=%s; "
                        "retrying in %d seconds.",
                        request_id, params.get("retry_after"), delay,
                    )
                    time.sleep(delay)
                    continue
                raise RuntimeError("Telegram API: " + str(payload.get("description", payload)))
            LOG.info("Bot API request #%s completed successfully.", request_id)
            return payload.get("result")
        except (http.client.RemoteDisconnected, http.client.IncompleteRead,
                TimeoutError, OSError, http.client.HTTPException) as exc:
            LOG.exception(
                "Bot API request #%s transport exception on attempt %d/3 "
                "(sent=%d/%d bytes, elapsed=%.3fs).",
                request_id, attempt, sent_bytes, total_bytes,
                time.monotonic() - request_started,
            )
            if sent_bytes >= total_bytes:
                raise RuntimeError(
                    "Telegram did not return a response after all %d upload bytes were sent "
                    "(%s); delivery status is unknown. Check the destination chat before retrying."
                    % (total_bytes, type(exc).__name__)
                ) from exc
            if attempt >= 3:
                raise RuntimeError(
                    "Telegram upload connection failed after 3 attempts (%s); "
                    "sent %d of %d bytes. Check network stability and try again."
                    % (type(exc).__name__, sent_bytes, total_bytes)
                ) from exc
            LOG.warning(
                "Bot API request #%s: connection interrupted during %s "
                "(attempt %d/3, %d/%d bytes); retrying.",
                request_id, method, attempt, sent_bytes, total_bytes,
            )
            time.sleep(attempt * 3)
        finally:
            if connection is not None:
                connection.close()
    raise RuntimeError("Telegram upload failed after retries.")

BOT_API_UPLOAD_LIMIT = 50_000_000
BOT_API_PHOTO_LIMIT = 10_000_000
TELETHON_VIDEO_THRESHOLD = BOT_API_UPLOAD_LIMIT
MTProto_BOT_UPLOAD_LIMIT = 2_000_000_000


def send_telethon(item, caption, chat_id, force_document=False):
    """Upload large files over HTTPS, then send them over Telethon's normal connection."""
    size = item["path"].stat().st_size
    if size > MTProto_BOT_UPLOAD_LIMIT:
        raise RuntimeError("This file is larger than the supported 2 GB MTProto bot upload limit.")
    api_id = str(CONFIG.get("API_ID", "")).strip()
    api_hash = str(CONFIG.get("API_HASH", "")).strip()
    if not api_id or not api_hash:
        raise RuntimeError("API_ID and API_HASH are required for large-file Telethon uploads.")

    session_dir = ROOT / "telethon"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_path = str(session_dir / "instagram_bulk_bot")
    last_progress = [-5]

    async def upload():
        # This is the regular Telethon client. It owns the authenticated session
        # and is used for all normal Telegram operations and the final send_file.
        LOG.info("MTProto stage 1/9: creating the regular Telethon client.")
        client = TelegramClient(
            session_path,
            int(api_id),
            api_hash,
            request_retries=5,
            connection_retries=5,
            retry_delay=2,
            auto_reconnect=True,
            flood_sleep_threshold=60,
            timeout=30,
            raise_last_call_error=True,
        )
        LOG.info("MTProto stage 2/9: regular Telethon client created.")
        upload_client = None
        try:
            # Authenticate the regular client through Telegram's normal MTProto
            # DC endpoint, never through the web.telegram.org HTTPS hostname.
            # The custom HTTPS hostname is selected only by TelegramHttpsConnection
            # on the separate upload_client below.
            dc_id = 2
            LOG.info("MTProto stage 3/9: configuring the regular Telegram DC session.")
            client.session.set_dc(dc_id, "149.154.167.51", 443)
            client.session.save()
            LOG.info("MTProto stage 4/9: starting/authenticating the regular Telethon client.")
            await client.start(bot_token=CONFIG["BOT_TOKEN"])
            LOG.info(
                "MTProto stage 4/9 complete: regular client authenticated "
                "(authorized=%s, session_dc_id=%s, server=%s:%s).",
                await client.is_user_authorized(),
                getattr(client.session, "dc_id", None),
                getattr(client.session, "server_address", None),
                getattr(client.session, "port", None),
            )

            # Reuse the exact same authorized session/auth key, but give the
            # upload-only client its own connection implementation. Telegram
            # associates the uploaded InputFile with this auth key, so the
            # regular client can submit that InputFile after this client closes.
            # Use a separate in-memory session object containing the same
            # auth key. This avoids two clients sharing/closing one SQLite session.
            LOG.info("MTProto stage 5/9: copying the authorized session into an isolated in-memory session.")
            upload_session = StringSession(StringSession.save(client.session))
            LOG.info(
                "MTProto stage 5/9 complete: isolated session created "
                "(upload_session_dc_id=%s, server=%s:%s).",
                getattr(upload_session, "dc_id", None),
                getattr(upload_session, "server_address", None),
                getattr(upload_session, "port", None),
            )
            upload_client = TelegramClient(
                upload_session,
                int(api_id),
                api_hash,
                connection=TelegramHttpsConnection,
                request_retries=5,
                connection_retries=5,
                retry_delay=2,
                auto_reconnect=True,
                flood_sleep_threshold=60,
                timeout=30,
                raise_last_call_error=True,
            )
            LOG.info("MTProto stage 6/9: HTTPS upload client constructed; connecting to Telegram HTTPS DC.")
            await upload_client.connect()
            LOG.info("MTProto stage 6/9 complete: HTTPS upload client connected (connected=%s).",
                     upload_client.is_connected())
            LOG.info("MTProto stage 7/9: checking inherited Telegram authorization.")
            if not await upload_client.is_user_authorized():
                raise RuntimeError(
                    "Upload-only HTTPS client did not inherit the regular Telethon authorization."
                )

            def progress(current, total):
                if not total:
                    return
                percent = int(current * 100 / total)
                if percent >= last_progress[0] + 5 or current >= total:
                    last_progress[0] = percent
                    LOG.info("Telethon HTTPS upload: %d%% (%.1f/%.1f MB).",
                             percent, current / 1048576.0, total / 1048576.0)

            LOG.info("MTProto stage 7/9 complete: HTTPS client authorization verified.")
            part_size_kb = 512
            part_count = (size + part_size_kb * 1024 - 1) // (part_size_kb * 1024)
            LOG.info(
                "MTProto stage 8/9: starting HTTPS upload with %d KiB chunks (%d parts; %.1f MB).",
                part_size_kb,
                part_count,
                size / 1048576.0,
            )
            uploaded_file = await upload_client.upload_file(
                str(item["path"]),
                file_size=size,
                file_name=item["path"].name,
                part_size_kb=part_size_kb,
                progress_callback=progress,
            )
            LOG.info("MTProto stage 8/9 complete: HTTPS file upload finished; closing upload-only client.")
            await upload_client.disconnect()
            upload_client = None
            LOG.info("MTProto upload-only client disconnected.")

            # Sending the Telegram message is deliberately outside the custom
            # HTTP transport. The regular client shares the same auth key.
            LOG.info("MTProto stage 9/9: sending the uploaded file through the regular Telethon connection.")
            await client.send_file(
                chat_id,
                uploaded_file,
                caption=(caption or "")[:1024] or None,
                force_document=bool(force_document or not item["is_video"]),
                supports_streaming=bool(item["is_video"] and not force_document),
            )
            LOG.info("MTProto stage 9/9 complete: file message sent over the regular Telethon connection.")
        finally:
            if upload_client is not None:
                LOG.info("Cleaning up: disconnecting the HTTPS upload client.")
                await upload_client.disconnect()
            LOG.info("Cleaning up: disconnecting the regular Telethon client.")
            await client.disconnect()
            LOG.info("MTProto clients disconnected.")

    LOG.info(
        "File exceeds the Bot API upload threshold; using HTTPS only for the MTProto file upload (%.1f MB).",
        size / 1048576.0,
    )
    try:
        asyncio.run(upload())
    except Exception as exc:
        raise RuntimeError("Telethon large-file upload/send failed (%s): %s" %
                           (type(exc).__name__, exc)) from exc


def send_single(item, caption, chat_id, force_document=False):
    size = item["path"].stat().st_size
    is_video = item["is_video"]
    # Keep videos within the official Bot API limit on the fast direct HTTPS
    # multipart path. Only larger videos need the MTProto/Telethon uploader.
    if is_video and size > TELETHON_VIDEO_THRESHOLD:
        LOG.info("Video exceeds the 50 MB Bot API limit; using Telethon HTTPS MTProto.")
        return send_telethon(item, caption, chat_id, force_document=force_document)
    if size > BOT_API_UPLOAD_LIMIT:
        return send_telethon(item, caption, chat_id, force_document=force_document)

    # Database files and other non-media payloads must be sent as documents.
    # Bot API sendPhoto has a stricter 10 MB upload limit, so larger images
    # are also sent as documents instead of failing a valid <=50 MB upload.
    use_document = force_document or (not is_video and size > BOT_API_PHOTO_LIMIT)
    method = "sendDocument" if use_document else ("sendVideo" if is_video else "sendPhoto")
    key = "document" if use_document else ("video" if is_video else "photo")
    fields = {"chat_id": chat_id}
    if caption:
        fields["caption"] = caption[:1024]
    try:
        return telegram_request(method, fields, {key: item["path"]})
    except RuntimeError as exc:
        # Only fall back when the Bot API connection broke before the full
        # multipart body was sent. A lost response after a complete upload is
        # deliberately NOT retried because Telegram may already have posted it.
        if "Telegram upload connection failed after 3 attempts" not in str(exc):
            raise
        LOG.warning(
            "Bot API upload repeatedly interrupted; retrying this file through Telethon HTTPS MTProto."
        )
        return send_telethon(item, caption, chat_id, force_document=force_document)


def send_album(files, caption, chat_id):
    # Albums with oversized items are sent individually, choosing the transport
    # per file so small files still use the regular Bot API.
    oversized = any(
        item["path"].stat().st_size >
        (TELETHON_VIDEO_THRESHOLD if item["is_video"] else BOT_API_PHOTO_LIMIT)
        for item in files
    )
    if oversized:
        results = []
        for index, item in enumerate(files):
            results.append(send_single(item, caption if index == 0 else "", chat_id))
        return results

    media = []
    uploads = {}
    for i, item in enumerate(files):
        field = "file%d" % i
        media.append({
            "type": "video" if item["is_video"] else "photo",
            "media": "attach://" + field,
        })
        uploads[field] = item["path"]
        if i == 0 and caption:
            media[0]["caption"] = caption[:1024]
    return telegram_request("sendMediaGroup", {"chat_id": chat_id, "media": json.dumps(media)}, uploads)


def extract_files(url, folder):
    folder.mkdir(parents=True, exist_ok=True)

    def progress_hook(_status):
        if STOP.is_set():
            raise yt_dlp.utils.DownloadError("Download stopped by user")

    options = {
        "outtmpl": str(folder / "%(autonumber)03d-%(id)s.%(ext)s"),
        "format": "best[height=360]/best[height=480]/best[height=720]/best[height<=360]/best[height<=480]/best[height<=720]/best",
        "noplaylist": False, "ignoreerrors": False, "quiet": True, "no_warnings": True,
        "retries": 10, "fragment_retries": 10, "extractor_retries": 5, "socket_timeout": 45,
        "merge_output_format": "mp4",
        "progress_hooks": [progress_hook],
    }
    info = None
    for attempt in range(1, 4):
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
            break
        except Exception as exc:
            message = str(exc).lower()
            transient = any(marker in message for marker in (
                "ssl", "unexpected eof", "eof occurred", "remote end closed",
                "connection reset", "connection aborted", "timed out",
                "temporarily unavailable", "network is unreachable"
            ))
            if not transient or attempt >= 3 or STOP.is_set():
                raise
            delay = attempt * 4
            LOG.warning("Temporary Instagram connection error (%s), retrying extraction in %d seconds (attempt %d/3).",
                        type(exc).__name__, delay, attempt)
            for _ in range(delay * 4):
                if STOP.is_set():
                    raise InterruptedError("Download stopped by user")
                time.sleep(0.25)
    if not info:
        raise RuntimeError("yt-dlp returned no information for this Instagram URL.")
    entries = info.get("entries") or [info]
    flattened = []
    for entry in entries:
        if not entry:
            continue
        if entry.get("entries"):
            flattened.extend([x for x in entry["entries"] if x])
        else:
            flattened.append(entry)
    found = []
    for entry in flattened:
        candidates = []
        for item in entry.get("requested_downloads") or []:
            if item.get("filepath"):
                candidates.append(Path(item["filepath"]))
        if entry.get("filepath"):
            candidates.append(Path(entry["filepath"]))
        media_id = str(entry.get("id") or "")
        if media_id:
            candidates.extend(folder.glob("*" + media_id + ".*"))
        path = next((p for p in candidates if p.is_file() and not p.name.endswith((".part", ".ytdl"))), None)
        if path and path not in [x["path"] for x in found]:
            ext = path.suffix.lower()
            found.append({
                "path": path,
                "is_video": ext in (".mp4", ".m4v", ".mov", ".webm", ".mkv"),
                "id": media_id or hashlib.sha1(str(path).encode()).hexdigest()[:12],
            })
    if not found:
        for path in sorted(folder.iterdir()):
            if path.is_file() and not path.name.endswith((".part", ".ytdl")):
                found.append({
                    "path": path,
                    "is_video": path.suffix.lower() in (".mp4", ".m4v", ".mov", ".webm", ".mkv"),
                    "id": hashlib.sha1(path.name.encode()).hexdigest()[:12],
                })
    if not found:
        raise RuntimeError("yt-dlp finished but no downloaded media files were found.")
    return found, (info.get("description") or "")


def wait_if_paused():
    while not PAUSED.is_set():
        if STOP.is_set():
            raise InterruptedError("Stop requested")
        time.sleep(0.4)
    if STOP.is_set():
        raise InterruptedError("Stop requested")


def send_database_backup(db, index, total, url):
    backup_chat = CONFIG.get("BACKUP_CHAT_ID") or CONFIG.get("OWNER_ID")
    if not backup_chat:
        return
    backup = ROOT / "progress-backup.sqlite3"
    try:
        db.commit()
        target = sqlite3.connect(str(backup))
        try:
            db.backup(target)
        finally:
            target.close()
        backup_item = {"path": backup, "is_video": False}
        send_single(
            backup_item,
            "Database checkpoint after link %d/%d\n%s" % (index, total, url),
            backup_chat,
            force_document=True,
        )
        LOG.info("Database backup sent to Telegram chat %s.", backup_chat)
    except Exception:
        LOG.exception("Database backup failed")
    finally:
        try:
            backup.unlink(missing_ok=True)
        except Exception:
            pass


def run_queue():
    global ACTIVE
    ACTIVE = True
    db = db_open()
    try:
        links_path = ROOT / "links.txt"
        if not links_path.exists():
            raise FileNotFoundError("links.txt was not found in app storage.")
        links = []
        seen = set()
        for line in links_path.read_text(encoding="utf-8-sig").splitlines():
            url = line.strip()
            if url and not url.startswith("#") and url not in seen:
                links.append(url)
                seen.add(url)
        LOG.info("Queue started: %d unique Instagram links.", len(links))
        for index, url in enumerate(links, 1):
            if STOP.is_set():
                LOG.warning("Stop requested. Queue stopped before next link.")
                break
            row = db.execute("SELECT status FROM posts WHERE url=?", (url,)).fetchone()
            if row and row[0] == "done":
                LOG.info("[%d/%d] Already completed; skipping %s", index, len(links), url)
                continue
            key = hashlib.sha1(url.encode()).hexdigest()[:16]
            folder = ROOT / "downloads" / key
            try:
                wait_if_paused()
                LOG.info("[%d/%d] Extracting and downloading: %s", index, len(links), url)
                db.execute("""INSERT INTO posts(url,status,attempts) VALUES(?,'processing',1)
                    ON CONFLICT(url) DO UPDATE SET status='processing',attempts=attempts+1,last_error=NULL,
                    updated_at=CURRENT_TIMESTAMP""", (url,))
                db.commit()
                media, description = extract_files(url, folder)
                caption = (url + ("\n\n" + description if description else ""))[:1024]
                LOG.info("[%d/%d] Downloaded %d media item(s).", index, len(links), len(media))
                chunks = [media[i:i+10] for i in range(0, len(media), 10)]
                for chunk_no, chunk in enumerate(chunks):
                    wait_if_paused()
                    sig = hashlib.sha256("|".join(x["id"] for x in chunk).encode()).hexdigest()
                    sent = db.execute("SELECT signature,status FROM chunks WHERE url=? AND chunk_no=?",
                                      (url, chunk_no)).fetchone()
                    if sent and sent[0] == sig and sent[1] == "sent":
                        LOG.info("Album %d already sent; skipping.", chunk_no + 1)
                        continue
                    upload_bytes = sum(item["path"].stat().st_size for item in chunk)
                    LOG.info("[%d/%d] Uploading batch %d/%d: %d file(s), %.1f MB.",
                             index, len(links), chunk_no + 1, len(chunks), len(chunk),
                             upload_bytes / (1024.0 * 1024.0))
                    if len(chunk) == 1:
                        send_single(chunk[0], caption if chunk_no == 0 else "", CONFIG["TARGET_CHAT_ID"])
                    else:
                        send_album(chunk, caption if chunk_no == 0 else "", CONFIG["TARGET_CHAT_ID"])
                    db.execute("""INSERT INTO chunks(url,chunk_no,signature,status) VALUES(?,?,?,'sent')
                        ON CONFLICT(url,chunk_no) DO UPDATE SET signature=excluded.signature,status='sent',
                        updated_at=CURRENT_TIMESTAMP""", (url, chunk_no, sig))
                    db.commit()
                    LOG.info("[%d/%d] Album %d/%d sent successfully.", index, len(links), chunk_no + 1, len(chunks))
                    delay_end = time.monotonic() + max(0.0, float(CONFIG.get("REQUEST_DELAY", "2")))
                    while time.monotonic() < delay_end:
                        wait_if_paused()
                        time.sleep(min(0.25, delay_end - time.monotonic()))
                db.execute("UPDATE posts SET status='done',last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE url=?", (url,))
                db.commit()
                LOG.info("[%d/%d] Completed successfully.", index, len(links))
            except InterruptedError:
                db.execute("UPDATE posts SET status='pending',updated_at=CURRENT_TIMESTAMP WHERE url=?", (url,))
                db.commit()
                LOG.warning("Queue interrupted by user.")
                break
            except Exception as exc:
                if STOP.is_set():
                    db.execute("UPDATE posts SET status='pending',last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE url=?", (url,))
                    db.commit()
                    LOG.info("Stop acknowledged. Current item returned to pending.")
                    break
                db.execute("UPDATE posts SET status='failed',last_error=?,updated_at=CURRENT_TIMESTAMP WHERE url=?",
                           (str(exc)[:1000], url))
                db.commit()
                LOG.exception("[%d/%d] Download or upload failed: %s", index, len(links), url)
            finally:
                shutil.rmtree(folder, ignore_errors=True)
                send_database_backup(db, index, len(links), url)
        counts = {s: db.execute("SELECT COUNT(*) FROM posts WHERE status=?", (s,)).fetchone()[0]
                  for s in ("done", "failed", "pending", "processing")}
        LOG.info("Queue ended. " + ", ".join("%s=%s" % x for x in counts.items()))
    finally:
        db.close()
        ACTIVE = False


def start(files_dir, settings_json):
    global ROOT, CONFIG
    ROOT = Path(files_dir)
    setup_logging(ROOT)
    try:
        CONFIG = json.loads(settings_json)
        required = ("BOT_TOKEN", "TARGET_CHAT_ID")
        missing = [x for x in required if not str(CONFIG.get(x, "")).strip()]
        if missing:
            raise ValueError("Missing settings: " + ", ".join(missing))
        if yt_dlp is None:
            raise RuntimeError("yt-dlp failed to import. Check embedded Python packages.")
        LOG.info("Downloader engine initialized. yt-dlp version: %s", yt_dlp.version.__version__)
        LOG.info("Telethon version: %s", telethon.__version__)
        LOG.info("Destination chat: %s", CONFIG["TARGET_CHAT_ID"])
        if ACTIVE:
            LOG.warning("A queue is already active.")
            return "Queue already running"
        if CONFIG.get("STOP_REQUESTED"):
            STOP.set()
        else:
            STOP.clear()
        if CONFIG.get("PAUSE_REQUESTED") and not STOP.is_set():
            PAUSED.clear()
        else:
            PAUSED.set()
        run_queue()
        return "Queue finished"
    except Exception:
        LOG.exception("Engine startup/runtime error")
        raise


def pause():
    PAUSED.clear()
    LOG.info("Pause requested; downloader will pause at the next safe checkpoint.")


def resume():
    was_paused = not PAUSED.is_set()
    PAUSED.set()
    if was_paused:
        LOG.info("Downloader resumed.")


def stop():
    already_stopping = STOP.is_set()
    STOP.set()
    PAUSED.set()
    if not already_stopping:
        LOG.info("Stop requested; cancelling extraction and stopping at the next safe checkpoint.")

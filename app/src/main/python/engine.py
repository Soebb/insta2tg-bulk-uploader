"""On-device Instagram downloader engine.

The Android service calls start(files_dir, settings_json). All progress is written
to downloader.log so the UI can display diagnostics without requiring ADB.
"""
import asyncio
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from wzgram import Client
from wzgram.types import InputMediaPhoto, InputMediaVideo
import warpcrypto as _warpcrypto

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


def verify_crypto_backend():
    key = bytes(range(32))
    iv = bytes(range(32))
    plain = bytes(range(32))
    encrypted = _warpcrypto.ige256_encrypt(plain, key, iv)
    if _warpcrypto.ige256_decrypt(encrypted, key, iv) != plain:
        raise RuntimeError("WarpCrypto JNI AES-IGE self-test failed")

    auth_key = bytes(range(256))
    auth_key_id = b"12345678"
    session_id = b"abcdefgh"
    body = b"JNI-WarpCrypto-self-test"
    packed = _warpcrypto.pack_message(1, 1, body, 0, session_id, auth_key, auth_key_id)
    msg_id, seq_no, length, decoded, _total_len = _warpcrypto.unpack_message(
        packed, session_id, auth_key, auth_key_id, incoming=False
    )
    if (msg_id, seq_no, length, decoded) != (1, 1, len(body), body):
        raise RuntimeError("WarpCrypto JNI MTProto pack/unpack self-test failed")


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


def upload_progress(label):
    last = [-5]
    async def progress(current, total):
        if not total:
            return
        percent = int(current * 100 / total)
        if percent >= last[0] + 5 or current >= total:
            last[0] = percent
            LOG.info("Wzgram MTProto upload %s: %d%% (%.1f/%.1f MB).",
                     label, percent, current / 1048576.0, total / 1048576.0)
    return progress


async def send_single(client, item, caption, chat_id, force_document=False):
    path = item["path"]
    size = path.stat().st_size
    common = {
        "chat_id": chat_id,
        "caption": (caption or "")[:1024] or None,
        "progress": upload_progress(path.name),
    }
    LOG.info("Uploading %s through Wzgram MTProto (%.1f MB).",
             path.name, size / 1048576.0)
    if item.get("is_video") and not force_document:
        return await client.send_video(
            video=str(path), supports_streaming=True, **common
        )
    if not force_document:
        return await client.send_photo(photo=str(path), **common)
    return await client.send_document(document=str(path), **common)


async def send_album(client, files, caption, chat_id):
    if not 2 <= len(files) <= 10:
        raise ValueError("Telegram albums must contain between 2 and 10 media items.")
    media = []
    for index, item in enumerate(files):
        path = item["path"]
        cap = (caption or "")[:1024] if index == 0 else None
        if item.get("is_video"):
            media.append(InputMediaVideo(
                str(path), caption=cap, supports_streaming=True
            ))
        else:
            media.append(InputMediaPhoto(str(path), caption=cap))
    total_bytes = sum(x["path"].stat().st_size for x in files)
    LOG.info("Uploading %d-item album through Wzgram MTProto (%.1f MB).",
             len(files), total_bytes / 1048576.0)
    return await client.send_media_group(
        chat_id, media, progress=upload_progress("album")
    )


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


async def send_database_backup(client, db, index, total, url):
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
        await send_single(
            client,
            {"path": backup, "is_video": False},
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


async def run_queue(client):
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
                        await send_single(client, chunk[0], caption if chunk_no == 0 else "", CONFIG["TARGET_CHAT_ID"])
                    else:
                        await send_album(client, chunk, caption if chunk_no == 0 else "", CONFIG["TARGET_CHAT_ID"])
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
                await send_database_backup(client, db, index, len(links), url)
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
        required = ("BOT_TOKEN", "API_ID", "API_HASH", "TARGET_CHAT_ID")
        missing = [x for x in required if not str(CONFIG.get(x, "")).strip()]
        if missing:
            raise ValueError("Missing settings: " + ", ".join(missing))
        if yt_dlp is None:
            raise RuntimeError("yt-dlp failed to import. Check embedded Python packages.")
        verify_crypto_backend()
        LOG.info("WarpCrypto Rust/JNI backend initialized and self-test passed.")
        LOG.info("Downloader engine initialized. yt-dlp version: %s", yt_dlp.version.__version__)
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
        async def run_with_wzgram():
            session_dir = ROOT / "wzgram"
            session_dir.mkdir(parents=True, exist_ok=True)
            async with Client(
                "instagram_bulk_bot",
                api_id=int(CONFIG["API_ID"]),
                api_hash=CONFIG["API_HASH"],
                bot_token=CONFIG["BOT_TOKEN"],
                workdir=str(session_dir),
                no_updates=True,
                sleep_threshold=60,
                max_concurrent_transmissions=1,
            ) as client:
                await run_queue(client)
        asyncio.run(run_with_wzgram())
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

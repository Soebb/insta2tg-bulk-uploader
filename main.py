import asyncio
import hashlib
import logging
import os
import shutil
import sqlite3
import time
from pathlib import Path

import yt_dlp
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.network import ConnectionHttp

from telegram_https import DC_HOSTS, TelegramHttpsConnection

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
load_dotenv(ROOT / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
BACKUP_CHAT_ID = int(os.getenv("BACKUP_CHAT_ID", str(OWNER_ID)) or 0)
SESSION_NAME = os.getenv("SESSION_NAME", "instagram_bulk_telethon").strip()
INPUT_FILE = Path(os.getenv("INPUT_FILE", "links.txt"))
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "downloads"))
DATABASE_FILE = Path(os.getenv("DATABASE_FILE", "progress.sqlite3"))
DIRECT_URL_UPLOAD = os.getenv("DIRECT_URL_UPLOAD", "true").lower() in {"1", "true", "yes", "on"}
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "2"))

if not BOT_TOKEN or not API_ID or not API_HASH or not TARGET_CHAT_ID:
    raise SystemExit("Fill BOT_TOKEN, API_ID, API_HASH and TARGET_CHAT_ID in .env.")

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("downloader.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("instagram-bulk")

# This is a Telethon MTProto client authenticated as the bot account.
# ConnectionHttp uses TLS automatically when its port is 443. The custom
# connection maps every Telegram DC to its official HTTPS hostname.
app = TelegramClient(
    SESSION_NAME,
    API_ID,
    API_HASH,
    connection=TelegramHttpsConnection,
    request_retries=5,
    connection_retries=5,
    retry_delay=2,
    auto_reconnect=True,
    flood_sleep_threshold=60,
)

run_lock = asyncio.Lock()
pause_event = asyncio.Event()
pause_event.set()
stop_requested = False


def database():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS posts(
        url TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS chunks(
        url TEXT NOT NULL, chunk_no INTEGER NOT NULL, signature TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(url, chunk_no))""")
    conn.commit()
    return conn


async def send_database_backup(conn, index, total, url):
    """Create a consistent SQLite snapshot and upload it through Telethon/MTProto."""
    if not BACKUP_CHAT_ID:
        log.warning("Database backup not sent: set OWNER_ID or BACKUP_CHAT_ID.")
        return
    backup_path = DOWNLOAD_DIR / f"progress-backup-{int(time.time())}.sqlite3"
    try:
        conn.commit()
        target = sqlite3.connect(str(backup_path))
        try:
            conn.backup(target)
            target.commit()
        finally:
            target.close()
        await app.send_file(
            BACKUP_CHAT_ID,
            str(backup_path),
            caption=f"Database checkpoint after link {index}/{total}.\nCompleted item: {url}",
        )
        log.info("Database checkpoint delivered to Telegram private chat %s.", BACKUP_CHAT_ID)
    except Exception:
        log.exception("Could not send database checkpoint through Telethon.")
    finally:
        try:
            backup_path.unlink(missing_ok=True)
        except OSError:
            log.warning("Could not remove temporary database backup %s", backup_path)


async def restore_database_from_reply(event):
    reply = await event.get_reply_message()
    if not reply or not reply.document:
        await event.reply("Reply to a database backup document with /restore.")
        return
    if run_lock.locked():
        await event.reply("Stop the queue before restoring the database.")
        return

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    restore_path = DOWNLOAD_DIR / "restore-candidate.sqlite3"
    try:
        downloaded = await app.download_media(reply, file=str(restore_path))
        if not downloaded:
            raise RuntimeError("Telegram did not return a downloaded file.")
        candidate = Path(downloaded)
        check = sqlite3.connect(str(candidate))
        try:
            integrity = check.execute("PRAGMA integrity_check").fetchone()
            tables = {
                row[0]
                for row in check.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if (
                not integrity
                or integrity[0] != "ok"
                or not {"posts", "chunks"}.issubset(tables)
            ):
                raise RuntimeError("The file is not a valid downloader database backup.")
        finally:
            check.close()

        for suffix in ("-wal", "-shm"):
            Path(str(DATABASE_FILE) + suffix).unlink(missing_ok=True)
        os.replace(candidate, DATABASE_FILE)
        await event.reply("Database restored successfully. You can now send /start.")
    except Exception as exc:
        log.exception("Database restore failed.")
        await event.reply(f"Restore failed: {exc}")
    finally:
        restore_path.unlink(missing_ok=True)


def load_links():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_FILE}")
    result, seen = [], set()
    for line in INPUT_FILE.read_text(encoding="utf-8-sig").splitlines():
        url = line.strip()
        if url and not url.startswith("#") and url not in seen:
            result.append(url)
            seen.add(url)
    return result


def flatten(info):
    entries = info.get("entries")
    if not entries:
        return [info]
    output = []
    for entry in entries:
        if entry:
            output.extend(flatten(entry) if entry.get("entries") else [entry])
    return output


def is_video(item):
    codec = item.get("vcodec")
    ext = (item.get("ext") or "").lower()
    return (codec and codec != "none") or ext in {
        "mp4", "m4v", "mov", "webm", "mkv"
    }


def best_format(entry):
    formats = [item for item in entry.get("formats", []) if item.get("url")]
    if not formats:
        return entry
    videos = [item for item in formats if is_video(item)]
    candidates = videos or formats
    if not videos:
        return max(
            candidates,
            key=lambda item: (item.get("width") or 0) * (item.get("height") or 0),
        )
    for height in (360, 480, 720):
        exact = [item for item in candidates if item.get("height") == height]
        if exact:
            return max(exact, key=lambda item: item.get("tbr") or 0)
    for limit in (360, 480, 720):
        lower = [
            item for item in candidates
            if (item.get("height") or 0) <= limit
        ]
        if lower:
            return max(
                lower,
                key=lambda item: (item.get("height") or 0, item.get("tbr") or 0),
            )
    return max(
        candidates,
        key=lambda item: (item.get("height") or 0, item.get("tbr") or 0),
    )


def make_items(info):
    result = []
    for entry in flatten(info):
        fmt = best_format(entry)
        media_url = fmt.get("url") or entry.get("url")
        if not media_url:
            continue
        media_id = str(
            entry.get("id") or hashlib.sha1(media_url.encode()).hexdigest()[:12]
        )
        size = (
            fmt.get("filesize")
            or fmt.get("filesize_approx")
            or entry.get("filesize")
            or entry.get("filesize_approx")
        )
        result.append({
            "id": media_id,
            "url": media_url,
            "is_video": is_video(fmt) or is_video(entry),
            "size": int(size) if size else None,
        })
    return result


def split(items, limit=10):
    for start in range(0, len(items), limit):
        yield start // limit, items[start:start + limit]


def signature(items):
    return hashlib.sha256(
        "|".join(item["id"] for item in items).encode()
    ).hexdigest()


def chunk_is_sent(conn, url, number, sig):
    row = conn.execute(
        "SELECT signature,status FROM chunks WHERE url=? AND chunk_no=?",
        (url, number),
    ).fetchone()
    return bool(row and row[0] == sig and row[1] == "sent")


def set_chunk(conn, url, number, sig):
    conn.execute(
        """INSERT INTO chunks(url,chunk_no,signature,status) VALUES(?,?,?,'sent')
        ON CONFLICT(url,chunk_no) DO UPDATE SET signature=excluded.signature,
        status='sent', updated_at=CURRENT_TIMESTAMP""",
        (url, number, sig),
    )
    conn.commit()


def caption_for(index, url, description):
    return f"{index}\n{url}\n\n{description or ''}".strip()[:1024]


async def send_direct(items, caption):
    """Ask Telegram to fetch public media URLs using Telethon's MTProto API."""
    urls = [item["url"] for item in items]
    if len(urls) == 1:
        return await app.send_file(
            TARGET_CHAT_ID,
            urls[0],
            caption=caption,
            supports_streaming=bool(items[0]["is_video"]),
        )
    return await app.send_file(
        TARGET_CHAT_ID,
        urls,
        caption=caption,
        supports_streaming=True,
    )


async def download_post(url, key):
    folder = DOWNLOAD_DIR / key
    folder.mkdir(parents=True, exist_ok=True)
    options = {
        "outtmpl": str(folder / "%(id)s.%(ext)s"),
        "format": (
            "best[height=360]/best[height=480]/best[height=720]/"
            "best[height<=360]/best[height<=480]/best[height<=720]/best"
        ),
        "noplaylist": False,
        "ignoreerrors": False,
        "quiet": True,
        "no_warnings": True,
        "retries": 8,
        "fragment_retries": 8,
        "socket_timeout": 30,
        "merge_output_format": "mp4",
    }

    def work():
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise RuntimeError("yt-dlp returned no media information.")
            entries = flatten(info)
            files = []
            for entry in entries:
                paths = [
                    Path(item["filepath"])
                    for item in entry.get("requested_downloads", [])
                    if item.get("filepath")
                ]
                if entry.get("filepath"):
                    paths.append(Path(entry["filepath"]))
                paths.extend(folder.glob(f"{entry.get('id', '')}.*"))
                found = next((path for path in paths if path.is_file()), None)
                if found and found not in files:
                    files.append(found)
            if len(files) != len(entries):
                remaining = [
                    path for path in sorted(folder.iterdir())
                    if path.is_file()
                    and path not in files
                    and not path.name.endswith((".part", ".ytdl"))
                ]
                files.extend(remaining[:len(entries) - len(files)])
            return files

    paths = await asyncio.to_thread(work)
    if not paths:
        raise RuntimeError("No downloaded files found.")
    return paths


async def send_local(items, paths, caption):
    if len(items) != len(paths):
        raise RuntimeError(
            f"Media/file count mismatch: {len(items)} vs {len(paths)}"
        )
    if len(paths) == 1:
        return await app.send_file(
            TARGET_CHAT_ID,
            str(paths[0]),
            caption=caption,
            supports_streaming=bool(items[0]["is_video"]),
        )
    return await app.send_file(
        TARGET_CHAT_ID,
        [str(path) for path in paths],
        caption=caption,
        supports_streaming=True,
    )


async def process_one(conn, index, total, url):
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    conn.execute(
        """INSERT INTO posts(url,status,attempts) VALUES(?,'processing',1)
        ON CONFLICT(url) DO UPDATE SET status='processing',
        attempts=attempts+1, updated_at=CURRENT_TIMESTAMP""",
        (url,),
    )
    conn.commit()
    log.info("[%d/%d] %s", index, total, url)

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": False,
        "retries": 5,
    }
    info = await asyncio.to_thread(
        lambda: yt_dlp.YoutubeDL(opts).extract_info(url, download=False)
    )
    if not info:
        raise RuntimeError("Could not extract post.")

    entries = flatten(info)
    items = make_items(info)
    if not items:
        raise RuntimeError("No photo/video media found.")

    caption = caption_for(
        index,
        url,
        info.get("description") or (entries[0].get("description") or ""),
    )
    work = [
        (number, part, signature(part))
        for number, part in split(items)
        if not chunk_is_sent(conn, url, number, signature(part))
    ]
    if not work:
        conn.execute("UPDATE posts SET status='done' WHERE url=?", (url,))
        conn.commit()
        return

    local_files = None
    for number, part, sig in work:
        await pause_event.wait()
        chunk_caption = caption if number == 0 else ""
        direct_ok = False

        if DIRECT_URL_UPLOAD and all(item.get("url") for item in part):
            try:
                await send_direct(part, chunk_caption)
                direct_ok = True
                log.info("Chunk %d sent using Telethon external-media upload.", number + 1)
            except FloodWaitError:
                raise
            except Exception as exc:
                log.warning("Telethon URL upload failed; using local fallback: %s", exc)

        if not direct_ok:
            if local_files is None:
                local_files = await download_post(url, key)
            start = number * 10
            selected = local_files[start:start + len(part)]
            await send_local(part, selected, chunk_caption)
            log.info("Chunk %d uploaded from local files through Telethon.", number + 1)

        set_chunk(conn, url, number, sig)
        await asyncio.sleep(REQUEST_DELAY)

    conn.execute(
        """UPDATE posts SET status='done',last_error=NULL,
        updated_at=CURRENT_TIMESTAMP WHERE url=?""",
        (url,),
    )
    conn.commit()

    if local_files:
        for path in local_files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                log.warning("Could not delete temporary file %s", path)
        try:
            (DOWNLOAD_DIR / key).rmdir()
        except OSError:
            pass


def allowed(event):
    return OWNER_ID == 0 or event.sender_id == OWNER_ID


async def run_queue(event):
    global stop_requested
    if run_lock.locked():
        await event.reply("The queue is already running.")
        return

    async with run_lock:
        stop_requested = False
        conn = database()
        try:
            links = load_links()
            await event.reply(f"Started. {len(links)} unique links loaded.")
            for index, url in enumerate(links, 1):
                if stop_requested:
                    break
                await pause_event.wait()
                row = conn.execute(
                    "SELECT status FROM posts WHERE url=?", (url,)
                ).fetchone()
                if row and row[0] == "done":
                    continue

                try:
                    await process_one(conn, index, len(links), url)
                except FloodWaitError as exc:
                    log.warning(
                        "Telegram FloodWait: wait %s seconds, then retry once.",
                        exc.seconds,
                    )
                    await asyncio.sleep(exc.seconds + 1)
                    try:
                        await process_one(conn, index, len(links), url)
                    except Exception as retry_exc:
                        log.exception("Retry failed for %s", url)
                        conn.execute(
                            "UPDATE posts SET status='failed',last_error=? WHERE url=?",
                            (str(retry_exc)[:1000], url),
                        )
                        conn.commit()
                except Exception as exc:
                    log.exception("Post failed: %s", url)
                    conn.execute(
                        "UPDATE posts SET status='failed',last_error=? WHERE url=?",
                        (str(exc)[:1000], url),
                    )
                    conn.commit()

                shutil.rmtree(
                    DOWNLOAD_DIR / hashlib.sha1(url.encode()).hexdigest()[:16],
                    ignore_errors=True,
                )
                await send_database_backup(conn, index, len(links), url)
                if index % 25 == 0:
                    await event.reply(
                        f"Progress: {index}/{len(links)} links scanned."
                    )

            done = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE status='done'"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE status='failed'"
            ).fetchone()[0]
            await event.reply(
                f"Queue finished/stopped.\nCompleted: {done}\nFailed: {failed}"
            )
        finally:
            conn.close()


@app.on(events.NewMessage(pattern=r"^/start(?:@\w+)?$"))
async def start_handler(event):
    if allowed(event):
        await run_queue(event)


@app.on(events.NewMessage(pattern=r"^/restore(?:@\w+)?$"))
async def restore_handler(event):
    if allowed(event) and event.is_private:
        await restore_database_from_reply(event)


@app.on(events.NewMessage(pattern=r"^/status(?:@\w+)?$"))
async def status_handler(event):
    if not allowed(event):
        return
    conn = database()
    counts = {
        name: conn.execute(
            "SELECT COUNT(*) FROM posts WHERE status=?", (name,)
        ).fetchone()[0]
        for name in ("done", "failed", "processing", "pending")
    }
    conn.close()
    await event.reply("\n".join(f"{key}: {value}" for key, value in counts.items()))


@app.on(events.NewMessage(pattern=r"^/pause(?:@\w+)?$"))
async def pause_handler(event):
    if allowed(event):
        pause_event.clear()
        await event.reply("Paused after the current transfer.")


@app.on(events.NewMessage(pattern=r"^/resume(?:@\w+)?$"))
async def resume_handler(event):
    if allowed(event):
        pause_event.set()
        await event.reply("Resumed.")


@app.on(events.NewMessage(pattern=r"^/stop(?:@\w+)?$"))
async def stop_handler(event):
    global stop_requested
    if allowed(event):
        stop_requested = True
        pause_event.set()
        await event.reply("Stop requested; the current post will finish first.")


async def main():
    # Always start from an official HTTPS hostname. ConnectionHttp enables TLS
    # because the selected port is 443; TelegramHttpsConnection also enforces
    # the hostname mapping when Telegram changes the data center.
    dc_id = app.session.dc_id if app.session.dc_id in DC_HOSTS else 2
    app.session.set_dc(dc_id, DC_HOSTS[dc_id], 443)
    app.session.save()

    log.info("Starting Telegram bot through Telethon MTProto over HTTPS.")
    await app.start(bot_token=BOT_TOKEN)
    me = await app.get_me()
    log.info(
        "Authenticated as @%s (id=%s). HTTPS transport is enabled for Telegram DCs 1-5.",
        me.username or "bot",
        me.id,
    )
    log.info("Bot ready. Send /start to process links.txt; /status to inspect progress.")
    try:
        await app.run_until_disconnected()
    finally:
        await app.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

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
from wzgram import Client, filters
from wzgram.types import InputMediaPhoto, InputMediaVideo
from wzgram.errors import FloodWait

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
load_dotenv(ROOT / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
BACKUP_CHAT_ID = int(os.getenv("BACKUP_CHAT_ID", str(OWNER_ID)) or 0)
INPUT_FILE = Path(os.getenv("INPUT_FILE", "links.txt"))
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "downloads"))
DATABASE_FILE = Path(os.getenv("DATABASE_FILE", "progress.sqlite3"))
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "2"))

if not BOT_TOKEN or not API_ID or not API_HASH or not TARGET_CHAT_ID:
    raise SystemExit("Fill BOT_TOKEN, API_ID, API_HASH and TARGET_CHAT_ID in .env.")

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("downloader.log", encoding="utf-8")],
)
log = logging.getLogger("instagram-bulk")
app = Client("instagram_bulk_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, sleep_threshold=60, max_concurrent_transmissions=1)
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
    """Create a consistent SQLite snapshot and deliver it to the owner's private chat."""
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
        await app.send_document(
            BACKUP_CHAT_ID,
            str(backup_path),
            caption=f"Database checkpoint after link {index}/{total}.\nCompleted item: {url}",
        )
        log.info("Database checkpoint delivered to Telegram private chat %s.", BACKUP_CHAT_ID)
    except Exception:
        log.exception("Could not send database checkpoint to Telegram.")
    finally:
        try:
            backup_path.unlink(missing_ok=True)
        except OSError:
            log.warning("Could not remove temporary database backup %s", backup_path)


async def restore_database_from_reply(message):
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.reply_text("Reply to a database backup document with /restore.")
        return
    if run_lock.locked():
        await message.reply_text("Stop the queue before restoring the database.")
        return
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    restore_path = DOWNLOAD_DIR / "restore-candidate.sqlite3"
    try:
        downloaded = await app.download_media(message.reply_to_message, file_name=str(restore_path))
        if not downloaded:
            raise RuntimeError("Telegram did not return a downloaded file.")
        candidate = Path(downloaded)
        check = sqlite3.connect(str(candidate))
        try:
            integrity = check.execute("PRAGMA integrity_check").fetchone()
            tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not integrity or integrity[0] != "ok" or not {"posts", "chunks"}.issubset(tables):
                raise RuntimeError("The file is not a valid downloader database backup.")
        finally:
            check.close()
        for suffix in ("-wal", "-shm"):
            Path(str(DATABASE_FILE) + suffix).unlink(missing_ok=True)
        os.replace(candidate, DATABASE_FILE)
        await message.reply_text("Database restored successfully. You can now send /start.")
    except Exception as exc:
        log.exception("Database restore failed.")
        await message.reply_text(f"Restore failed: {exc}")
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
    return (codec and codec != "none") or ext in {"mp4", "m4v", "mov", "webm", "mkv"}


def best_format(entry):
    formats = [f for f in entry.get("formats", []) if f.get("url")]
    if not formats:
        return entry
    videos = [f for f in formats if is_video(f)]
    candidates = videos or formats
    if not videos:
        return max(candidates, key=lambda f: (f.get("width") or 0) * (f.get("height") or 0))
    for height in (360, 480, 720):
        exact = [f for f in candidates if f.get("height") == height]
        if exact:
            return max(exact, key=lambda f: f.get("tbr") or 0)
    for limit in (360, 480, 720):
        lower = [f for f in candidates if (f.get("height") or 0) <= limit]
        if lower:
            return max(lower, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
    return max(candidates, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))


def make_items(info):
    result = []
    for entry in flatten(info):
        fmt = best_format(entry)
        url = fmt.get("url") or entry.get("url")
        if not url:
            continue
        media_id = str(entry.get("id") or hashlib.sha1(url.encode()).hexdigest()[:12])
        size = fmt.get("filesize") or fmt.get("filesize_approx") or entry.get("filesize") or entry.get("filesize_approx")
        result.append({
            "id": media_id, "url": url,
            "is_video": is_video(fmt) or is_video(entry),
            "size": int(size) if size else None,
        })
    return result


def split(items, limit=10):
    for start in range(0, len(items), limit):
        yield start // limit, items[start:start + limit]


def signature(items):
    return hashlib.sha256("|".join(x["id"] for x in items).encode()).hexdigest()


def chunk_is_sent(conn, url, number, sig):
    row = conn.execute("SELECT signature,status FROM chunks WHERE url=? AND chunk_no=?", (url, number)).fetchone()
    return bool(row and row[0] == sig and row[1] == "sent")


def set_chunk(conn, url, number, sig):
    conn.execute("""INSERT INTO chunks(url,chunk_no,signature,status) VALUES(?,?,?,'sent')
        ON CONFLICT(url,chunk_no) DO UPDATE SET signature=excluded.signature,status='sent',
        updated_at=CURRENT_TIMESTAMP""", (url, number, sig))
    conn.commit()


def caption_for(index, url, description):
    return f"{index}\n{url}\n\n{description or ''}".strip()[:1024]


async def download_post(url, key):
    folder = DOWNLOAD_DIR / key
    folder.mkdir(parents=True, exist_ok=True)
    options = {
        "outtmpl": str(folder / "%(id)s.%(ext)s"),
        "format": "best[height=360]/best[height=480]/best[height=720]/best[height<=360]/best[height<=480]/best[height<=720]/best",
        "noplaylist": False, "ignoreerrors": False, "quiet": True, "no_warnings": True,
        "retries": 8, "fragment_retries": 8, "socket_timeout": 30, "merge_output_format": "mp4",
    }
    def work():
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise RuntimeError("yt-dlp returned no media information.")
            entries = flatten(info)
            files = []
            for entry in entries:
                paths = [Path(x["filepath"]) for x in entry.get("requested_downloads", []) if x.get("filepath")]
                if entry.get("filepath"):
                    paths.append(Path(entry["filepath"]))
                paths.extend(folder.glob(f"{entry.get('id', '')}.*"))
                found = next((p for p in paths if p.is_file()), None)
                if found and found not in files:
                    files.append(found)
            if len(files) != len(entries):
                remaining = [p for p in sorted(folder.iterdir()) if p.is_file() and p not in files and not p.name.endswith((".part", ".ytdl"))]
                files.extend(remaining[:len(entries) - len(files)])
            return files
    paths = await asyncio.to_thread(work)
    if not paths:
        raise RuntimeError("No downloaded files found.")
    return paths


async def send_local(items, paths, caption):
    if len(items) != len(paths):
        raise RuntimeError(f"Media/file count mismatch: {len(items)} vs {len(paths)}")
    if len(paths) == 1:
        progress = make_upload_progress(paths[0].name)
        if items[0]["is_video"]:
            return await app.send_video(
                TARGET_CHAT_ID, str(paths[0]), caption=caption,
                supports_streaming=True, progress=progress
            )
        return await app.send_photo(
            TARGET_CHAT_ID, str(paths[0]), caption=caption, progress=progress
        )
    group = []
    for i, (item, path) in enumerate(zip(items, paths)):
        cap = caption if i == 0 else None
        if item["is_video"]:
            group.append(InputMediaVideo(str(path), caption=cap, supports_streaming=True))
        else:
            group.append(InputMediaPhoto(str(path), caption=cap))
    return await app.send_media_group(
        TARGET_CHAT_ID, group, progress=make_upload_progress("album")
    )


def make_upload_progress(label):
    last = [-5]
    async def progress(current, total):
        if not total:
            return
        percent = int(current * 100 / total)
        if percent >= last[0] + 5 or current >= total:
            last[0] = percent
            log.info("Wzgram upload %s: %d%% (%.1f/%.1f MB).",
                     label, percent, current / 1048576, total / 1048576)
    return progress


async def process_one(conn, index, total, url):
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    conn.execute("""INSERT INTO posts(url,status,attempts) VALUES(?,'processing',1)
        ON CONFLICT(url) DO UPDATE SET status='processing',attempts=attempts+1,
        updated_at=CURRENT_TIMESTAMP""", (url,))
    conn.commit()
    log.info("[%d/%d] %s", index, total, url)
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": False, "retries": 5}
    info = await asyncio.to_thread(lambda: yt_dlp.YoutubeDL(opts).extract_info(url, download=False))
    if not info:
        raise RuntimeError("Could not extract post.")
    entries = flatten(info)
    items = make_items(info)
    if not items:
        raise RuntimeError("No photo/video media found.")
    caption = caption_for(index, url, info.get("description") or (entries[0].get("description") or ""))
    work = [(n, part, signature(part)) for n, part in split(items) if not chunk_is_sent(conn, url, n, signature(part))]
    if not work:
        conn.execute("UPDATE posts SET status='done' WHERE url=?", (url,))
        conn.commit()
        return

    local_files = await download_post(url, key)
    try:
        for number, part, sig in work:
            await pause_event.wait()
            chunk_caption = caption if number == 0 else ""
            start = number * 10
            selected = local_files[start:start + len(part)]
            await send_local(part, selected, chunk_caption)
            log.info("Chunk %d uploaded through Wzgram MTProto.", number + 1)
            set_chunk(conn, url, number, sig)
            await asyncio.sleep(REQUEST_DELAY)
    finally:
        for path in local_files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                log.warning("Could not delete temporary file %s", path)
        shutil.rmtree(DOWNLOAD_DIR / key, ignore_errors=True)

    conn.execute("UPDATE posts SET status='done',last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE url=?", (url,))
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


def allowed(message):
    return OWNER_ID == 0 or (message.from_user and message.from_user.id == OWNER_ID)


async def run_queue(message):
    global stop_requested
    if run_lock.locked():
        await message.reply_text("The queue is already running.")
        return
    async with run_lock:
        stop_requested = False
        conn = database()
        try:
            links = load_links()
            await message.reply_text(f"Started. {len(links)} unique links loaded.")
            for index, url in enumerate(links, 1):
                if stop_requested:
                    break
                await pause_event.wait()
                row = conn.execute("SELECT status FROM posts WHERE url=?", (url,)).fetchone()
                if row and row[0] == "done":
                    continue
                try:
                    await process_one(conn, index, len(links), url)
                except FloodWait as exc:
                    log.warning("FloodWait: wait %s seconds, then retry once.", exc.value)
                    await asyncio.sleep(exc.value + 1)
                    try:
                        await process_one(conn, index, len(links), url)
                    except Exception as retry_exc:
                        log.exception("Retry failed for %s", url)
                        conn.execute("UPDATE posts SET status='failed',last_error=? WHERE url=?", (str(retry_exc)[:1000], url))
                        conn.commit()
                except Exception as exc:
                    log.exception("Post failed: %s", url)
                    conn.execute("UPDATE posts SET status='failed',last_error=? WHERE url=?", (str(exc)[:1000], url))
                    conn.commit()
                shutil.rmtree(DOWNLOAD_DIR / hashlib.sha1(url.encode()).hexdigest()[:16], ignore_errors=True)
                await send_database_backup(conn, index, len(links), url)
                if index % 25 == 0:
                    await message.reply_text(f"Progress: {index}/{len(links)} links scanned.")
            done = conn.execute("SELECT COUNT(*) FROM posts WHERE status='done'").fetchone()[0]
            failed = conn.execute("SELECT COUNT(*) FROM posts WHERE status='failed'").fetchone()[0]
            await message.reply_text(f"Queue finished/stopped.\nCompleted: {done}\nFailed: {failed}")
        finally:
            conn.close()


@app.on_message(filters.command("start"))
async def start_handler(client, message):
    if allowed(message):
        await run_queue(message)


@app.on_message(filters.command("restore") & filters.private)
async def restore_handler(client, message):
    if not allowed(message) or not message.from_user or message.from_user.id != BACKUP_CHAT_ID:
        return
    await restore_database_from_reply(message)


@app.on_message(filters.command("status"))
async def status_handler(client, message):
    if not allowed(message):
        return
    conn = database()
    counts = {name: conn.execute("SELECT COUNT(*) FROM posts WHERE status=?", (name,)).fetchone()[0]
              for name in ("done", "failed", "processing", "pending")}
    conn.close()
    await message.reply_text("\n".join(f"{k}: {v}" for k, v in counts.items()))


@app.on_message(filters.command("pause"))
async def pause_handler(client, message):
    if allowed(message):
        pause_event.clear()
        await message.reply_text("Paused after the current transfer.")


@app.on_message(filters.command("resume"))
async def resume_handler(client, message):
    if allowed(message):
        pause_event.set()
        await message.reply_text("Resumed.")


@app.on_message(filters.command("stop"))
async def stop_handler(client, message):
    global stop_requested
    if allowed(message):
        stop_requested = True
        pause_event.set()
        await message.reply_text("Stop requested; the current post will finish first.")


if __name__ == "__main__":
    log.info("Bot ready. Send /start to process links.txt; /status to inspect progress.")
    app.run()
